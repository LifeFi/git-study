"""InlineCodeView: file tree + code view with embedded quiz blocks."""

from __future__ import annotations

from difflib import SequenceMatcher

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click, MouseScrollDown, MouseScrollUp
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Label, Static, Tree

from ...domain.code_context import (
    detect_code_language,
    get_commit_parent_sha,
    get_file_content_at_commit_or_empty,
    get_range_changed_file_paths,
    list_commit_tree_files,
)
from ...domain.repo_context import get_repo
from ...tui.code_browser import highlight_code_lines  # syntax highlighting per line
from ...tui.inline_quiz import find_anchor_line, resolve_anchor_line, QUESTION_TYPE_KO
from ...types import InlineQuizGrade, InlineQuizQuestion

_CONTEXT_LINES = 3  # lines of context around each hunk (like git diff -U3)


# ---------------------------------------------------------------------------
# Claude Code-style diff renderer
# ---------------------------------------------------------------------------

def build_claude_diff(
    path: str,
    language: str,
    base_content: str,
    target_content: str,
) -> Text:
    """Render a diff in Claude Code's visual style.

    Layout per line:
      old_lineno  new_lineno  marker  content
    +/- lines have full-row background (numbers through trailing whitespace).
    Only changed hunks + CONTEXT_LINES surrounding lines are shown.
    """
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()

    base_hl = highlight_code_lines(base_content, language)
    target_hl = highlight_code_lines(target_content, language)

    matcher = SequenceMatcher(a=base_lines, b=target_lines, autojunk=False)
    opcodes = matcher.get_opcodes()
    hunks = _group_into_hunks(opcodes, len(base_lines), len(target_lines))

    # --- Pass 1: collect rows as (marker, Text) to find max plain width ---
    rows: list[tuple[str, str, Text]] = []  # (kind, hunk_header_or_empty, line_text)

    for hunk in hunks:
        old_start, old_count, new_start, new_count, ops = hunk
        hunk_hdr = Text(no_wrap=True)
        hunk_hdr.append(
            f"@@ -{old_start + 1},{old_count} +{new_start + 1},{new_count} @@\n",
            style="bold cyan",
        )
        rows.append(("hdr", "hdr", hunk_hdr))
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                for k in range(j2 - j1):
                    rows.append((" ", "", _build_line(i1 + k + 1, j1 + k + 1, " ", base_hl[i1 + k])))
            elif tag == "insert":
                for k in range(j2 - j1):
                    rows.append(("+", "", _build_line(None, j1 + k + 1, "+", target_hl[j1 + k])))
            elif tag == "delete":
                for k in range(i2 - i1):
                    rows.append(("-", "", _build_line(i1 + k + 1, None, "-", base_hl[i1 + k])))
            elif tag == "replace":
                for k in range(i2 - i1):
                    rows.append(("-", "", _build_line(i1 + k + 1, None, "-", base_hl[i1 + k])))
                for k in range(j2 - j1):
                    rows.append(("+", "", _build_line(None, j1 + k + 1, "+", target_hl[j1 + k])))

    # --- Pass 2: find max width, pad +/- rows, assemble final Text ---
    max_width = max((len(t.plain.rstrip("\n")) for _, _, t in rows), default=0)
    max_width = max(max_width, 40)  # minimum visual width

    rendered = Text(no_wrap=True)
    for marker, _, line_text in rows:
        if marker in ("+", "-"):
            rendered.append_text(_pad_row(line_text, max_width, marker))
        elif marker == "hdr":
            rendered.append_text(line_text)  # hunk header already has \n
        else:
            rendered.append_text(line_text)
            rendered.append("\n")

    if not rendered.plain.strip():
        rendered.append("변경 사항이 없습니다.\n", style="dim")

    return rendered


def _build_line(
    old_no: int | None,
    new_no: int | None,
    marker: str,
    content: Text,
) -> Text:
    """Build a single diff row Text (without padding)."""
    # Show only one line number: old_no for deleted lines, new_no otherwise.
    display_no = old_no if marker == "-" else new_no
    num_str = f"{display_no:4}" if display_no is not None else "    "

    if marker == "+":
        row_bg = "on color(22)"
        marker_style = f"bold bright_green {row_bg}"
        num_style = row_bg
        content_style = row_bg
    elif marker == "-":
        row_bg = "on color(52)"
        marker_style = f"bold bright_red {row_bg}"
        num_style = row_bg
        content_style = row_bg
    else:
        row_bg = None
        marker_style = "dim"
        num_style = "dim"
        content_style = None

    t = Text(no_wrap=True)
    t.append(f"{num_str} ", style=num_style)
    t.append(f"{marker} ", style=marker_style)
    if content_style:
        tinted = content.copy()
        tinted.stylize(content_style)
        t.append_text(tinted)
    else:
        t.append_text(content)
    return t


def _pad_row(line_text: Text, width: int, marker: str) -> Text:
    """Pad a +/- row with background-colored spaces to `width`, then newline."""
    row_bg = "on color(22)" if marker == "+" else "on color(52)"
    plain_len = len(line_text.plain)  # excludes any trailing \n already
    pad = max(0, width - plain_len)
    result = line_text.copy()
    if pad:
        result.append(" " * pad, style=row_bg)
    result.append("\n")
    return result


def _append_line(
    rendered: Text,
    old_no: int | None,
    new_no: int | None,
    marker: str,
    content: Text,
) -> None:
    """Append one diff line into `rendered` (used by build_claude_diff_lines)."""
    rendered.append_text(_build_line(old_no, new_no, marker, content))
    rendered.append("\n")


def _group_into_hunks(
    opcodes: list[tuple],
    n_base: int,
    n_target: int,
) -> list[tuple]:
    """Group opcodes into unified-diff hunks with CONTEXT_LINES of context."""
    ctx = _CONTEXT_LINES
    hunks = []
    group: list[tuple] = []
    hunk_old_start = 0
    hunk_new_start = 0

    def flush():
        if not group:
            return
        old_start = hunk_old_start
        new_start = hunk_new_start
        old_count = sum(
            (i2 - i1) for tag, i1, i2, j1, j2 in group if tag in ("equal", "delete", "replace")
        )
        new_count = sum(
            (j2 - j1) for tag, i1, i2, j1, j2 in group if tag in ("equal", "insert", "replace")
        )
        hunks.append((old_start, old_count, new_start, new_count, list(group)))
        group.clear()

    for i, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            # Leading context
            if group:
                # Trailing context of previous hunk
                trail_start = i1
                trail_end = min(i1 + ctx, i2)
                if trail_end > trail_start:
                    group.append(("equal", trail_start, trail_end, j1, j1 + (trail_end - trail_start)))
                # Check if there's a next non-equal op
                has_next = any(t != "equal" for t, *_ in opcodes[i + 1:])
                if has_next:
                    # More hunks follow — flush current and start next context
                    flush()
                    lead_start = max(i2 - ctx, trail_end)
                    lead_end = i2
                    if lead_end > lead_start:
                        hunk_old_start = lead_start
                        hunk_new_start = j2 - (lead_end - lead_start)
                        group.append(("equal", lead_start, lead_end, j2 - (lead_end - lead_start), j2))
                else:
                    flush()
            else:
                # Leading context before first change
                lead_start = max(i2 - ctx, i1)
                lead_end = i2
                if lead_end > lead_start:
                    hunk_old_start = lead_start
                    hunk_new_start = j2 - (lead_end - lead_start)
                    group.append(("equal", lead_start, lead_end, j2 - (lead_end - lead_start), j2))
        else:
            if not group:
                # Set hunk start to include leading context
                lead_start = max(i1 - ctx, 0)
                lead_end = i1
                if lead_end > lead_start:
                    hunk_old_start = lead_start
                    hunk_new_start = j1 - (lead_end - lead_start)
                    group.append(("equal", lead_start, lead_end, j1 - (lead_end - lead_start), j1))
                else:
                    hunk_old_start = i1
                    hunk_new_start = j1
            group.append((tag, i1, i2, j1, j2))

    flush()
    return hunks


# ---------------------------------------------------------------------------
# Helpers for quiz-mode diff line slicing
# ---------------------------------------------------------------------------

def build_claude_diff_lines(
    language: str,
    base_content: str,
    target_content: str,
) -> list[Text]:
    """Return per-line Rich Text items with diff markers, indexed by *target* line.

    Index i corresponds to target line i+1 (1-based).
    Deleted base lines are inserted before the first matching target line.
    Returns one Text per visible output row (same ordering as render_current_code_text).
    """
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    base_hl = highlight_code_lines(base_content, language)
    target_hl = highlight_code_lines(target_content, language)

    matcher = SequenceMatcher(a=base_lines, b=target_lines, autojunk=False)
    rows: list[tuple[str, Text]] = []  # (marker, line_text)
    old_no = 1
    new_no = 1

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                rows.append((" ", _build_line(old_no, new_no, " ", base_hl[i1 + k])))
                old_no += 1
                new_no += 1
        elif tag == "insert":
            for k in range(j2 - j1):
                rows.append(("+", _build_line(None, new_no, "+", target_hl[j1 + k])))
                new_no += 1
        elif tag == "delete":
            for k in range(i2 - i1):
                rows.append(("-", _build_line(old_no, None, "-", base_hl[i1 + k])))
                old_no += 1
        elif tag == "replace":
            for k in range(i2 - i1):
                rows.append(("-", _build_line(old_no, None, "-", base_hl[i1 + k])))
                old_no += 1
            for k in range(j2 - j1):
                rows.append(("+", _build_line(None, new_no, "+", target_hl[j1 + k])))
                new_no += 1

    # Pad +/- rows to max width for full-row background
    max_width = max((len(t.plain) for _, t in rows), default=40)
    max_width = max(max_width, 40)

    result: list[Text] = []
    for marker, line_text in rows:
        if marker in ("+", "-"):
            result.append(_pad_row(line_text, max_width, marker))
        else:
            t = line_text.copy()
            t.append("\n")
            result.append(t)

    return result


def _build_target_to_render_map(base_content: str, target_content: str) -> dict[int, int]:
    """Return mapping: target line number (1-based) -> render_lines index (0-based).

    Deleted base lines occupy render_lines slots but have no target line number,
    so they are skipped in the mapping.
    """
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    matcher = SequenceMatcher(a=base_lines, b=target_lines, autojunk=False)
    result: dict[int, int] = {}
    render_idx = 0
    target_line = 1  # 1-based

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for _ in range(j2 - j1):
                result[target_line] = render_idx
                target_line += 1
                render_idx += 1
        elif tag == "insert":
            for _ in range(j2 - j1):
                result[target_line] = render_idx
                target_line += 1
                render_idx += 1
        elif tag == "delete":
            render_idx += i2 - i1  # deleted rows consume render slots, no target line
        elif tag == "replace":
            render_idx += i2 - i1  # deleted rows first
            for _ in range(j2 - j1):
                result[target_line] = render_idx
                target_line += 1
                render_idx += 1

    return result


def _plain_line(line_no: int, content: Text) -> Text:
    """번호 붙은 일반 코드 줄 (diff 없음)."""
    t = _build_line(line_no, line_no, " ", content)
    t.append("\n")
    return t


def _join_lines(lines: list[Text]) -> Text:
    """Concatenate a list of Text lines (each already ends with \\n)."""
    result = Text(no_wrap=True)
    for line in lines:
        result.append_text(line)
    return result


def _collect_diff_markers(
    base_content: str, target_content: str
) -> tuple[int, list[tuple[int, str]]]:
    """Return (total_rows, markers) matching the row layout of build_claude_diff_lines."""
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    matcher = SequenceMatcher(a=base_lines, b=target_lines, autojunk=False)
    markers: list[tuple[int, str]] = []
    row = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            row += j2 - j1
        elif tag == "insert":
            for _ in range(j2 - j1):
                markers.append((row, "add"))
                row += 1
        elif tag == "delete":
            for _ in range(i2 - i1):
                markers.append((row, "delete"))
                row += 1
        elif tag == "replace":
            for _ in range(i2 - i1):
                markers.append((row, "delete"))
                row += 1
            for _ in range(j2 - j1):
                markers.append((row, "add"))
                row += 1
    return row, markers


# ---------------------------------------------------------------------------
# OverviewRuler: thin strip showing diff/quiz positions proportionally
# ---------------------------------------------------------------------------

class OverviewRuler(Widget):
    """Vertical strip next to the scrollbar showing +/- and quiz markers."""

    DEFAULT_CSS = """
    OverviewRuler {
        width: 2;
        height: 1fr;
        background: $surface-darken-1;
    }
    """

    class ScrollTo(Message):
        def __init__(self, ratio: float) -> None:
            super().__init__()
            self.ratio = ratio

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._total_rows: int = 0
        self._markers: list[tuple[int, str]] = []

    def set_markers(self, total_rows: int, markers: list[tuple[int, str]]) -> None:
        self._total_rows = total_rows
        self._markers = markers
        self.refresh()

    def render_line(self, y: int) -> Strip:
        height = self.size.height
        if height == 0 or self._total_rows == 0:
            return Strip([Segment("  ")])

        line_start = int(y / height * self._total_rows)
        line_end = max(int((y + 1) / height * self._total_rows), line_start + 1)

        kinds = {kind for row, kind in self._markers if line_start <= row < line_end}

        if "quiz" in kinds:
            style = Style.parse("on cyan")
        elif "add" in kinds and "delete" in kinds:
            style = Style.parse("on yellow")
        elif "add" in kinds:
            style = Style.parse("on color(22)")
        elif "delete" in kinds:
            style = Style.parse("on color(52)")
        else:
            return Strip([Segment("  ")])

        return Strip([Segment("  ", style)])

    def on_click(self, event: Click) -> None:
        height = self.size.height
        if height == 0:
            return
        self.post_message(self.ScrollTo(event.y / height))


# ---------------------------------------------------------------------------
# InlineQuizBlock: a single quiz question embedded between code segments
# ---------------------------------------------------------------------------

class InlineQuizBlock(Widget):
    """A quiz question block rendered inline between code lines."""

    DEFAULT_CSS = """
    InlineQuizBlock {
        height: auto;
        margin: 0 1;
        padding: 1 2;
        border: round $panel;
        background: $boost;
    }

    InlineQuizBlock.-active {
        border: round cyan;
    }

    InlineQuizBlock.-answered {
        border: round green;
    }

    InlineQuizBlock.-graded {
        border: round yellow;
    }

    InlineQuizBlock .iqb-header {
        height: auto;
        color: $accent;
        text-style: bold;
    }

    InlineQuizBlock .iqb-body {
        height: auto;
        margin-top: 1;
    }

    InlineQuizBlock .iqb-status {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    InlineQuizBlock .iqb-score {
        height: auto;
        margin-top: 1;
        text-style: bold;
        color: yellow;
        display: none;
    }

    InlineQuizBlock .iqb-my-answer {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        display: none;
    }

    InlineQuizBlock .iqb-model-answer {
        height: auto;
        margin-top: 1;
        color: cyan;
        display: none;
    }

    InlineQuizBlock .iqb-feedback {
        height: auto;
        margin-top: 1;
        color: $text;
        display: none;
    }
    """

    class Activated(Message):
        """Fired when user clicks on a quiz block."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(
        self,
        question: InlineQuizQuestion,
        index: int,
        *,
        is_active: bool = False,
        answer: str = "",
        grade: InlineQuizGrade | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._question = question
        self._index = index
        self._is_active = is_active
        self._answer = answer
        self._grade = grade

    @property
    def index(self) -> int:
        return self._index

    def compose(self) -> ComposeResult:
        q = self._question
        qtype_ko = QUESTION_TYPE_KO.get(q.get("question_type", ""), q.get("question_type", ""))
        header_text = f"[Q{self._index + 1}] {qtype_ko}  ·  {q.get('file_path', '')}:{self._anchor_line_display()}"
        if self._is_active:
            header_text += "  ◀ 현재"

        yield Static(header_text, classes="iqb-header")
        yield Static(q.get("question", ""), classes="iqb-body")
        yield Static(self._build_status_text(), classes="iqb-status")
        yield Static("", classes="iqb-score")
        yield Static("", classes="iqb-my-answer")
        yield Static("", classes="iqb-model-answer")
        yield Static("", classes="iqb-feedback")

    def on_mount(self) -> None:
        self._apply_classes()
        self._sync_grade_widgets()

    def _anchor_line_display(self) -> str:
        return "?"

    def _build_status_text(self) -> str:
        if self._answer:
            preview = self._answer[:60] + ("..." if len(self._answer) > 60 else "")
            return f"✔ 답변: {preview}"
        return "● 답변 대기 중"

    def _sync_grade_widgets(self) -> None:
        """채점 완료 시 상세 위젯 표시, 아닌 경우 상태 텍스트만 표시."""
        try:
            is_graded = self._grade is not None
            self.query_one(".iqb-status", Static).display = not is_graded
            self.query_one(".iqb-score", Static).display = is_graded
            self.query_one(".iqb-my-answer", Static).display = is_graded
            self.query_one(".iqb-model-answer", Static).display = is_graded
            self.query_one(".iqb-feedback", Static).display = is_graded
            if is_graded:
                score = self._grade.get("score", 0)
                feedback = self._grade.get("feedback", "")
                expected = self._question.get("expected_answer", "")
                self.query_one(".iqb-score", Static).update(f"★  {score} / 100")
                self.query_one(".iqb-my-answer", Static).update(
                    f"내 답변:\n{self._answer or '(없음)'}"
                )
                self.query_one(".iqb-model-answer", Static).update(
                    f"모범 답안:\n{expected}"
                )
                self.query_one(".iqb-feedback", Static).update(
                    f"채점 이유:\n{feedback}"
                )
        except Exception:
            pass

    def _apply_classes(self) -> None:
        self.remove_class("-active", "-answered", "-graded")
        if self._grade is not None:
            self.add_class("-graded")
        elif self._answer:
            self.add_class("-answered")
        if self._is_active:
            self.add_class("-active")

    def update_state(
        self,
        *,
        is_active: bool | None = None,
        answer: str | None = None,
        grade: InlineQuizGrade | None = None,
    ) -> None:
        if is_active is not None:
            self._is_active = is_active
        if answer is not None:
            self._answer = answer
        if grade is not None:
            self._grade = grade
        try:
            q = self._question
            qtype_ko = QUESTION_TYPE_KO.get(q.get("question_type", ""), q.get("question_type", ""))
            header_text = f"[Q{self._index + 1}] {qtype_ko}  ·  {q.get('file_path', '')}:{self._anchor_line_display()}"
            if self._is_active:
                header_text += "  ◀ 현재"
            self.query_one(".iqb-header", Static).update(header_text)
            self.query_one(".iqb-status", Static).update(self._build_status_text())
        except Exception:
            pass
        self._apply_classes()
        self._sync_grade_widgets()

    @on(Click)
    def handle_click(self, event: Click) -> None:
        self.post_message(self.Activated(self._index))


# ---------------------------------------------------------------------------
# FileTree: Tree subclass that fixes shift+scroll horizontal direction
# ---------------------------------------------------------------------------

class FileTree(Tree):
    def _on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        if event.shift:
            self.scroll_right(animate=False)
            event.stop()
        else:
            super()._on_mouse_scroll_down(event)

    def _on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if event.shift:
            self.scroll_left(animate=False)
            event.stop()
        else:
            super()._on_mouse_scroll_up(event)


# ---------------------------------------------------------------------------
# InlineCodeView: left file tree + right code panel with quiz blocks
# ---------------------------------------------------------------------------

class InlineCodeView(Widget):
    """File tree + code view with inline quiz blocks."""

    DEFAULT_CSS = """
    InlineCodeView {
        width: 1fr;
        height: 1fr;
    }

    InlineCodeView #icv-layout {
        width: 1fr;
        height: 1fr;
    }

    InlineCodeView #file-tree-pane {
        width: 28;
        min-width: 20;
        border: round $panel;
        padding: 0 1;
    }

    InlineCodeView #file-tree-title {
        color: $accent;
        text-style: bold;
    }

    InlineCodeView #file-tree {
        height: 1fr;
        margin-top: 1;
    }

    InlineCodeView #code-view-pane {
        width: 1fr;
        border: round $panel;
        padding: 0 1;
    }

    InlineCodeView #file-tree-pane:focus-within {
        border: round $accent;
    }

    InlineCodeView #code-view-pane:focus-within {
        border: round $accent;
    }

    InlineCodeView #cv-file-label {
        height: auto;
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    InlineCodeView #code-area {
        width: 1fr;
        height: 1fr;
    }

    InlineCodeView #code-scroll {
        width: 1fr;
        height: 1fr;
        background: $boost;
    }

    InlineCodeView #overview-ruler {
        width: 2;
        height: 1fr;
    }

    InlineCodeView .code-segment {
        width: auto;
        height: auto;
        padding: 0 1;
    }
    """

    class QuestionActivated(Message):
        """Fired when a quiz block is clicked or navigated to."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Repository state
        self.repo_source: str = "local"
        self.github_repo_url: str | None = None
        self.oldest_commit_sha: str = ""
        self.newest_commit_sha: str = ""
        self.repo = None
        self.base_commit_sha: str | None = None
        self.changed_paths: set[str] = set()
        self.file_paths: list[str] = []
        self.current_file_path: str | None = None

        # Quiz state
        self._questions: list[InlineQuizQuestion] = []
        self._answers: dict[str, str] = {}
        self._grades: list[InlineQuizGrade] = []
        self._known_files: dict[str, str] = {}
        self._current_q_index: int = 0
        self._anchor_cache: dict[str, int | None] = {}  # "q_id" -> line number
        self._render_id: int = 0  # increment on each render to ensure unique widget IDs

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="icv-layout"):
            with Vertical(id="file-tree-pane"):
                yield Label("Files", id="file-tree-title")
                yield FileTree("Repository", id="file-tree")
            with Vertical(id="code-view-pane"):
                yield Label("", id="cv-file-label")
                with Horizontal(id="code-area"):
                    with VerticalScroll(id="code-scroll"):
                        yield Static("코드 브라우저 준비 중...", classes="code-segment")
                    yield OverviewRuler(id="overview-ruler")

    def on_mount(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.show_root = False


    # ------------------------------------------------------------------
    # Public API: repository browsing
    # ------------------------------------------------------------------

    def show_range(
        self,
        *,
        repo_source: str,
        github_repo_url: str | None,
        oldest_commit_sha: str,
        newest_commit_sha: str,
        local_repo_root=None,
    ) -> None:
        """Load a commit range and populate the file tree."""
        self.repo_source = repo_source
        self.github_repo_url = github_repo_url
        self.oldest_commit_sha = oldest_commit_sha
        self.newest_commit_sha = newest_commit_sha
        self.local_repo_root = local_repo_root
        self.repo = get_repo(
            repo_source=self.repo_source,
            github_repo_url=self.github_repo_url,
            refresh_remote=False,
            local_repo_root=local_repo_root,
        )
        self.base_commit_sha = get_commit_parent_sha(self.repo, self.oldest_commit_sha)
        base_commit = self.repo.commit(self.base_commit_sha) if self.base_commit_sha else None
        target_commit = self.repo.commit(self.newest_commit_sha)
        self.changed_paths = set(
            get_range_changed_file_paths(base_commit, target_commit)
            if base_commit is not None
            else []
        )
        self.file_paths = list_commit_tree_files(self.repo, self.newest_commit_sha)
        self.current_file_path = None
        self._populate_tree()

    # ------------------------------------------------------------------
    # Public API: quiz management
    # ------------------------------------------------------------------

    def load_inline_quiz(
        self,
        questions: list[InlineQuizQuestion],
        answers: dict[str, str],
        grades: list[InlineQuizGrade],
        known_files: dict[str, str],
        current_index: int = 0,
    ) -> None:
        """Load quiz questions and embed them in the code view."""
        self._questions = list(questions)
        self._answers = dict(answers)
        self._grades = list(grades)
        self._known_files = dict(known_files)
        self._current_q_index = current_index
        self._anchor_cache.clear()
        # Pre-compute anchor lines
        for q in self._questions:
            qid = q.get("id", "")
            fpath = q.get("file_path", "")
            content = self._get_file_content(fpath)
            self._anchor_cache[qid] = resolve_anchor_line(q, content or "")
        self._update_tree_quiz_markers()
        # Show the file for the current question
        if self._questions:
            self._show_file_for_question(current_index)

    def activate_question(self, index: int) -> None:
        """Activate a specific question, switching files if needed."""
        if index < 0 or index >= len(self._questions):
            return
        old_index = self._current_q_index
        self._current_q_index = index
        old_file = self._questions[old_index].get("file_path", "") if old_index < len(self._questions) else ""
        new_file = self._questions[index].get("file_path", "")
        if new_file != old_file or new_file != self.current_file_path:
            self._show_file_for_question(index)
        else:
            self._refresh_quiz_blocks_state()
            self._scroll_to_active_block()

    def update_answer(self, index: int, answer: str) -> None:
        """Store an answer and refresh the corresponding quiz block."""
        if index < 0 or index >= len(self._questions):
            return
        qid = self._questions[index].get("id", "")
        self._answers[qid] = answer
        # Update the block widget
        for block in self.query(InlineQuizBlock):
            if block.index == index:
                block.update_state(answer=answer)
                break
        self._update_tree_quiz_markers()

    def update_grades(self, grades: list[InlineQuizGrade]) -> None:
        """Apply grading results to quiz blocks."""
        self._grades = list(grades)
        grade_map: dict[str, InlineQuizGrade] = {g["id"]: g for g in grades}
        for i, q in enumerate(self._questions):
            qid = q.get("id", "")
            grade = grade_map.get(qid)
            if grade is None:
                continue
            for block in self.query(InlineQuizBlock):
                if block.index == i:
                    block.update_state(grade=grade)
                    break
        self._update_tree_quiz_markers()

    def clear_quiz(self) -> None:
        """Remove all quiz blocks, keep code display."""
        self._questions.clear()
        self._answers.clear()
        self._grades.clear()
        self._known_files.clear()
        self._anchor_cache.clear()
        self._current_q_index = 0
        if self.current_file_path:
            self._render_code_only(self.current_file_path)

    # ------------------------------------------------------------------
    # File tree population (reuses CodeBrowserDock logic)
    # ------------------------------------------------------------------

    def _populate_tree(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.reset("Repository")
        tree.show_root = False
        root = tree.root
        for path in self.file_paths:
            self._add_path_to_tree(root, path)
        root.expand()
        # Select first changed file, or first file
        first_file = next(iter(self.changed_paths), None) or (
            self.file_paths[0] if self.file_paths else None
        )
        if first_file:
            self._select_path_in_tree(tree, first_file)
            self._render_file(first_file)

    def _add_path_to_tree(self, root, path: str) -> None:
        current = root
        parts = path.split("/")
        current_path: list[str] = []
        for index, part in enumerate(parts):
            current_path.append(part)
            node_path = "/".join(current_path)
            is_leaf = index == len(parts) - 1
            existing = next(
                (child for child in current.children if child.data == node_path),
                None,
            )
            if existing is not None:
                current = existing
                continue
            label = Text(part)
            if self._path_is_changed(node_path):
                label.stylize("bold green")
            node_label = label if self._path_is_changed(node_path) else part
            if is_leaf:
                current.add_leaf(node_label, data=node_path)
                return
            current = current.add(node_label, data=node_path)

    def _path_is_changed(self, node_path: str) -> bool:
        return any(
            cp == node_path or cp.startswith(f"{node_path}/")
            for cp in self.changed_paths
        )

    def _select_path_in_tree(self, tree: Tree, target_path: str) -> None:
        def walk(node):
            if node.data == target_path:
                return node
            for child in node.children:
                found = walk(child)
                if found is not None:
                    return found
            return None

        node = walk(tree.root)
        if node is not None:
            node.expand()
            tree.select_node(node)

    # ------------------------------------------------------------------
    # File rendering
    # ------------------------------------------------------------------

    def _get_file_content(self, fpath: str) -> str:
        """Get file content, preferring known_files cache, then git."""
        if fpath in self._known_files:
            return self._known_files[fpath]
        if self.repo is None:
            return ""
        return get_file_content_at_commit_or_empty(
            self.repo, self.newest_commit_sha, fpath,
        )

    def _render_file(self, path: str) -> None:
        """Render a file with or without quiz blocks depending on state."""
        self.current_file_path = path
        language = detect_code_language(path) or "text"
        self.query_one("#cv-file-label", Label).update(f"{path}  |  {language}")
        if self._questions:
            self._render_code_with_quiz(path)
        else:
            self._render_code_only(path)

    def _render_code_only(self, path: str) -> None:
        """Render full file with diff markers (all lines visible)."""
        language = detect_code_language(path) or "text"
        target_content = get_file_content_at_commit_or_empty(
            self.repo, self.newest_commit_sha, path,
        ) if self.repo else ""
        base_content = get_file_content_at_commit_or_empty(
            self.repo, self.base_commit_sha, path,
        ) if self.repo and self.base_commit_sha else ""

        if base_content:
            render_lines = build_claude_diff_lines(language, base_content, target_content)
            rendered = _join_lines(render_lines)
            if not rendered.plain.strip():
                rendered.append("변경 사항이 없습니다.\n", style="dim")
            total_rows, diff_markers = _collect_diff_markers(base_content, target_content)
        else:
            # First commit or no parent — plain numbered view
            highlighted_lines = highlight_code_lines(target_content, language)
            rendered = Text(no_wrap=True)
            for line_no, line in enumerate(highlighted_lines, start=1):
                rendered.append(f"{line_no:4}   ", style="dim")
                rendered.append_text(line)
                rendered.append("\n")
            if not rendered.plain.strip():
                rendered.append("파일 내용을 표시할 수 없습니다.\n", style="dim")
            total_rows = len(target_content.splitlines())
            diff_markers = []

        self._update_ruler(total_rows, diff_markers)
        scroll = self.query_one("#code-scroll", VerticalScroll)
        scroll.remove_children()
        scroll.mount(Static(rendered, classes="code-segment"))
        self.app.call_after_refresh(lambda: scroll.scroll_home(animate=False))

    def _render_code_with_quiz(self, path: str) -> None:
        """Render code with InlineQuizBlock widgets embedded at anchor lines."""
        language = detect_code_language(path) or "text"
        target_content = self._get_file_content(path)
        base_content = get_file_content_at_commit_or_empty(
            self.repo, self.base_commit_sha, path,
        ) if self.repo and self.base_commit_sha else ""

        # Build Claude Code-style diff lines for segment slicing
        if base_content:
            render_lines = build_claude_diff_lines(language, base_content, target_content)
            # target line (1-based) -> render_lines index (0-based) mapping
            target_to_render = _build_target_to_render_map(base_content, target_content)
        else:
            highlighted_lines_plain = highlight_code_lines(target_content, language)
            render_lines = [
                _plain_line(ln, hl)
                for ln, hl in enumerate(highlighted_lines_plain, start=1)
            ]
            # No diff: 1:1 mapping (target line N -> index N-1)
            target_to_render = {i + 1: i for i in range(len(render_lines))}

        total_lines = len(render_lines)

        # Collect questions for this file with their anchor lines
        file_questions: list[tuple[int, int, InlineQuizQuestion]] = []  # (anchor_line, global_index, question)
        grade_map: dict[str, InlineQuizGrade] = {g["id"]: g for g in self._grades}

        for i, q in enumerate(self._questions):
            if q.get("file_path", "") != path:
                continue
            qid = q.get("id", "")
            anchor = self._anchor_cache.get(qid)
            if anchor is None:
                anchor = total_lines  # put at end if no anchor found
            file_questions.append((anchor, i, q))

        # Sort by anchor line
        file_questions.sort(key=lambda t: t[0])

        # Update overview ruler
        if base_content:
            ruler_total, diff_markers = _collect_diff_markers(base_content, target_content)
        else:
            ruler_total = total_lines
            diff_markers = []
        quiz_markers = [(anchor, "quiz") for anchor, _, _ in file_questions]
        self._update_ruler(ruler_total, diff_markers + quiz_markers)

        # Build segments: split code at each anchor line
        scroll = self.query_one("#code-scroll", VerticalScroll)
        scroll.remove_children()
        self._render_id += 1
        rid = self._render_id

        if not file_questions:
            # No questions for this file, render plain
            self._render_code_only(path)
            return

        prev_line = 0  # 0-indexed end of previous segment
        widgets_to_mount: list[Widget] = []

        for anchor_line, global_idx, q in file_questions:
            # anchor_line is a target line number (1-based); convert to render_lines index
            render_anchor = target_to_render.get(anchor_line, anchor_line - 1)
            seg_end = min(render_anchor, total_lines)  # exclusive of anchor line (quiz block goes before it)

            if seg_end > prev_line:
                code_text = _join_lines(render_lines[prev_line:seg_end])
                widgets_to_mount.append(Static(code_text, classes="code-segment"))

            # Quiz block
            qid = q.get("id", "")
            answer = self._answers.get(qid, "")
            grade = grade_map.get(qid)
            is_active = global_idx == self._current_q_index
            block = InlineQuizBlock(
                question=q,
                index=global_idx,
                is_active=is_active,
                answer=answer,
                grade=grade,
                id=f"iqb-{rid}-{global_idx}",
            )
            widgets_to_mount.append(block)
            prev_line = seg_end

        # Remaining code after last question
        if prev_line < total_lines:
            code_text = _join_lines(render_lines[prev_line:total_lines])
            widgets_to_mount.append(Static(code_text, classes="code-segment"))

        scroll.mount(*widgets_to_mount)
        # Layout is computed asynchronously after mount — scroll after next refresh
        self.app.call_after_refresh(self._scroll_to_active_block)


    def _show_file_for_question(self, index: int) -> None:
        """Show the file that contains the given question and render it."""
        if index < 0 or index >= len(self._questions):
            return
        fpath = self._questions[index].get("file_path", "")
        if fpath and fpath in self.file_paths:
            tree = self.query_one("#file-tree", Tree)
            self._select_path_in_tree(tree, fpath)
        self._render_file(fpath)

    def _refresh_quiz_blocks_state(self) -> None:
        """Update active/answered/graded classes on all visible quiz blocks."""
        grade_map: dict[str, InlineQuizGrade] = {g["id"]: g for g in self._grades}
        q_by_index = {i: q for i, q in enumerate(self._questions)}
        for block in self.query(InlineQuizBlock):
            i = block.index
            q = q_by_index.get(i)
            if q is None:
                continue
            qid = q.get("id", "")
            block.update_state(
                is_active=(i == self._current_q_index),
                answer=self._answers.get(qid, ""),
                grade=grade_map.get(qid),
            )

    def _scroll_to_active_block(self) -> None:
        """Scroll the code view so the active quiz block is visible."""
        for block in self.query(InlineQuizBlock):
            if block.index == self._current_q_index:
                block.scroll_visible(animate=True)
                break

    def _update_ruler(self, total_rows: int, markers: list[tuple[int, str]]) -> None:
        try:
            self.query_one("#overview-ruler", OverviewRuler).set_markers(total_rows, markers)
        except Exception:
            pass

    def _update_tree_quiz_markers(self) -> None:
        """파일 트리 노드에 퀴즈 진행 상태 마커를 표시."""
        if not self._questions:
            return
        try:
            tree = self.query_one("#file-tree", Tree)
        except Exception:
            return

        grade_map = {g["id"]: g for g in self._grades}

        # 파일별 (전체, 답변완료, 채점완료) 집계
        file_stats: dict[str, list[int]] = {}
        for q in self._questions:
            fpath = q.get("file_path", "")
            if not fpath:
                continue
            qid = q.get("id", "")
            if fpath not in file_stats:
                file_stats[fpath] = [0, 0, 0]
            file_stats[fpath][0] += 1
            if qid in self._answers:
                file_stats[fpath][1] += 1
            if qid in grade_map:
                file_stats[fpath][2] += 1

        def walk(node) -> None:
            path = node.data
            if path and path in file_stats:
                total, answered, graded = file_stats[path]
                base = path.split("/")[-1]
                label = Text(base)
                if self._path_is_changed(path):
                    label.stylize("bold green")
                if graded == total:
                    label.append(f" ★{total}", style="yellow")
                elif answered == total:
                    label.append(f" ✔{total}", style="green")
                else:
                    label.append(f" ●{total - answered}", style="cyan")
                node.set_label(label)
            for child in node.children:
                walk(child)

        walk(tree.root)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @on(Tree.NodeSelected, "#file-tree")
    def handle_file_selected(self, event: Tree.NodeSelected) -> None:
        path = event.node.data
        if not path or path not in self.file_paths:
            return
        self._render_file(path)

    @on(InlineQuizBlock.Activated)
    def handle_quiz_block_activated(self, event: InlineQuizBlock.Activated) -> None:
        self.post_message(self.QuestionActivated(event.index))

    @on(OverviewRuler.ScrollTo)
    def handle_ruler_scroll(self, event: OverviewRuler.ScrollTo) -> None:
        scroll = self.query_one("#code-scroll", VerticalScroll)
        target_y = int(event.ratio * scroll.virtual_size.height)
        scroll.scroll_to(y=target_y, animate=False)

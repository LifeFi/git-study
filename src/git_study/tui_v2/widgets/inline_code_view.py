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
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widget import Widget
from textual.binding import Binding
from textual.widgets import Label, Static, TextArea, Tree

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


def _overlay_bg(line: Text, bg_str: str) -> Text:
    """Return a copy of `line` with all existing backgrounds replaced by `bg_str`.

    Rich의 stylize는 내부 span 배경에 덮어씌워지지 않으므로,
    새 Text를 bg_str 배경으로 구성하고 원본의 전경(foreground)만 재적용한다.
    """
    from rich.style import Style
    plain = line.plain
    new = Text(plain, style=bg_str, no_wrap=True)
    for span in line._spans:
        orig = span.style
        if isinstance(orig, str):
            orig = Style.parse(orig)
        if isinstance(orig, Style) and (orig.color or orig.bold or orig.italic or orig.dim):
            fg = Style(color=orig.color, bold=orig.bold, italic=orig.italic, dim=orig.dim)
            new.stylize(fg, span.start, span.end)
    return new


def _join_diff_with_cursor(
    render_lines: list[Text],
    vis_map: list[int],
    cursor_line: int | None,
    sel_range: tuple[int | None, int | None],
) -> Text:
    """Join diff render_lines into Text, applying cursor/selection highlights.

    vis_map[i] = target file line (1-based) for visual row i, 0 if deleted.
    선택/커서 라인은 _overlay_bg로 배경을 강제 교체해 diff 색상 위에 표시.
    """
    sel_lo, sel_hi = sel_range
    result = Text(no_wrap=True)
    for vis_idx, line_text in enumerate(render_lines):
        file_line = vis_map[vis_idx] if vis_idx < len(vis_map) else 0
        if file_line > 0:
            is_cursor = file_line == cursor_line
            is_selected = sel_lo is not None and sel_lo <= file_line <= sel_hi
            if is_cursor and sel_lo is None:
                result.append_text(_overlay_bg(line_text, "on color(17)"))
                continue
            elif is_selected:
                result.append_text(_overlay_bg(line_text, "on color(18)"))
                continue
        result.append_text(line_text)
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
        border: round #666622;
        background: $boost;
    }

    InlineQuizBlock.-active {
        border: round #ffff55;
    }

    InlineQuizBlock.-answered {
        border: round $panel;
    }

    InlineQuizBlock.-answered.-active {
        border: round #ffff55;
    }

    InlineQuizBlock.-graded {
        border: round #666600;
    }

    InlineQuizBlock.-graded.-active {
        border: round #ffff55;
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

    InlineQuizBlock .iqb-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        display: none;
    }

    InlineQuizBlock .iqb-grade-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        display: none;
    }

    InlineQuizBlock .iqb-regrade-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        display: none;
    }

    InlineQuizBlock .iqb-input {
        height: auto;
        min-height: 3;
        max-height: 8;
        margin-top: 1;
        border: tall $panel-lighten-2;
        padding: 0 1;
        background: $surface;
        display: none;
    }
    """

    class Activated(Message):
        """Fired when user clicks on a quiz block."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    class AnswerSubmitted(Message):
        """Fired when user submits an answer via the inline textarea."""

        def __init__(self, index: int, answer: str) -> None:
            super().__init__()
            self.index = index
            self.answer = answer

    class AnswerEscaped(Message):
        """Fired when user presses Escape in the inline textarea."""

        def __init__(self, index: int, via_shift_tab: bool = False) -> None:
            super().__init__()
            self.index = index
            self.via_shift_tab = via_shift_tab

    class _AnswerArea(TextArea):
        """Inline textarea for quiz block answers."""

        BINDINGS = [
            Binding("enter", "submit_answer", priority=True),
            Binding("escape", "escape_answer", priority=True),
            Binding("pagedown", "scroll_code_down", priority=True),
            Binding("pageup", "scroll_code_up", priority=True),
            Binding("ctrl+f", "scroll_code_down", priority=True),
            Binding("ctrl+b", "scroll_code_up", priority=True),
        ]

        _suppress_blur: bool = False

        def on_key(self, event) -> None:
            if event.key == "shift+enter":
                event.stop()
                event.prevent_default()
                self.insert("\n")
            elif event.key == "tab":
                event.stop()
                event.prevent_default()
                self.action_focus_cmd_bar()
            elif event.key == "shift+tab":
                event.stop()
                event.prevent_default()
                self.action_escape_answer(via_shift_tab=True)

        def action_submit_answer(self) -> None:
            answer = self.text.strip()
            if not answer:
                return
            if answer == "/grade":
                from .command_bar import CommandBar, CommandInput
                self._suppress_blur = True
                self.clear()
                try:
                    cb = self.app.query_one("#cmd-bar", CommandBar)
                    cb.focus_input()
                except Exception:
                    pass
                try:
                    cb = self.app.query_one("#cmd-bar", CommandBar)
                    inp = cb.query_one("#cb-input", CommandInput)
                    inp.value = "/grade"
                    inp.post_message(CommandInput.Submitted("/grade"))
                except Exception:
                    pass
                return
            block = self._find_block()
            if block:
                self._suppress_blur = True
                self.clear()
                block.post_message(InlineQuizBlock.AnswerSubmitted(block._index, answer))

        def action_escape_answer(self, via_shift_tab: bool = False) -> None:
            block = self._find_block()
            if block:
                self._suppress_blur = True
                block.post_message(InlineQuizBlock.AnswerEscaped(block._index, via_shift_tab=via_shift_tab))

        def action_focus_cmd_bar(self) -> None:
            self._suppress_blur = True
            try:
                self.app.query_one("#cmd-bar").focus_input()
            except Exception:
                pass

        def on_focus(self) -> None:
            block = self._find_block()
            if block:
                try:
                    block.query_one(".iqb-hint", Static).display = True
                except Exception:
                    pass

        def on_blur(self) -> None:
            block = self._find_block()
            if block:
                try:
                    block.query_one(".iqb-hint", Static).display = False
                except Exception:
                    pass
            if self._suppress_blur:
                self._suppress_blur = False
                return
            if block and block._is_active:
                block.post_message(InlineQuizBlock.AnswerEscaped(block._index))

        def action_scroll_code_down(self) -> None:
            self._scroll_code(1)

        def action_scroll_code_up(self) -> None:
            self._scroll_code(-1)

        def _scroll_code(self, direction: int) -> None:
            try:
                node = self.parent
                while node is not None:
                    try:
                        scroll = node.query_one("#code-scroll")
                        page = max(scroll.size.height - 2, 1)
                        scroll.scroll_to(
                            y=max(0, int(scroll.scroll_y) + direction * page),
                            animate=False,
                        )
                        return
                    except Exception:
                        node = getattr(node, "parent", None)
            except Exception:
                pass

        def _find_block(self) -> "InlineQuizBlock | None":
            node = self.parent
            while node:
                if isinstance(node, InlineQuizBlock):
                    return node
                node = node.parent
            return None

    def __init__(
        self,
        question: InlineQuizQuestion,
        index: int,
        *,
        total: int = 0,
        is_active: bool = False,
        answer: str = "",
        grade: InlineQuizGrade | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._question = question
        self._index = index
        self._total = total
        self._is_active = is_active
        self._answer = answer
        self._grade = grade

    @property
    def index(self) -> int:
        return self._index

    def _num_label(self) -> str:
        return f"[{self._index + 1}/{self._total}]" if self._total > 0 else f"[Q{self._index + 1}]"

    def compose(self) -> ComposeResult:
        q = self._question
        qtype_ko = QUESTION_TYPE_KO.get(q.get("question_type", ""), q.get("question_type", ""))
        header_text = f"{self._num_label()} {qtype_ko}  ·  {q.get('file_path', '')}:{self._anchor_line_display()}"
        if self._is_active:
            header_text += "  ◀ 현재"

        yield Static(header_text, classes="iqb-header")
        yield Static(q.get("question", ""), classes="iqb-body")
        yield Static(self._build_status_text(), classes="iqb-status")
        yield Static("", classes="iqb-score")
        yield Static("", classes="iqb-my-answer")
        yield Static("", classes="iqb-model-answer")
        yield Static("", classes="iqb-feedback")
        yield Static(
            "[bold cyan]Enter[/bold cyan] 답변 입력  "
            "[bold cyan]Shift+Enter[/bold cyan] 줄바꿈  "
            "[bold cyan]Shift+↑↓[/bold cyan] 이동  "
            "[bold cyan]F1[/bold cyan] 힌트  "
            "[dim]Esc 종료[/dim]",
            classes="iqb-hint",
        )
        yield Static(
            "채점하려면 명령창에서 [bold cyan]/grade[/bold cyan] 입력",
            classes="iqb-grade-hint",
        )
        yield Static(
            "다시 풀어보려면 명령창에서 [bold cyan]/quiz retry[/bold cyan] 입력",
            classes="iqb-regrade-hint",
        )
        yield InlineQuizBlock._AnswerArea(classes="iqb-input")

    def on_mount(self) -> None:
        self._apply_classes()
        self._sync_grade_widgets()
        if self._is_active and self._grade is None:
            try:
                ta = self.query_one(".iqb-input", InlineQuizBlock._AnswerArea)
                ta.display = True
                # 포커스는 여기서 잡지 않음 — #code-scroll이 포커스를 유지해야 스크롤 가능
                # 명시적 답변 모드 진입(activate_question/update_state)에서만 포커스 이동
            except Exception:
                pass

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
            is_answered = bool(self._answer) and not is_graded
            self.query_one(".iqb-grade-hint", Static).display = is_answered
            self.query_one(".iqb-regrade-hint", Static).display = is_graded
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
            header_text = f"{self._num_label()} {qtype_ko}  ·  {q.get('file_path', '')}:{self._anchor_line_display()}"
            if self._is_active:
                header_text += "  ◀ 현재"
            self.query_one(".iqb-header", Static).update(header_text)
            self.query_one(".iqb-status", Static).update(self._build_status_text())
        except Exception:
            pass
        self._apply_classes()
        self._sync_grade_widgets()
        # 인라인 textarea 표시/숨김 (포커스는 #code-scroll 유지 — on_focus 가드가 _AnswerArea를 허용 안 함)
        try:
            ta = self.query_one(".iqb-input", InlineQuizBlock._AnswerArea)
            show = self._is_active and self._grade is None
            ta.display = show
        except Exception:
            pass

    @on(Click)
    def handle_click(self, event: Click) -> None:
        if isinstance(event.widget, TextArea):
            return  # textarea 내부 클릭은 Activated 재발행하지 않음
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
# CodePane: virtual-rendering ScrollView for plain/diff code (no quiz)
# ---------------------------------------------------------------------------

class CodePane(ScrollView):
    """가상 렌더링 코드뷰. render_line(y)로 보이는 줄만 처리."""

    DEFAULT_CSS = """
    CodePane {
        width: 1fr;
        height: 1fr;
    }
    """

    class CursorKey(Message):
        """커서 이동 키를 InlineCodeView에 전달하는 메시지."""

        def __init__(self, direction: str) -> None:
            super().__init__()
            self.direction = direction  # up/down/page_up/page_down/left/right/v/escape

    _CURSOR_KEY_MAP = {
        "up": "up", "k": "up",
        "down": "down", "j": "down",
        "pageup": "page_up", "ctrl+b": "page_up",
        "pagedown": "page_down", "ctrl+f": "page_down",
        "left": "left", "right": "right",
        "v": "v", "escape": "escape",
    }

    def on_key(self, event) -> None:
        """커서 이동 키를 CursorKey 메시지로 변환. ScrollView 기본 스크롤보다 우선."""
        direction = self._CURSOR_KEY_MAP.get(event.key)
        if direction:
            self.post_message(self.CursorKey(direction))
            event.prevent_default()
            event.stop()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plain_hl_lines: list[Text] = []
        self._diff_lines: list[Text] = []
        self._diff_vis_map: list[int] = []
        self._is_diff: bool = False
        self._cursor_line: int | None = None
        self._sel_lo: int | None = None
        self._sel_hi: int | None = None
        self._max_content_width: int = 0  # 실제 최대 줄 너비 (가로 스크롤 판단용)

    @property
    def virtual_size(self):
        from textual.geometry import Size
        lines = self._diff_lines if self._is_diff else self._plain_hl_lines
        # 실제 콘텐츠 너비 vs 위젯 너비 — 큰 쪽으로 설정해야 불필요한 스크롤바가 안 생김
        w = max(self._max_content_width, self.size.width or 80)
        return Size(w, max(len(lines), 1))

    def set_plain(self, hl_lines: list[Text], cursor: int | None, sel_lo: int | None, sel_hi: int | None) -> None:
        self._plain_hl_lines = hl_lines
        self._diff_lines = []
        self._is_diff = False
        self._cursor_line = cursor
        self._sel_lo = sel_lo
        self._sel_hi = sel_hi
        # 행번호(4) + 구분자(3) + 코드 콘텐츠
        self._max_content_width = max((len(hl.plain) + 7 for hl in hl_lines), default=0)
        self.scroll_home(animate=False)
        self._invalidate()
        self.refresh()

    def set_diff(self, diff_lines: list[Text], vis_map: list[int], cursor: int | None, sel_lo: int | None, sel_hi: int | None) -> None:
        self._diff_lines = diff_lines
        self._diff_vis_map = vis_map
        self._plain_hl_lines = []
        self._is_diff = True
        self._cursor_line = cursor
        self._sel_lo = sel_lo
        self._sel_hi = sel_hi
        self._max_content_width = max((len(line.plain) for line in diff_lines), default=0)
        self.scroll_home(animate=False)
        self._invalidate()
        self.refresh()

    def update_cursor(self, cursor: int | None, sel_lo: int | None, sel_hi: int | None) -> None:
        self._cursor_line = cursor
        self._sel_lo = sel_lo
        self._sel_hi = sel_hi
        self._invalidate()
        self.refresh()

    def scroll_to_line(self, file_line: int) -> None:
        """커서 라인이 뷰포트에 들어오도록 스크롤."""
        if self._is_diff:
            vis_row = next((i for i, fl in enumerate(self._diff_vis_map) if fl == file_line), None)
            if vis_row is None:
                return
        else:
            vis_row = file_line - 1
        visible_h = self.scrollable_content_region.height or self.size.height
        top = int(self.scroll_y)
        bottom = top + visible_h - 1
        if vis_row < top:
            self.scroll_to(y=vis_row, animate=False)
        elif vis_row > bottom:
            self.scroll_to(y=vis_row - visible_h + 1, animate=False)

    def _invalidate(self) -> None:
        """StylesCache를 지워서 다음 render_line 호출이 항상 최신 데이터를 사용하게 함."""
        if hasattr(self, "_styles_cache"):
            self._styles_cache.clear()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """세로 스크롤 시 캐시 무효화 후 부모 처리(스크롤바 갱신 + refresh)."""
        self._invalidate()
        super().watch_scroll_y(old_value, new_value)

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        """가로 스크롤 시 캐시 무효화 후 부모 처리."""
        self._invalidate()
        super().watch_scroll_x(old_value, new_value)

    def render_line(self, y: int) -> Strip:
        """y = 화면 행(0~height-1). content_row = y + scroll_y, 가로는 scroll_x 반영."""
        width = self.size.width or 80
        content_row = y + int(self.scroll_y)
        scroll_x = int(self.scroll_x)

        if self._is_diff:
            lines = self._diff_lines
            if content_row >= len(lines):
                return Strip.blank(width)
            file_line = self._diff_vis_map[content_row] if content_row < len(self._diff_vis_map) else 0
            line_text = lines[content_row]
            if file_line > 0:
                is_cursor = file_line == self._cursor_line
                is_sel = self._sel_lo is not None and self._sel_lo <= file_line <= self._sel_hi
                if is_cursor and self._sel_lo is None:
                    line_text = _overlay_bg(line_text, "on color(17)")
                elif is_sel:
                    line_text = _overlay_bg(line_text, "on color(18)")
        else:
            lines = self._plain_hl_lines
            if content_row >= len(lines):
                return Strip.blank(width)
            line_no = content_row + 1  # 1-based
            hl = lines[content_row]
            is_cursor = line_no == self._cursor_line
            is_sel = self._sel_lo is not None and self._sel_lo <= line_no <= self._sel_hi
            line_text = Text(no_wrap=True)
            if is_cursor:
                line_text.append(f"{line_no:4} \u276f ", style="bold white")
            else:
                line_text.append(f"{line_no:4}   ", style="dim")
            line_text.append_text(hl)
            if is_cursor and self._sel_lo is None:
                line_text.stylize("on color(17)", 0, len(line_text.plain))
            elif is_sel:
                line_text.stylize("on color(18)", 0, len(line_text.plain))

        # Text.render(): wrap 없이 span→segment 직변환; \n 세그먼트 제거
        segs = [s for s in line_text.render(self.app.console, end="") if "\n" not in s.text]
        return Strip(segs).crop(scroll_x, scroll_x + width)


# ---------------------------------------------------------------------------
# _QuizCodeScroll: VerticalScroll subclass that posts PageScrolled for cursor tracking
# ---------------------------------------------------------------------------

class _QuizCodeScroll(VerticalScroll):
    """Quiz mode scroll container. Overrides page_up/down actions to also update cursor."""

    class PageScrolled(Message):
        def __init__(self, direction: str) -> None:
            super().__init__()
            self.direction = direction  # "up" or "down"

    def action_page_up(self) -> None:
        self.scroll_page_up()
        self.post_message(self.PageScrolled("up"))

    def action_page_down(self) -> None:
        self.scroll_page_down()
        self.post_message(self.PageScrolled("down"))


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

    class LineRangeSelected(Message):
        """현재 보고 있는 파일을 채팅에 push 요청."""

        def __init__(self, file_path: str, start_line: int = 0, end_line: int = 0) -> None:
            super().__init__()
            self.file_path = file_path
            self.start_line = start_line  # 0 = 전체 파일
            self.end_line = end_line      # 0 = 전체 파일

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

        # Cursor / line-selection state
        self._cursor_line: int | None = None    # 1-based target file line
        self._sel_start: int | None = None      # visual select start (1-based)
        self._total_file_lines: int = 0
        self._is_diff_view: bool = False
        self._visual_to_file_line: list[int] = []   # diff view: vis row(0-idx) → target line (0=deleted)
        self._cached_render_lines: list = []        # diff view render line cache
        self._cached_hl_lines: list[Text] = []      # Lv1: plain view highlight cache

        # Quiz state
        self._questions: list[InlineQuizQuestion] = []
        self._answers: dict[str, str] = {}
        self._grades: list[InlineQuizGrade] = []
        self._known_files: dict[str, str] = {}
        self._current_q_index: int = 0
        self._anchor_cache: dict[str, int | None] = {}  # "q_id" -> line number
        self._render_id: int = 0  # increment on each render to ensure unique widget IDs
        # 퀴즈 모드 세그먼트 메타: (start_render_idx, end_render_idx, lines_slice, target_to_render|None)
        self._quiz_segment_meta: list[tuple[int, int, list[Text], dict[int, int] | None]] = []
        # 스크롤 성능 최적화: 세그먼트 위젯 캐시 + 이전 커서 위치
        self._quiz_segment_widgets: list = []
        self._last_cursor_seg_idx: int | None = None
        self._last_cursor_in_seg: int | None = None


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
                    yield CodePane(id="code-pane")
                    with _QuizCodeScroll(id="code-scroll"):
                        yield Static("코드 브라우저 준비 중...", classes="code-segment")
                    yield OverviewRuler(id="overview-ruler")

    def on_mount(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.show_root = False
        self.query_one("#code-scroll").display = False


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
        focus_answer: bool = False,
    ) -> None:
        """Load quiz questions and embed them in the code view."""
        self._focus_answer_on_load = focus_answer
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
        self._focus_answer_on_load = True  # 명시적 진입 — 답변창 포커스 허용
        old_index = self._current_q_index
        self._current_q_index = index
        old_file = self._questions[old_index].get("file_path", "") if old_index < len(self._questions) else ""
        new_file = self._questions[index].get("file_path", "")
        if new_file != old_file or new_file != self.current_file_path:
            self._show_file_for_question(index)
        else:
            self._refresh_quiz_blocks_state()
            self._scroll_to_active_block()
            self.app.call_after_refresh(self._focus_active_answer_or_scroll)

    def _focus_active_answer_or_scroll(self) -> None:
        """Focus the active quiz block's answer TextArea, or fall back to cmd-bar."""
        if not getattr(self, "_focus_answer_on_load", True):
            # 플래그를 여기서 리셋하지 않음 — load_inline_quiz가 재호출될 때까지 억제 유지
            # (call_after_refresh가 여러 번 예약되어도 모두 cmd-bar로 포커스)
            try:
                self.app.query_one("#cmd-bar").focus_input()
            except Exception:
                pass
            return
        for block in self.query(InlineQuizBlock):
            if block.index == self._current_q_index and block._is_active:
                if block._grade is None:
                    try:
                        ta = block.query_one(".iqb-input", InlineQuizBlock._AnswerArea)
                        if ta.display:
                            ta.focus()
                            return
                    except Exception:
                        pass
                # 채점 완료 블록: 스크롤 후 code-scroll 포커스
                block.scroll_visible(animate=True)
                try:
                    self.query_one("#code-scroll").focus()
                except Exception:
                    pass
                return
        try:
            self.app.query_one("#cmd-bar").focus_input()
        except Exception:
            pass

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
        if path != self.current_file_path:
            self._cursor_line = 1  # 파일 전환 시 커서 첫 줄로
            self._sel_start = None
        self.current_file_path = path
        language = detect_code_language(path) or "text"
        self.query_one("#cv-file-label", Label).update(f"{path}  |  {language}")
        if self._questions:
            self._render_code_with_quiz(path)
        else:
            self._render_code_only(path)

    def _get_sel_range(self) -> tuple[int | None, int | None]:
        """Return (lo, hi) of the current visual selection, or (None, None)."""
        if self._sel_start is not None and self._cursor_line is not None:
            lo = min(self._sel_start, self._cursor_line)
            hi = max(self._sel_start, self._cursor_line)
            return lo, hi
        return None, None

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
            # Diff view — cursor/selection supported via visual_to_file_line mapping
            self._is_diff_view = True
            self._total_file_lines = len(target_content.splitlines())
            # Build visual row (0-indexed) → target file line mapping (0 = deleted line)
            base_ls = base_content.splitlines()
            target_ls = target_content.splitlines()
            vis_map: list[int] = []
            _o, _n = 1, 1
            for _tag, _i1, _i2, _j1, _j2 in SequenceMatcher(a=base_ls, b=target_ls, autojunk=False).get_opcodes():
                if _tag == "equal":
                    for _ in range(_j2 - _j1):
                        vis_map.append(_n); _o += 1; _n += 1
                elif _tag == "insert":
                    for _ in range(_j2 - _j1):
                        vis_map.append(_n); _n += 1
                elif _tag == "delete":
                    for _ in range(_i2 - _i1):
                        vis_map.append(0); _o += 1
                elif _tag == "replace":
                    for _ in range(_i2 - _i1):
                        vis_map.append(0); _o += 1
                    for _ in range(_j2 - _j1):
                        vis_map.append(_n); _n += 1
            self._visual_to_file_line = vis_map

            render_lines = build_claude_diff_lines(language, base_content, target_content)
            self._cached_render_lines = render_lines
            total_rows, diff_markers = _collect_diff_markers(base_content, target_content)

            code_pane = self.query_one("#code-pane", CodePane)
            code_pane.set_diff(render_lines, vis_map, self._cursor_line, *self._get_sel_range())
            self.query_one("#code-scroll").display = False
            code_pane.display = True
        else:
            # Plain numbered view — cursor/selection supported
            self._is_diff_view = False
            self._total_file_lines = len(target_content.splitlines())
            self._visual_to_file_line = list(range(1, self._total_file_lines + 1))
            self._cached_render_lines = []

            highlighted_lines = highlight_code_lines(target_content, language)
            self._cached_hl_lines = highlighted_lines  # Lv1: cache for _refresh_cursor
            total_rows = self._total_file_lines
            diff_markers = []

            code_pane = self.query_one("#code-pane", CodePane)
            code_pane.set_plain(self._cached_hl_lines, self._cursor_line, *self._get_sel_range())
            self.query_one("#code-scroll").display = False
            code_pane.display = True

        self._update_ruler(total_rows, diff_markers)

    def _refresh_cursor_quiz(self) -> None:
        """퀴즈 모드: 커서/선택 영역이 속한 세그먼트 업데이트."""
        # 세그먼트 위젯 캐시 — 리마운트 시 _quiz_segment_widgets = [] 로 무효화됨
        if not self._quiz_segment_widgets:
            try:
                self._quiz_segment_widgets = list(self.query(".code-segment"))
            except Exception:
                return

        segments = self._quiz_segment_widgets
        meta = self._quiz_segment_meta
        sel_lo, sel_hi = self._get_sel_range()

        # Track which segment has the current cursor for future clearing
        new_cursor_seg_idx = None

        for i, (start, end, lines_slice, t2r) in enumerate(meta):
            # Does this segment contain the cursor?
            has_cursor_here = False
            if self._cursor_line is not None:
                if t2r is not None:
                    ridx = t2r.get(self._cursor_line)
                    if ridx is not None and start <= ridx < end:
                        has_cursor_here = True
                else:
                    ridx = self._cursor_line - 1
                    if start <= ridx < end:
                        has_cursor_here = True

            if has_cursor_here:
                new_cursor_seg_idx = i

            # Does this segment overlap with selection?
            has_selection = sel_lo is not None
            overlaps_selection = False
            rev_t2r: dict[int, int] | None = None
            if t2r is not None:
                rev_t2r = {v: k for k, v in t2r.items()}
            if has_selection:
                if t2r is None:
                    # plain: ridx+1 == file line
                    if sel_lo <= end and sel_hi >= start + 1:
                        overlaps_selection = True
                else:
                    for ridx in range(start, end):
                        fline = rev_t2r.get(ridx, 0) if rev_t2r else 0
                        if fline and sel_lo <= fline <= sel_hi:
                            overlaps_selection = True
                            break

            # A segment needs update if it has the cursor, overlaps selection,
            # or it WAS the cursor segment (and we need to clear it).
            should_update = has_cursor_here or overlaps_selection or (i == self._last_cursor_seg_idx)

            if should_update:
                if i < len(segments):
                    result = Text(no_wrap=True)
                    for ridx in range(start, end):
                        line = lines_slice[ridx - start]
                        if t2r is None:
                            fline = ridx + 1
                        else:
                            fline = rev_t2r.get(ridx, 0) if rev_t2r else 0

                        is_cursor = (fline == self._cursor_line) if fline else False
                        is_sel = (has_selection and fline and sel_lo <= fline <= sel_hi)

                        if is_cursor and not has_selection:
                            result.append_text(_overlay_bg(line, "on color(17)"))
                        elif is_sel:
                            result.append_text(_overlay_bg(line, "on color(18)"))
                        else:
                            result.append_text(line)
                    segments[i].update(result)

        self._last_cursor_seg_idx = new_cursor_seg_idx

    def _refresh_cursor(self) -> None:
        """커서/선택 변경 시 CodePane만 갱신 (재하이라이트 없이)."""
        if not self.current_file_path:
            return
        # quiz 뷰 여부는 #code-pane 표시 상태로 판단 (self._questions만으로 판단하면
        # quiz 세션 중 퀴즈 없는 파일의 CodePane 커서 갱신이 누락됨)
        try:
            is_quiz_view = not self.query_one("#code-pane", CodePane).display
        except Exception:
            is_quiz_view = bool(self._questions)
        if is_quiz_view:
            self._refresh_cursor_quiz()
            return

        if self._is_diff_view:
            if not self._cached_render_lines:
                return
            try:
                code_pane = self.query_one("#code-pane", CodePane)
                code_pane.update_cursor(self._cursor_line, *self._get_sel_range())
            except Exception:
                pass
            return

        if not self._cached_hl_lines:
            return
        try:
            code_pane = self.query_one("#code-pane", CodePane)
            code_pane.update_cursor(self._cursor_line, *self._get_sel_range())
        except Exception:
            pass

    def _render_code_with_quiz(self, path: str) -> None:
        """Render code with InlineQuizBlock widgets embedded at anchor lines."""
        language = detect_code_language(path) or "text"
        target_content = self._get_file_content(path)
        base_content = get_file_content_at_commit_or_empty(
            self.repo, self.base_commit_sha, path,
        ) if self.repo and self.base_commit_sha else ""

        self._total_file_lines = len(target_content.splitlines())

        # Build Claude Code-style diff lines for segment slicing
        if base_content:
            render_lines = build_claude_diff_lines(language, base_content, target_content)
            # target line (1-based) -> render_lines index (0-based) mapping
            target_to_render = _build_target_to_render_map(base_content, target_content)
            # Build visual row (0-indexed) -> target file line mapping (0 = deleted line)
            vis_map = [0] * len(render_lines)
            for fl, ri in target_to_render.items():
                if ri < len(vis_map):
                    vis_map[ri] = fl
            self._visual_to_file_line = vis_map
        else:
            highlighted_lines_plain = highlight_code_lines(target_content, language)
            render_lines = [
                _plain_line(ln, hl)
                for ln, hl in enumerate(highlighted_lines_plain, start=1)
            ]
            # No diff: 1:1 mapping (target line N -> index N-1)
            target_to_render = {i + 1: i for i in range(len(render_lines))}
            self._visual_to_file_line = list(range(1, len(render_lines) + 1))

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
        self.query_one("#code-pane").display = False
        scroll = self.query_one("#code-scroll", _QuizCodeScroll)
        scroll.display = True
        scroll.remove_children()
        self._render_id += 1
        rid = self._render_id
        self._quiz_segment_meta = []
        self._quiz_segment_widgets = []       # 리마운트 시 위젯 캐시 무효화
        self._last_cursor_seg_idx = None
        self._last_cursor_in_seg = None
        t2r = target_to_render if base_content else None

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
                seg_lines = render_lines[prev_line:seg_end]
                code_text = _join_lines(seg_lines)
                widgets_to_mount.append(Static(code_text, classes="code-segment"))
                self._quiz_segment_meta.append((prev_line, seg_end, seg_lines, t2r))

            # Quiz block
            qid = q.get("id", "")
            answer = self._answers.get(qid, "")
            grade = grade_map.get(qid)
            app_mode = getattr(self.app, "_mode", "")
            is_active = (global_idx == self._current_q_index) and (app_mode == "quiz_answering")
            block = InlineQuizBlock(
                question=q,
                index=global_idx,
                total=len(self._questions),
                is_active=is_active,
                answer=answer,
                grade=grade,
                id=f"iqb-{rid}-{global_idx}",
            )
            widgets_to_mount.append(block)
            prev_line = seg_end

        # Remaining code after last question
        if prev_line < total_lines:
            seg_lines = render_lines[prev_line:total_lines]
            code_text = _join_lines(seg_lines)
            widgets_to_mount.append(Static(code_text, classes="code-segment"))
            self._quiz_segment_meta.append((prev_line, total_lines, seg_lines, t2r))

        scroll.mount(*widgets_to_mount)
        # Layout is computed asynchronously after mount — scroll and cursor after next refresh
        self.app.call_after_refresh(self._scroll_to_active_block)
        self.app.call_after_refresh(self._refresh_cursor_quiz)
        self.app.call_after_refresh(self._focus_active_answer_or_scroll)


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
        is_answering = getattr(self.app, "_mode", "") == "quiz_answering"
        for block in self.query(InlineQuizBlock):
            i = block.index
            q = q_by_index.get(i)
            if q is None:
                continue
            qid = q.get("id", "")
            block.update_state(
                is_active=(is_answering and i == self._current_q_index),
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
        # 파일 선택 후 #code-pane이 표시 중이면 포커스 이동 → 키보드 커서 이동 즉시 활성화
        try:
            pane = self.query_one("#code-pane", CodePane)
            if pane.display:
                pane.focus()
        except Exception:
            pass

    @on(InlineQuizBlock.Activated)
    def handle_quiz_block_activated(self, event: InlineQuizBlock.Activated) -> None:
        self.post_message(self.QuestionActivated(event.index))

    @on(OverviewRuler.ScrollTo)
    def handle_ruler_scroll(self, event: OverviewRuler.ScrollTo) -> None:
        try:
            code_pane = self.query_one("#code-pane", CodePane)
            if code_pane.display:
                target_y = int(event.ratio * code_pane.virtual_size.height)
                code_pane.scroll_to(y=target_y, animate=False)
                return
        except Exception:
            pass
        try:
            scroll = self.query_one("#code-scroll", _QuizCodeScroll)
            target_y = int(event.ratio * scroll.virtual_size.height)
            scroll.scroll_to(y=target_y, animate=False)
        except Exception:
            pass

    @on(Click, ".code-segment")
    def on_code_segment_click(self, event: Click) -> None:
        """퀴즈 모드: 코드 세그먼트 클릭으로 커서 라인 설정."""
        if not self._questions or not self.current_file_path:
            return
        try:
            segments = list(self.query(".code-segment"))
            seg_i = next((i for i, s in enumerate(segments) if s is event.widget), None)
            if seg_i is None or seg_i >= len(self._quiz_segment_meta):
                return
            start, end, lines_slice, t2r = self._quiz_segment_meta[seg_i]
            click_row = int(event.y)
            render_idx = start + click_row
            if render_idx >= end:
                render_idx = end - 1
            if t2r is not None:
                rev = {v: k for k, v in t2r.items()}
                file_line = rev.get(render_idx)
                if file_line is None:
                    return
            else:
                file_line = render_idx + 1
            self._cursor_line = max(1, min(file_line, self._total_file_lines))
            self._refresh_cursor_quiz()
        except Exception:
            pass
        event.stop()

    @on(Click, "#code-pane")
    def on_code_pane_click(self, event: Click) -> None:
        if self._questions or not self.current_file_path:
            return
        try:
            code_pane = self.query_one("#code-pane", CodePane)
            code_pane.focus()
            visual_row = int(event.y + code_pane.scroll_y)
            if self._is_diff_view:
                file_line = self._visual_to_file_line[visual_row] if visual_row < len(self._visual_to_file_line) else 0
                if file_line == 0:
                    return
            else:
                file_line = visual_row + 1
            file_line = max(1, min(file_line, self._total_file_lines))
            self._cursor_line = file_line
            code_pane.update_cursor(self._cursor_line, *self._get_sel_range())
            code_pane.scroll_to_line(file_line)
        except Exception:
            pass
        event.stop()

    def _is_code_scroll_focused(self) -> bool:
        """파일 트리와 TextArea 입력 외의 포커스인지 확인.

        on_key가 InlineCodeView까지 버블링됐다면 포커스는 반드시 내부에 있음.
        파일 트리(#file-tree-pane)와 TextArea(_AnswerArea)만 제외하면 됨.
        """
        focused = self.app.focused
        if focused is None:
            return False
        # TextArea 입력 중 (_AnswerArea) → 커서 이동 비활성화
        if isinstance(focused, TextArea):
            return False
        # 파일 트리 내 포커스 → 커서 이동 비활성화
        try:
            tree_pane = self.query_one("#file-tree-pane")
            node = focused
            while node is not None:
                if node is tree_pane:
                    return False
                node = getattr(node, "parent", None)
        except Exception:
            pass
        return True

    def _scroll_to_cursor_quiz(self) -> None:
        """퀴즈 모드: 커서 라인이 뷰포트 안에 들어오도록 #code-scroll 스크롤 조정."""
        if self._cursor_line is None or not self._questions:
            return
        try:
            scroll = self.query_one("#code-scroll", _QuizCodeScroll)
            meta = self._quiz_segment_meta

            # find render index
            render_idx = None
            for start, end, lines, t2r in meta:
                if t2r is not None:
                    ridx = t2r.get(self._cursor_line)
                    if ridx is not None and start <= ridx < end:
                        render_idx = ridx
                        break
                else:
                    ridx = self._cursor_line - 1
                    if start <= ridx < end:
                        render_idx = ridx
                        break

            if render_idx is None:
                return

            # find widget and offset
            widgets = self._quiz_segment_widgets or list(self.query(".code-segment"))
            for i, (start, end, lines, t2r) in enumerate(meta):
                if start <= render_idx < end:
                    if i < len(widgets):
                        w = widgets[i]
                        line_offset = render_idx - start
                        abs_y = w.region.y + line_offset

                        viewport_h = scroll.scrollable_content_region.height
                        if viewport_h == 0:
                            viewport_h = scroll.size.height

                        cur_y = int(scroll.scroll_y)
                        if abs_y < cur_y:
                            scroll.scroll_to(y=abs_y, animate=False)
                        elif abs_y >= cur_y + viewport_h:
                            scroll.scroll_to(y=abs_y - viewport_h + 1, animate=False)
                    break
        except Exception:
            pass

    def _scroll_to_cursor(self) -> None:
        """커서 라인이 뷰포트 안에 들어오도록 CodePane 스크롤 조정."""
        if self._cursor_line is None:
            return
        try:
            self.query_one("#code-pane", CodePane).scroll_to_line(self._cursor_line)
        except Exception:
            pass

    @on(_QuizCodeScroll.PageScrolled)
    def on_quiz_scroll_paged(self, event: _QuizCodeScroll.PageScrolled) -> None:
        """퀴즈 모드에서 #code-scroll pageup/pagedown 시 커서 라인 갱신."""
        if not self._questions or self._cursor_line is None:
            return
        try:
            scroll = self.query_one("#code-scroll", _QuizCodeScroll)
            page = max(scroll.size.height - 2, 1)
        except Exception:
            page = 10
        if event.direction == "up":
            self._cursor_line = max(1, self._cursor_line - page)
        else:
            self._cursor_line = min(self._total_file_lines, self._cursor_line + page)
        self._refresh_cursor_quiz()

    @on(CodePane.CursorKey)
    def on_code_pane_cursor_key(self, event: CodePane.CursorKey) -> None:
        """CodePane BINDINGS에서 발생한 커서 이동 처리."""
        if not self.current_file_path:
            return
        try:
            if not self.query_one("#code-pane", CodePane).display:
                return  # quiz mode: #code-scroll 포커스, 커서는 on_key/PageScrolled 처리
        except Exception:
            return
        if self._cursor_line is None:
            self._cursor_line = 1

        direction = event.direction
        if direction in ("up", "down", "page_up", "page_down"):
            try:
                pane = self.query_one("#code-pane", CodePane)
                page = max(pane.size.height - 2, 1)
            except Exception:
                page = 10
            if direction == "up":
                self._cursor_line = max(1, self._cursor_line - 1)
            elif direction == "down":
                self._cursor_line = min(self._total_file_lines, self._cursor_line + 1)
            elif direction == "page_up":
                self._cursor_line = max(1, self._cursor_line - page)
            elif direction == "page_down":
                self._cursor_line = min(self._total_file_lines, self._cursor_line + page)
            self._refresh_cursor()
            self._scroll_to_cursor()
        elif direction == "v":
            if self._sel_start is None:
                self._sel_start = self._cursor_line
            else:
                self._sel_start = None
            self._refresh_cursor()
        elif direction == "escape":
            self._sel_start = None
            self._refresh_cursor()
        elif direction == "left":
            try:
                self.query_one("#code-pane", CodePane).scroll_left(animate=False)
            except Exception:
                pass
        elif direction == "right":
            try:
                self.query_one("#code-pane", CodePane).scroll_right(animate=False)
            except Exception:
                pass

    def on_key(self, event) -> None:
        """커서 이동, 비주얼 선택, push 키 처리."""
        # 퀴즈 모드: up/down/page up/down으로 #code-scroll 스크롤 (파일 트리 포커스 제외)
        if self._questions and self._is_code_scroll_focused():
            if event.key in ("up", "k", "down", "j", "pageup", "ctrl+b", "pagedown", "ctrl+f"):
                try:
                    scroll = self.query_one("#code-scroll", _QuizCodeScroll)
                    page = max(scroll.size.height - 2, 1)
                    if event.key in ("down", "j"):
                        if self._cursor_line is not None:
                            self._cursor_line = min(self._total_file_lines, self._cursor_line + 1)
                    elif event.key in ("up", "k"):
                        if self._cursor_line is not None:
                            self._cursor_line = max(1, self._cursor_line - 1)
                    elif event.key in ("pagedown", "ctrl+f"):
                        if self._cursor_line is not None:
                            self._cursor_line = min(self._total_file_lines, self._cursor_line + page)
                    else:  # pageup, ctrl+b
                        if self._cursor_line is not None:
                            self._cursor_line = max(1, self._cursor_line - page)

                    self._refresh_cursor_quiz()
                    self._scroll_to_cursor_quiz()
                    event.stop()
                    return
                except Exception:
                    pass
            elif event.key == "v":
                if self._cursor_line is not None:
                    if self._sel_start is None:
                        self._sel_start = self._cursor_line
                    else:
                        self._sel_start = None
                    self._refresh_cursor_quiz()
                event.stop()
                return
            elif event.key == "escape":
                if self._sel_start is not None:
                    self._sel_start = None
                    self._refresh_cursor_quiz()
                    event.stop()
                    return
                # 비주얼 선택 없음 → 버블링으로 app.action_escape_to_cmdbar 처리


        # shift+enter or p: push file / range to chat
        if event.key in ("shift+enter", "p") and self.current_file_path:
            sel_lo, sel_hi = self._get_sel_range()
            if sel_lo is not None:
                self.post_message(self.LineRangeSelected(
                    file_path=self.current_file_path,
                    start_line=sel_lo,
                    end_line=sel_hi,
                ))
            else:
                self.post_message(self.LineRangeSelected(file_path=self.current_file_path))
            event.stop()

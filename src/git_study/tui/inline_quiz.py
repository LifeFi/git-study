"""Inline Quiz Dock — 소스 코드 특정 위치에 앵커된 퀴즈."""

from typing import TypedDict

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.timer import Timer
from textual.widgets import Button, Label, Static, TextArea

from ..domain.inline_anchor import (
    extract_file_paths_from_summary,
    parse_file_context_blocks,
)
from ..domain.code_context import (
    detect_code_language,
    get_file_content_at_commit_or_empty,
)
from ..services.inline_grade_service import (
    generate_inline_quiz_grade_result,
    generate_inline_quiz_grades,
    stream_inline_grade_progress,
)
from ..services.inline_quiz_service import (
    generate_inline_quiz_questions,
    stream_inline_quiz_progress,
)
from ..types import GradingSummary, InlineQuizGrade, InlineQuizQuestion
from .answer_input import AnswerTextArea
from .code_browser import highlight_code_lines


class InlineQuizSavedState(TypedDict):
    questions: list[InlineQuizQuestion]
    answers: dict[str, str]
    grades: list[InlineQuizGrade]
    current_index: int
    known_files: dict[str, str]


QUESTION_TYPE_KO = {
    "intent": "의도",
    "behavior": "동작",
    "tradeoff": "트레이드오프",
    "vulnerability": "취약점/위험",
}


def find_anchor_line(file_content: str, anchor_snippet: str) -> int | None:
    """anchor_snippet이 파일 내 몇 번째 줄에서 시작하는지 반환 (1-based).

    validate_anchor_candidates와 동일한 정규화(rstrip)를 사용하여
    부분 문자열 위치를 찾고 라인 번호로 변환한다.
    """
    if not file_content or not anchor_snippet:
        return None

    from ..domain.inline_anchor import normalize_anchor_snippet

    norm_content = normalize_anchor_snippet(file_content)
    norm_snippet = normalize_anchor_snippet(anchor_snippet)
    if not norm_snippet:
        return None

    pos = norm_content.find(norm_snippet)
    if pos == -1:
        # Fallback: 각 줄을 strip하여 느슨하게 매칭
        return _find_anchor_line_loose(file_content, anchor_snippet)

    # 부분 문자열 위치 → 라인 번호 (1-based)
    return norm_content[:pos].count("\n") + 1


def _find_anchor_line_loose(file_content: str, anchor_snippet: str) -> int | None:
    """Fallback: 각 줄을 strip하여 느슨하게 매칭. 빈 줄은 건너뛴다."""
    file_lines = file_content.splitlines()
    snippet_lines = [line for line in anchor_snippet.strip().splitlines() if line.strip()]
    if not snippet_lines:
        return None

    first = snippet_lines[0].strip()
    for index, file_line in enumerate(file_lines):
        if first not in file_line.strip() and not file_line.strip().startswith(first[:30]):
            continue
        if len(snippet_lines) == 1:
            return index + 1
        # 빈 줄을 건너뛰면서 나머지 snippet 줄 매칭
        matched = True
        snippet_idx = 1
        scan = index + 1
        while snippet_idx < len(snippet_lines) and scan < len(file_lines):
            if not file_lines[scan].strip():
                scan += 1
                continue
            if snippet_lines[snippet_idx].strip() not in file_lines[scan] and not file_lines[scan].strip().startswith(snippet_lines[snippet_idx].strip()[:30]):
                matched = False
                break
            snippet_idx += 1
            scan += 1
        if snippet_idx < len(snippet_lines):
            matched = False
        if matched:
            return index + 1
    return None


def render_annotated_code(
    file_content: str,
    language: str,
    current_anchor_line: int | None,
    all_markers: list[tuple[int, str, bool]],
) -> Text:
    highlighted_lines = highlight_code_lines(file_content, language)
    marker_map: dict[int, tuple[str, bool]] = {
        line_no: (label, is_current)
        for line_no, label, is_current in all_markers
    }
    highlight_range: set[int] = set()
    if current_anchor_line:
        for line_no in range(current_anchor_line, current_anchor_line + 6):
            highlight_range.add(line_no)

    result = Text()
    for index, line_text in enumerate(highlighted_lines):
        line_no = index + 1

        if line_no in marker_map:
            label, is_current = marker_map[line_no]
            style = "bold bright_cyan" if is_current else "cyan"
            result.append(f"  ┌── {label}\n", style=style)

        result.append(f"{line_no:4} ", style="dim")
        if line_no in highlight_range:
            result.append("▌ ", style="green")
            tinted = line_text.copy()
            tinted.stylize("on color(22)")
            result.append_text(tinted)
        else:
            result.append("  ")
            result.append_text(line_text)
        result.append("\n")

    return result


INLINE_QUIZ_SHARED_CSS = """
    #iq-header {
        height: auto;
        align: left middle;
    }

    #iq-title {
        color: $accent;
        text-style: bold;
        width: auto;
        margin-right: 1;
    }

    #iq-header-status {
        width: auto;
        color: $text-muted;
    }

    #iq-header-status.-loading {
        color: $success;
        text-style: bold;
    }

    #iq-header-spacer {
        width: 1fr;
    }

    #iq-close {
        width: auto;
        min-width: 5;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        tint: transparent;
    }

    #iq-close:hover,
    #iq-close:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #iq-body {
        height: 1fr;
    }

    #iq-code-panel,
    #iq-quiz-panel {
        border: none;
        padding: 0 1;
    }

    #iq-code-panel {
        width: 1fr;
        margin-right: 1;
    }

    #iq-code-file-label {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    #iq-code-file-label.-loading {
        color: $success;
        text-style: bold;
    }

    #iq-code-scroll {
        height: 1fr;
        border: round $panel;
        background: $boost;
    }

    #iq-code-content {
        width: 1fr;
        padding: 1;
    }

    #iq-quiz-panel {
        width: 38;
        min-width: 34;
    }

    #iq-answering-group {
        height: 1fr;
    }

    #iq-q-nav {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }

    .iq-q-btn {
        width: auto;
        min-width: 3;
        height: auto;
        min-height: 1;
        padding: 0 1;
        background: transparent;
        border: none;
        color: $text;
        tint: transparent;
        text-align: center;
        content-align: center middle;
        text-style: bold;
        margin-right: 1;
    }

    .iq-q-btn.iq-active {
        color: $text;
        background: $accent 20%;
        text-style: bold;
    }

    .iq-q-btn.iq-answered {
        color: $text;
    }

    #iq-q-type-badge {
        height: auto;
        color: $success;
        text-style: bold;
        margin-bottom: 1;
    }

    #iq-question-text {
        height: auto;
        margin-bottom: 1;
        color: $text;
    }

    #iq-answer-label {
        height: auto;
        color: $text-muted;
        margin-bottom: 0;
    }

    #iq-answer-input {
        height: 8;
        margin-bottom: 1;
    }

    #iq-nav-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    .iq-nav-btn,
    #iq-prev,
    #iq-next {
        width: auto;
        min-width: 8;
        height: 1;
        min-height: 1;
        padding: 0;
        margin-right: 1;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        tint: transparent;
    }

    .iq-nav-btn:hover,
    .iq-nav-btn:focus,
    #iq-prev:hover,
    #iq-prev:focus,
    #iq-next:hover,
    #iq-next:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #iq-nav-spacer {
        width: 1fr;
    }

    .iq-nav-btn:disabled,
    #iq-prev:disabled,
    #iq-next:disabled {
        color: $text-muted;
        text-style: none;
    }

    .iq-nav-btn:disabled:hover,
    .iq-nav-btn:disabled:focus,
    #iq-prev:disabled:hover,
    #iq-prev:disabled:focus,
    #iq-next:disabled:hover,
    #iq-next:disabled:focus {
        background: transparent;
        border: none;
        color: $text-muted;
        text-style: none;
    }

    #iq-results-group {
        height: 1fr;
        display: none;
        margin-bottom: 1;
    }

    #iq-results-title {
        height: auto;
        color: $accent;
        text-style: bold;
        margin: 0 0 1 0;
    }

    #iq-result-scroll {
        height: 1fr;
        border: round $panel;
        background: $boost;
    }

    #iq-result-content {
        width: 1fr;
        padding: 1;
    }

    #iq-status-scroll {
        height: 3;
        background: $panel;
        scrollbar-size-vertical: 1;
    }

    #iq-status-bar {
        height: auto;
        min-height: 3;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
"""


class InlineQuizWidget(Vertical):
    DEFAULT_CSS = INLINE_QUIZ_SHARED_CSS

    BINDINGS = [
        ("left,h", "prev_question", "Prev"),
        ("right,l", "next_question", "Next"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_context: dict = {}
        self.repo = None
        self.target_commit_sha = ""
        self.cache_key = ""
        self.user_request = ""
        self.grading_request = ""
        self.questions: list[InlineQuizQuestion] = []
        self.answers: dict[str, str] = {}
        self.grades: list[InlineQuizGrade] = []
        self.current_index = 0
        self._known_files: dict[str, str] = {}
        self._resolved_paths: dict[str, str] = {}
        self._anchor_cache: dict[str, int | None] = {}
        self._state = "idle"
        self._anim_frame = 0
        self._loading_progress_label = ""
        self._grading_progress_label = ""
        self.grading_summary: GradingSummary = {}
        self._nav_build_serial = 0
        self._animate_timer: Timer | None = None
        self._session_serial = 0

    def _score_gauge_text(self, score: int) -> str:
        clamped = max(0, min(100, int(score)))
        filled = clamped // 10
        return ("█" * filled) + ("░" * (10 - filled))

    def compose(self) -> ComposeResult:
        with Horizontal(id="iq-header"):
            yield Label("Inline Quiz", id="iq-title")
            yield Label("", id="iq-header-status")
            yield Static("", id="iq-header-spacer")
            yield Button("Close", id="iq-close", compact=True, flat=True)
        with Horizontal(id="iq-body"):
            with Vertical(id="iq-code-panel"):
                yield Label("파일 로딩 대기 중...", id="iq-code-file-label")
                with VerticalScroll(id="iq-code-scroll"):
                    yield Static("", id="iq-code-content")
            with Vertical(id="iq-quiz-panel"):
                yield Horizontal(id="iq-q-nav")
                with Vertical(id="iq-answering-group"):
                    yield Label("", id="iq-q-type-badge")
                    yield Static("질문 생성 대기 중...", id="iq-question-text")
                    yield Label("답변:", id="iq-answer-label")
                    yield AnswerTextArea("", id="iq-answer-input")
                    with Vertical(id="iq-results-group"):
                        yield Label("채점 결과", id="iq-results-title")
                        with VerticalScroll(id="iq-result-scroll"):
                            yield Static("", id="iq-result-content")
                    with Horizontal(id="iq-nav-row"):
                        yield Button(
                            "◀ Prev",
                            id="iq-prev",
                            classes="iq-nav-btn",
                            compact=True,
                            flat=True,
                        )
                        yield Static("", id="iq-nav-spacer")
                        yield Button(
                            "Next ▶",
                            id="iq-next",
                            classes="iq-nav-btn",
                            compact=True,
                            flat=True,
                        )
        with VerticalScroll(id="iq-status-scroll"):
            yield Static("Inline Quiz를 열어주세요.", id="iq-status-bar")

    def on_mount(self) -> None:
        self._animate_timer = self.set_interval(0.35, self._tick_animation, pause=True)
        self.display = False
        self._set_status("Inline Quiz를 열어주세요.")

    def _notify_app_controls_changed(self) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        refresh = getattr(app, "_refresh_result_command_row", None)
        if callable(refresh):
            refresh()

    def _persist_state_to_app(self) -> None:
        app = getattr(self, "app", None)
        if app is None or not self.cache_key:
            return
        save_state = getattr(app, "save_inline_quiz_state", None)
        if callable(save_state):
            save_state(self.cache_key, self.get_saved_state(), self.grading_summary)

    def _notify_inline_generation_started(self) -> None:
        app = getattr(self, "app", None)
        if app is None or not self.cache_key:
            return
        notify = getattr(app, "notify_inline_quiz_started", None)
        if callable(notify):
            notify(self.cache_key)

    def _notify_inline_generation_finished(self) -> None:
        app = getattr(self, "app", None)
        if app is None or not self.cache_key:
            return
        notify = getattr(app, "notify_inline_quiz_finished", None)
        if callable(notify):
            notify(self.cache_key)

    def _notify_inline_grading_started(self) -> None:
        app = getattr(self, "app", None)
        if app is None or not self.cache_key:
            return
        notify = getattr(app, "notify_inline_grade_started", None)
        if callable(notify):
            notify(self.cache_key)

    def _notify_inline_grading_finished(self) -> None:
        app = getattr(self, "app", None)
        if app is None or not self.cache_key:
            return
        notify = getattr(app, "notify_inline_grade_finished", None)
        if callable(notify):
            notify(self.cache_key)

    def show_quiz(
        self,
        *,
        commit_context: dict,
        repo,
        target_commit_sha: str,
        title_suffix: str = "",
        saved_state: InlineQuizSavedState | None = None,
        cache_key: str = "",
        user_request: str = "",
        grading_request: str = "",
    ) -> None:
        self._session_serial += 1
        session_serial = self._session_serial
        self.display = True
        self.styles.display = "block"
        self.commit_context = commit_context
        self.repo = repo
        self.target_commit_sha = target_commit_sha
        self.cache_key = cache_key or target_commit_sha
        self.user_request = user_request
        self.grading_request = grading_request
        self._reset_ui()

        title = f"Inline Quiz  {title_suffix}".rstrip()
        self.query_one("#iq-title", Label).update(title)
        if saved_state is not None:
            self._notify_inline_generation_finished()
            self._restore_state(saved_state)
        else:
            self._state = "loading"
            self._notify_inline_generation_started()
            self._anim_frame = 0
            if self._animate_timer is not None:
                self._animate_timer.reset()
                self._animate_timer.resume()
            self._tick_animation()
            self._generate_questions(
                session_serial,
                commit_context,
                repo,
                target_commit_sha,
                user_request,
            )
        self._notify_app_controls_changed()

    def hide_panel(self) -> None:
        self._session_serial += 1
        self._notify_inline_generation_finished()
        self._notify_inline_grading_finished()
        self._save_current_answer()
        if self.cache_key and hasattr(self.app, "save_inline_quiz_state"):
            self.app.save_inline_quiz_state(self.cache_key, self.get_saved_state())
        self.display = False
        self.styles.display = "none"
        if self._animate_timer is not None:
            self._animate_timer.pause()

    def fixed_panel_width(self) -> int:
        return 78

    def show_placeholder(self, message: str) -> None:
        self._session_serial += 1
        self._notify_inline_generation_finished()
        self._notify_inline_grading_finished()
        self.display = True
        self.styles.display = "block"
        self.questions = []
        self.answers = {}
        self.grades = []
        self.grading_summary = {}
        self.current_index = 0
        self._state = "idle"
        self._anim_frame = 0
        self._loading_progress_label = ""
        self._grading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._nav_build_serial += 1
        self.query_one("#iq-title", Label).update("Inline Quiz")
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        self.query_one("#iq-q-nav", Horizontal).remove_children()
        self.query_one("#iq-q-type-badge", Label).update("")
        self.query_one("#iq-question-text", Static).update(message)
        self.query_one("#iq-code-file-label", Label).remove_class("-loading")
        self.query_one("#iq-answer-input", TextArea).text = ""
        self.query_one("#iq-answer-input", TextArea).blur()
        self.query_one("#iq-answering-group", Vertical).display = True
        self.query_one("#iq-results-group", Vertical).display = False
        self.query_one("#iq-code-file-label", Label).update("선택된 커밋 없음")
        self.query_one("#iq-code-content", Static).update(
            Text("커밋을 선택하면 이 영역에 앵커된 코드가 표시됩니다.", style="dim")
        )
        self._set_status(message)
        self._notify_app_controls_changed()

    def _reset_ui(self) -> None:
        self.questions = []
        self.answers = {}
        self.grades = []
        self.grading_summary = {}
        self.current_index = 0
        self._known_files = {}
        self._resolved_paths = {}
        self._anchor_cache = {}
        self._state = "idle"
        self._anim_frame = 0
        self._loading_progress_label = ""
        self._grading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._nav_build_serial += 1
        self.query_one("#iq-q-nav", Horizontal).remove_children()
        self.query_one("#iq-q-type-badge", Label).update("")
        self.query_one("#iq-question-text", Static).update("")
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        self.query_one("#iq-code-file-label", Label).add_class("-loading")
        self.query_one("#iq-answer-input", TextArea).text = ""
        self.query_one("#iq-answering-group", Vertical).display = True
        self.query_one("#iq-results-group", Vertical).display = False
        self.query_one("#iq-code-file-label", Label).update("인라인 퀴즈 생성 중.")
        self.query_one("#iq-code-content", Static).update("")
        self._set_status("")

    def _restore_state(self, saved: InlineQuizSavedState) -> None:
        self.questions = list(saved["questions"])
        self.answers = dict(saved["answers"])
        self.grades = list(saved["grades"])
        self.current_index = int(saved["current_index"])
        self._known_files = dict(saved["known_files"])
        self._resolved_paths = {}
        self._anchor_cache = {}
        self._loading_progress_label = ""
        self._grading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        self.query_one("#iq-code-file-label", Label).remove_class("-loading")
        self._build_q_nav()
        self._update_question_panel(focus_input=False)
        self._update_code_panel()
        if self.grades:
            self._state = "results"
            self._show_results()
        else:
            self._state = "answering"
            loaded_paths = ", ".join(self._known_files.keys()) or "없음"
            self._set_status(
                f"질문 {len(self.questions)}개 복원됨  |  로드된 파일: {loaded_paths}"
            )
        self._notify_app_controls_changed()

    def _tick_animation(self) -> None:
        if self._state not in {"loading", "grading"} or not self.display:
            return
        dots = ("+" * ((self._anim_frame % 3) + 1)).ljust(3)
        if self._state == "loading":
            progress_text = (
                f"인라인 퀴즈 생성 중 [{self._loading_progress_label}]{dots}"
                if self._loading_progress_label
                else f"인라인 퀴즈 생성 중{dots}"
            )
            self.query_one("#iq-code-file-label", Label).add_class("-loading")
            self.query_one("#iq-code-file-label", Label).update(progress_text)
            self.query_one("#iq-header-status", Label).update("")
            self.query_one("#iq-header-status", Label).remove_class("-loading")
            self.query_one("#iq-question-text", Static).update("")
        elif self._state == "grading":
            progress_text = (
                f"채점 중 [{self._grading_progress_label}]"
                if self._grading_progress_label
                else "채점 중"
            )
            self.query_one("#iq-header-status", Label).update(f"{progress_text}{dots}")
            self.query_one("#iq-header-status", Label).add_class("-loading")
        self._anim_frame += 1

    def _set_loading_progress_node(self, label: str) -> None:
        self._loading_progress_label = label
        if self._state == "loading":
            self._tick_animation()

    def _set_grading_progress_node(self, label: str) -> None:
        self._grading_progress_label = label
        if self._state == "grading":
            self._tick_animation()

    def _set_status(self, text: str) -> None:
        self.query_one("#iq-status-bar", Static).update(text)

    def _build_known_files(
        self,
        commit_context: dict,
        repo,
        target_commit_sha: str,
    ) -> dict[str, str]:
        known_files = dict(parse_file_context_blocks(commit_context.get("file_context_text", "")))

        all_paths: set[str] = set(known_files.keys())
        for path in extract_file_paths_from_summary(
            commit_context.get("changed_files_summary", "")
        ):
            all_paths.add(path)

        parent_sha: str | None = None
        try:
            commit = repo.commit(target_commit_sha)
            if commit.parents:
                parent_sha = commit.parents[0].hexsha
        except Exception:
            pass

        for path in all_paths:
            full = get_file_content_at_commit_or_empty(
                repo, target_commit_sha, path
            )
            if full:
                known_files[path] = full
            elif parent_sha:
                parent_full = get_file_content_at_commit_or_empty(
                    repo, parent_sha, path
                )
                if parent_full:
                    known_files[path] = parent_full
        return known_files

    @work(thread=True)
    def _generate_questions(
        self,
        session_serial: int,
        commit_context: dict,
        repo,
        target_commit_sha: str,
        user_request: str,
    ) -> None:
        try:
            known_files = self._build_known_files(commit_context, repo, target_commit_sha)
            self.app.call_from_thread(
                self._set_loading_progress_if_current,
                session_serial,
                "파일 문맥 준비",
            )
            questions = None
            for event in stream_inline_quiz_progress(
                commit_context,
                user_request=user_request,
            ):
                if event.get("type") == "node":
                    self.app.call_from_thread(
                        self._set_loading_progress_if_current,
                        session_serial,
                        str(event.get("label", "")),
                    )
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    questions = result.get("inline_questions", [])
            if questions is None:
                questions = generate_inline_quiz_questions(
                    commit_context,
                    user_request=user_request,
                )
            self.app.call_from_thread(
                self._apply_questions_loaded_if_current,
                session_serial,
                known_files,
                questions,
            )
        except Exception as exc:
            self.app.call_from_thread(
                self._apply_questions_failed_if_current,
                session_serial,
                str(exc),
            )

    def _set_loading_progress_if_current(self, session_serial: int, label: str) -> None:
        if session_serial != self._session_serial:
            return
        self._set_loading_progress_node(label)

    def _apply_questions_loaded_if_current(
        self,
        session_serial: int,
        known_files: dict[str, str],
        questions: list[InlineQuizQuestion],
    ) -> None:
        if session_serial != self._session_serial:
            return
        self._known_files = known_files
        self._on_questions_loaded(questions)

    def _apply_questions_failed_if_current(
        self,
        session_serial: int,
        error: str,
    ) -> None:
        if session_serial != self._session_serial:
            return
        self._on_questions_failed(error)

    def _on_questions_loaded(self, questions: list[InlineQuizQuestion]) -> None:
        self._notify_inline_generation_finished()
        self.questions = questions
        self.answers = {}
        self.grades = []
        self.grading_summary = {}
        self.current_index = 0
        self._state = "answering"
        self._loading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        self.query_one("#iq-code-file-label", Label).remove_class("-loading")
        self._build_q_nav()
        self._update_question_panel()
        self._update_code_panel()
        loaded_paths = ", ".join(self._known_files.keys()) or "없음"
        self._set_status(
            f"질문 {len(questions)}개 생성됨  |  로드된 파일: {loaded_paths}"
        )
        self._persist_state_to_app()
        self._notify_app_controls_changed()

    def _on_questions_failed(self, error: str) -> None:
        self._notify_inline_generation_finished()
        self._state = "answering"
        self._loading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        self.query_one("#iq-code-file-label", Label).remove_class("-loading")
        friendly_error = error
        if "OPENAI_API_KEY" in error or "API key" in error:
            friendly_error = (
                "인라인 퀴즈 질문 생성에는 OpenAI API Key가 필요합니다. "
                "API Key 버튼에서 설정해 주세요."
            )
        self._set_status(f"질문 생성 실패: {friendly_error[:100]}")
        self.query_one("#iq-question-text", Static).update(
            f"질문 생성에 실패했습니다.\n\n{friendly_error[:300]}"
        )
        self._notify_app_controls_changed()

    def _build_q_nav(self) -> None:
        nav = self.query_one("#iq-q-nav", Horizontal)
        nav.remove_children()
        self._nav_build_serial += 1
        for index, question in enumerate(self.questions):
            answered = bool(self.answers.get(question["id"], "").strip())
            classes = "iq-q-btn" + (" iq-active" if index == self.current_index else "")
            nav.mount(
                Button(
                    f"{index + 1}✓" if answered else str(index + 1),
                    id=f"iq-nav-{self._nav_build_serial}-{index}",
                    classes=classes,
                    compact=True,
                    flat=True,
                )
            )

    def _refresh_q_nav_styles(self) -> None:
        nav = self.query_one("#iq-q-nav", Horizontal)
        buttons = [child for child in nav.children if isinstance(child, Button)]
        for index, question in enumerate(self.questions):
            if index >= len(buttons):
                continue
            button = buttons[index]
            answered = bool(self.answers.get(question["id"], "").strip())
            button.label = f"{index + 1}✓" if answered else str(index + 1)
            button.remove_class("iq-active", "iq-answered")
            if index == self.current_index:
                button.add_class("iq-active")
            elif answered:
                button.add_class("iq-answered")

    def _resolve_to_known(self, file_path: str, anchor_snippet: str) -> str:
        cache_key = f"{file_path}\n{anchor_snippet}"
        if cache_key in self._resolved_paths:
            return self._resolved_paths[cache_key]

        if file_path in self._known_files:
            self._resolved_paths[cache_key] = file_path
            return file_path

        for known_path in self._known_files:
            if known_path.endswith(file_path) or file_path.endswith(known_path):
                self._resolved_paths[cache_key] = known_path
                return known_path
            if known_path.split("/")[-1] == file_path.split("/")[-1]:
                self._resolved_paths[cache_key] = known_path
                return known_path

        for known_path, content in self._known_files.items():
            if find_anchor_line(content, anchor_snippet):
                self._resolved_paths[cache_key] = known_path
                return known_path

        self._resolved_paths[cache_key] = file_path
        return file_path

    def _get_file_content(self, question: InlineQuizQuestion) -> tuple[str, str]:
        resolved = self._resolve_to_known(question["file_path"], question["anchor_snippet"])
        return resolved, self._known_files.get(resolved, "")

    def _get_anchor_line(self, question: InlineQuizQuestion) -> int | None:
        key = f"{question['id']}:{question['file_path']}"
        if key not in self._anchor_cache:
            _, content = self._get_file_content(question)
            self._anchor_cache[key] = find_anchor_line(content, question["anchor_snippet"])
        return self._anchor_cache[key]

    def _update_question_panel(self, *, focus_input: bool = False) -> None:
        if not self.questions:
            return
        question = self.questions[self.current_index]
        type_ko = QUESTION_TYPE_KO.get(question["question_type"], question["question_type"])
        self.query_one("#iq-q-type-badge", Label).update(
            f"[{self.current_index + 1}/{len(self.questions)}]  {type_ko}"
        )
        self.query_one("#iq-question-text", Static).update(question["question"])
        answer_input = self.query_one("#iq-answer-input", TextArea)
        answer_input.text = self.answers.get(question["id"], "")
        answer_input.disabled = self._state in {"grading", "results"}
        self.query_one("#iq-prev", Button).disabled = self.current_index <= 0
        self.query_one("#iq-next", Button).disabled = self.current_index >= len(self.questions) - 1
        if focus_input and not answer_input.disabled:
            answer_input.focus()
        self._refresh_q_nav_styles()

    def _update_code_panel(self) -> None:
        if not self.questions:
            return
        question = self.questions[self.current_index]
        resolved_path, content = self._get_file_content(question)
        language = detect_code_language(resolved_path) or "text"

        if not content:
            self.query_one("#iq-code-file-label", Label).update(
                f"[파일 없음] {question['file_path']}"
            )
            fallback = Text()
            fallback.append("앵커 코드 (파일 로드 실패)\n\n", style="dim red")
            type_ko = QUESTION_TYPE_KO.get(question["question_type"], "")
            fallback.append(
                f"  ┌── [{question['id'].upper()}] {type_ko}\n",
                style="bold bright_cyan",
            )
            for line_no, line in enumerate(question["anchor_snippet"].splitlines(), 1):
                fallback.append(f"{line_no:4} ", style="dim")
                fallback.append("▌ ", style="green")
                fallback.append(f"{line}\n")
            self.query_one("#iq-code-content", Static).update(fallback)
            return

        self.query_one("#iq-code-file-label", Label).update(
            f"{resolved_path}  |  {language}"
        )

        current_anchor = self._get_anchor_line(question)
        markers: list[tuple[int, str, bool]] = []
        for index, other_question in enumerate(self.questions):
            other_resolved, _ = self._get_file_content(other_question)
            if other_resolved != resolved_path:
                continue
            line_no = self._get_anchor_line(other_question)
            if line_no:
                type_ko = QUESTION_TYPE_KO.get(
                    other_question["question_type"],
                    other_question["question_type"],
                )
                markers.append(
                    (
                        line_no,
                        f"[{other_question['id'].upper()}] {type_ko}",
                        index == self.current_index,
                    )
                )

        renderable = render_annotated_code(content, language, current_anchor, markers)
        self.query_one("#iq-code-content", Static).update(renderable)

        if current_anchor and current_anchor > 5:
            self.query_one("#iq-code-scroll", VerticalScroll).scroll_to(
                y=current_anchor - 5,
                animate=False,
            )

    def _save_current_answer(self) -> None:
        if not self.questions:
            return
        question = self.questions[self.current_index]
        self.answers[question["id"]] = self.query_one("#iq-answer-input", TextArea).text
        self._refresh_q_nav_styles()

    def _navigate_to(self, index: int) -> None:
        self._save_current_answer()
        self.current_index = max(0, min(index, len(self.questions) - 1))
        self._update_question_panel()
        self._update_code_panel()
        if self._state == "results":
            self._show_results()

    @on(Button.Pressed, "#iq-prev")
    def handle_prev(self) -> None:
        self._navigate_to(self.current_index - 1)

    @on(Button.Pressed, "#iq-next")
    def handle_next(self) -> None:
        self._navigate_to(self.current_index + 1)

    @on(AnswerTextArea.Submit)
    def handle_answer_input_submit(self, message: AnswerTextArea.Submit) -> None:
        if message.answer_input is not self.query_one("#iq-answer-input", AnswerTextArea):
            return
        self._save_current_answer()
        self._persist_state_to_app()

    @on(AnswerTextArea.NavigatePrevious)
    def handle_answer_input_previous(
        self, message: AnswerTextArea.NavigatePrevious
    ) -> None:
        if message.answer_input is not self.query_one("#iq-answer-input", AnswerTextArea):
            return
        self._save_current_answer()
        self._persist_state_to_app()
        self._navigate_to(self.current_index - 1)

    @on(AnswerTextArea.NavigateNext)
    def handle_answer_input_next(self, message: AnswerTextArea.NavigateNext) -> None:
        if message.answer_input is not self.query_one("#iq-answer-input", AnswerTextArea):
            return
        self._save_current_answer()
        self._persist_state_to_app()
        if self.current_index >= len(self.questions) - 1:
            self.handle_grade()
        else:
            self._navigate_to(self.current_index + 1)

    @on(TextArea.Changed, "#iq-answer-input")
    def handle_answer_input_changed(self) -> None:
        if not self.questions:
            return
        self._save_current_answer()
        self._persist_state_to_app()

    def handle_grade(self) -> None:
        self._save_current_answer()
        self._state = "grading"
        self._notify_inline_grading_started()
        self._anim_frame = 0
        if self._animate_timer is not None:
            self._animate_timer.reset()
            self._animate_timer.resume()
        self._notify_app_controls_changed()
        self._do_grade()

    @on(Button.Pressed, "#iq-close")
    def handle_close(self) -> None:
        self.hide_panel()
        if hasattr(self.app, "_after_inline_quiz_closed"):
            self.app._after_inline_quiz_closed()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("iq-nav-"):
            try:
                index = int(button_id.rsplit("-", 1)[-1])
            except ValueError:
                return
            self._navigate_to(index)
            event.stop()

    def get_saved_state(self) -> InlineQuizSavedState:
        return InlineQuizSavedState(
            questions=self.questions,
            answers=dict(self.answers),
            grades=list(self.grades),
            current_index=self.current_index,
            known_files=dict(self._known_files),
        )

    def action_prev_question(self) -> None:
        if self.questions:
            self._navigate_to(self.current_index - 1)

    def action_next_question(self) -> None:
        if self.questions:
            self._navigate_to(self.current_index + 1)

    @work(thread=True)
    def _do_grade(self) -> None:
        session_serial = self._session_serial
        cache_key = self.cache_key
        saved_state = self.get_saved_state()
        try:
            grades = None
            grading_summary: GradingSummary = {}
            for event in stream_inline_grade_progress(
                self.questions,
                self.answers,
                user_request=self.grading_request,
            ):
                if event.get("type") == "node":
                    self.app.call_from_thread(
                        self._set_grading_progress_if_current,
                        session_serial,
                        str(event.get("label", "")),
                    )
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    grades = result.get("final_grades", [])
                    grading_summary = dict(result.get("grading_summary", {}))
            if grades is None:
                result = generate_inline_quiz_grade_result(
                    self.questions,
                    self.answers,
                    user_request=self.grading_request,
                )
                grades = result.get("final_grades", [])
                grading_summary = dict(result.get("grading_summary", {}))
            self.app.call_from_thread(
                self._apply_grades_loaded_if_current,
                session_serial,
                cache_key,
                saved_state,
                grades,
                grading_summary,
            )
        except Exception as exc:
            self.app.call_from_thread(
                self._apply_grades_failed_if_current,
                session_serial,
                cache_key,
                str(exc),
            )

    def _set_grading_progress_if_current(self, session_serial: int, label: str) -> None:
        if session_serial != self._session_serial:
            return
        self._set_grading_progress_node(label)

    def _apply_grades_loaded_if_current(
        self,
        session_serial: int,
        cache_key: str,
        saved_state: InlineQuizSavedState,
        grades: list[InlineQuizGrade],
        grading_summary: GradingSummary | None = None,
    ) -> None:
        app = getattr(self, "app", None)
        if app is not None and cache_key:
            save_state = getattr(app, "save_inline_quiz_state", None)
            if callable(save_state):
                save_state(
                    cache_key,
                    InlineQuizSavedState(
                        questions=list(saved_state["questions"]),
                        answers=dict(saved_state["answers"]),
                        grades=list(grades),
                        current_index=saved_state["current_index"],
                        known_files=dict(saved_state["known_files"]),
                    ),
                    grading_summary,
                )
        if session_serial != self._session_serial:
            if app is not None and cache_key:
                notify = getattr(app, "notify_inline_grade_finished", None)
                if callable(notify):
                    notify(cache_key)
            return
        self._on_grades_loaded(grades, grading_summary)

    def _apply_grades_failed_if_current(
        self,
        session_serial: int,
        cache_key: str,
        error: str,
    ) -> None:
        if session_serial != self._session_serial:
            app = getattr(self, "app", None)
            if app is not None and cache_key:
                notify = getattr(app, "notify_inline_grade_finished", None)
                if callable(notify):
                    notify(cache_key)
            return
        self._on_grades_failed(error)

    def _on_grades_loaded(
        self,
        grades: list[InlineQuizGrade],
        grading_summary: GradingSummary | None = None,
    ) -> None:
        self._notify_inline_grading_finished()
        self.grades = grades
        self.grading_summary = grading_summary or {}
        self._state = "results"
        self._grading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        self._update_question_panel()
        self._show_results()
        self._persist_state_to_app()
        self._notify_app_controls_changed()

    def _on_grades_failed(self, error: str) -> None:
        self._notify_inline_grading_finished()
        self._state = "answering"
        self._grading_progress_label = ""
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self.query_one("#iq-header-status", Label).update("")
        self.query_one("#iq-header-status", Label).remove_class("-loading")
        friendly_error = error
        if "OPENAI_API_KEY" in error or "API key" in error:
            friendly_error = (
                "인라인 퀴즈 채점에는 OpenAI API Key가 필요합니다. "
                "API Key 버튼에서 설정해 주세요."
            )
        self._set_status(f"채점 실패: {friendly_error[:100]}")
        self._notify_app_controls_changed()

    def _show_results(self) -> None:
        grade_map = {grade["id"]: grade for grade in self.grades}
        avg_score = (
            sum(grade["score"] for grade in self.grades) // len(self.grades)
            if self.grades
            else 0
        )
        if not self.questions:
            self.query_one("#iq-result-content", Static).update("")
            self.query_one("#iq-answering-group", Vertical).display = False
            self.query_one("#iq-results-group", Vertical).display = True
            self.query_one("#iq-answer-input", TextArea).disabled = True
            self._set_status("채점 결과가 없습니다.")
            return

        current_question = self.questions[self.current_index]
        current_grade = grade_map.get(current_question["id"])
        current_score = int(current_grade["score"]) if current_grade else 0
        current_feedback = (
            str(current_grade.get("feedback", "")).strip()
            if current_grade is not None
            else "채점 결과를 불러오지 못했습니다."
        )
        type_ko = QUESTION_TYPE_KO.get(
            current_question["question_type"],
            current_question["question_type"],
        )

        result = Text()
        result.append(self._score_gauge_text(current_score), style="green")
        result.append(f"  {current_score}점\n\n", style="bold yellow")
        result.append(current_feedback)

        self.query_one("#iq-results-title", Label).update(
            f"채점 결과  [{self.current_index + 1}/{len(self.questions)}]  {type_ko}"
        )
        self.query_one("#iq-result-content", Static).update(result)
        self.query_one("#iq-answering-group", Vertical).display = True
        self.query_one("#iq-results-group", Vertical).display = True
        self.query_one("#iq-answer-input", TextArea).disabled = True
        self._set_status(f"채점 완료! 평균 {avg_score}점.")


class InlineQuizDock(InlineQuizWidget):
    DEFAULT_CSS = (
        """
    InlineQuizDock {
        display: none;
        layer: overlay;
        position: absolute;
        width: 100%;
        height: 1fr;
        border: round #b88a3b;
        padding: 0 1;
        offset: 0 3;
        background: $surface;
    }
    """
        + INLINE_QUIZ_SHARED_CSS
    )

    def on_mount(self) -> None:
        self._animate_timer = self.set_interval(0.35, self._tick_animation, pause=True)
        self.display = False
        self._set_status("Inline Quiz를 열어주세요.")


class SessionInlineQuizView(InlineQuizWidget):
    DEFAULT_CSS = (
        """
    SessionInlineQuizView {
        display: block;
        position: relative;
        width: 1fr;
        height: 1fr;
        min-height: 12;
        border: none;
        padding: 0;
        background: transparent;
    }

    SessionInlineQuizView #iq-close {
        display: none;
    }

    SessionInlineQuizView #iq-header {
        display: none;
    }
    """
        + INLINE_QUIZ_SHARED_CSS
    )

    def on_mount(self) -> None:
        self._animate_timer = self.set_interval(0.35, self._tick_animation, pause=True)
        self.display = True
        self.styles.display = "block"
        self.show_placeholder("Open Inline을 누르면 여기서 인라인 퀴즈를 생성할 수 있습니다.")

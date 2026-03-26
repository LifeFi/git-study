"""Inline Quiz Dock — 소스 코드 특정 위치에 앵커된 퀴즈."""

from typing import TypedDict

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Label, Static, TextArea

from ..graph import (
    InlineQuizGrade,
    InlineQuizQuestion,
    build_inline_quiz_questions,
    detect_code_language,
    get_file_content_at_commit_or_empty,
    grade_inline_answers,
)
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
    """anchor_snippet이 파일 내 몇 번째 줄에서 시작하는지 반환 (1-based)."""
    file_lines = file_content.splitlines()
    snippet_lines = [line for line in anchor_snippet.strip().splitlines() if line.strip()]
    if not snippet_lines:
        return None

    first = snippet_lines[0].strip()
    for index, file_line in enumerate(file_lines):
        if first not in file_line.strip() and not file_line.strip().startswith(first[:30]):
            continue
        matched = True
        for offset, snippet_line in enumerate(snippet_lines[1:], 1):
            if index + offset >= len(file_lines):
                matched = False
                break
            if snippet_line.strip() not in file_lines[index + offset]:
                matched = False
                break
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


class InlineQuizDock(Vertical):
    DEFAULT_CSS = """
    InlineQuizDock {
        display: none;
        layer: overlay;
        position: absolute;
        width: 100%;
        height: 100%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }

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

    #iq-header-spacer {
        width: 1fr;
    }

    #iq-body {
        height: 1fr;
    }

    #iq-code-panel,
    #iq-quiz-panel {
        border: round $accent;
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
        height: 1fr;
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

    #iq-grade-btn {
        width: auto;
        min-width: 8;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        tint: transparent;
    }

    #iq-grade-btn:hover,
    #iq-grade-btn:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #iq-results-group {
        height: 1fr;
        display: none;
    }

    #iq-results-title {
        height: auto;
        color: $accent;
        text-style: bold;
        margin: 1 0;
    }

    #iq-result-scroll {
        height: 1fr;
        border: round $panel;
    }

    #iq-result-content {
        width: 1fr;
        padding: 1;
    }

    #iq-status-bar {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
    """

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
        self.questions: list[InlineQuizQuestion] = []
        self.answers: dict[str, str] = {}
        self.grades: list[InlineQuizGrade] = []
        self.current_index = 0
        self._known_files: dict[str, str] = {}
        self._resolved_paths: dict[str, str] = {}
        self._anchor_cache: dict[str, int | None] = {}
        self._state = "idle"
        self._anim_frame = 0
        self._nav_build_serial = 0
        self._animate_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="iq-header"):
            yield Label("Inline Quiz", id="iq-title")
            yield Static("", id="iq-header-spacer")
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
                    yield TextArea("", id="iq-answer-input")
                    with Horizontal(id="iq-nav-row"):
                        yield Button(
                            "◀ 이전",
                            id="iq-prev",
                            classes="iq-nav-btn",
                            compact=True,
                            flat=True,
                        )
                        yield Static("", id="iq-nav-spacer")
                        yield Button(
                            "다음 ▶",
                            id="iq-next",
                            classes="iq-nav-btn",
                            compact=True,
                            flat=True,
                        )
                        yield Button(
                            "채점하기",
                            id="iq-grade-btn",
                            compact=True,
                            flat=True,
                        )
                with Vertical(id="iq-results-group"):
                    yield Label("채점 결과", id="iq-results-title")
                    with VerticalScroll(id="iq-result-scroll"):
                        yield Static("", id="iq-result-content")
        yield Static("Inline Quiz를 열어주세요.", id="iq-status-bar")

    def on_mount(self) -> None:
        self._animate_timer = self.set_interval(0.35, self._tick_animation, pause=True)
        self.display = False
        self._set_status("Inline Quiz를 열어주세요.")

    def show_quiz(
        self,
        *,
        commit_context: dict,
        repo,
        target_commit_sha: str,
        title_suffix: str = "",
        saved_state: InlineQuizSavedState | None = None,
        cache_key: str = "",
    ) -> None:
        self.display = True
        self.styles.display = "block"
        self.commit_context = commit_context
        self.repo = repo
        self.target_commit_sha = target_commit_sha
        self.cache_key = cache_key or target_commit_sha
        self._reset_ui()

        title = f"Inline Quiz  {title_suffix}".rstrip()
        self.query_one("#iq-title", Label).update(title)
        if saved_state is not None:
            self._restore_state(saved_state)
        else:
            self._state = "loading"
            self._anim_frame = 0
            if self._animate_timer is not None:
                self._animate_timer.reset()
                self._animate_timer.resume()
            self._tick_animation()
            self._generate_questions()

    def hide_panel(self) -> None:
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
        self.display = True
        self.styles.display = "block"
        self.questions = []
        self.answers = {}
        self.grades = []
        self.current_index = 0
        self._state = "idle"
        self._anim_frame = 0
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._nav_build_serial += 1
        self.query_one("#iq-title", Label).update("Inline Quiz")
        self.query_one("#iq-q-nav", Horizontal).remove_children()
        self.query_one("#iq-q-type-badge", Label).update("")
        self.query_one("#iq-question-text", Static).update(message)
        self.query_one("#iq-answer-input", TextArea).text = ""
        self.query_one("#iq-answer-input", TextArea).blur()
        self.query_one("#iq-answering-group", Vertical).display = True
        self.query_one("#iq-results-group", Vertical).display = False
        self.query_one("#iq-code-file-label", Label).update("선택된 커밋 없음")
        self.query_one("#iq-code-content", Static).update(
            Text("커밋을 선택하면 이 영역에 앵커된 코드가 표시됩니다.", style="dim")
        )
        self.query_one("#iq-grade-btn", Button).disabled = True
        self._set_status(message)

    def _reset_ui(self) -> None:
        self.questions = []
        self.answers = {}
        self.grades = []
        self.current_index = 0
        self._known_files = {}
        self._resolved_paths = {}
        self._anchor_cache = {}
        self._state = "idle"
        self._anim_frame = 0
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._nav_build_serial += 1
        self.query_one("#iq-q-nav", Horizontal).remove_children()
        self.query_one("#iq-q-type-badge", Label).update("")
        self.query_one("#iq-question-text", Static).update("질문 생성 중.")
        self.query_one("#iq-answer-input", TextArea).text = ""
        self.query_one("#iq-answering-group", Vertical).display = True
        self.query_one("#iq-results-group", Vertical).display = False
        self.query_one("#iq-code-file-label", Label).update("파일 로딩 중.")
        self.query_one("#iq-code-content", Static).update("")
        self.query_one("#iq-grade-btn", Button).disabled = False
        self._set_status("질문을 생성하는 중.")

    def _restore_state(self, saved: InlineQuizSavedState) -> None:
        self._known_files = saved["known_files"]
        self._on_questions_loaded(saved["questions"])
        self.answers = saved["answers"]
        self.current_index = saved["current_index"]
        if saved["grades"]:
            self.grades = saved["grades"]
            self._show_results()
        else:
            self._update_question_panel()
            self._update_code_panel()

    def _tick_animation(self) -> None:
        if self._state not in {"loading", "grading"} or not self.display:
            return
        dots = "." * ((self._anim_frame % 3) + 1)
        if self._state == "loading":
            self._set_status(f"질문 생성 중{dots}")
            self.query_one("#iq-question-text", Static).update(f"질문 생성 중{dots}")
        elif self._state == "grading":
            self._set_status(f"채점 중{dots}")
        self._anim_frame += 1

    def _set_status(self, text: str) -> None:
        self.query_one("#iq-status-bar", Static).update(text)

    def _preload_known_files(self) -> None:
        import re as _re

        from ..graph import _extract_file_paths_from_summary

        file_context_text = self.commit_context.get("file_context_text", "")

        for match in _re.finditer(
            r"FILE:\s+(.+?)\n```[^\n]*\n([\s\S]*?)```",
            file_context_text,
        ):
            path = match.group(1).strip()
            snippet = match.group(2)
            if path and snippet.strip():
                self._known_files[path] = snippet

        all_paths: set[str] = set(self._known_files.keys())
        for path in _extract_file_paths_from_summary(
            self.commit_context.get("changed_files_summary", "")
        ):
            all_paths.add(path)

        parent_sha: str | None = None
        try:
            commit = self.repo.commit(self.target_commit_sha)
            if commit.parents:
                parent_sha = commit.parents[0].hexsha
        except Exception:
            pass

        for path in all_paths:
            full = get_file_content_at_commit_or_empty(
                self.repo, self.target_commit_sha, path
            )
            if full:
                self._known_files[path] = full
            elif parent_sha:
                parent_full = get_file_content_at_commit_or_empty(
                    self.repo, parent_sha, path
                )
                if parent_full:
                    self._known_files[path] = parent_full

    @work(thread=True)
    def _generate_questions(self) -> None:
        try:
            self._preload_known_files()
            questions = build_inline_quiz_questions(self.commit_context)
            self.app.call_from_thread(self._on_questions_loaded, questions)
        except Exception as exc:
            self.app.call_from_thread(self._on_questions_failed, str(exc))

    def _on_questions_loaded(self, questions: list[InlineQuizQuestion]) -> None:
        self.questions = questions
        self._state = "answering"
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._build_q_nav()
        self._update_question_panel()
        self._update_code_panel()
        loaded_paths = ", ".join(self._known_files.keys()) or "없음"
        self._set_status(
            f"질문 {len(questions)}개 생성됨  |  로드된 파일: {loaded_paths}"
        )

    def _on_questions_failed(self, error: str) -> None:
        self._state = "answering"
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._set_status(f"질문 생성 실패: {error[:100]}")
        self.query_one("#iq-question-text", Static).update(
            f"질문 생성에 실패했습니다.\n\n{error[:300]}"
        )

    def _build_q_nav(self) -> None:
        nav = self.query_one("#iq-q-nav", Horizontal)
        nav.remove_children()
        self._nav_build_serial += 1
        for index, question in enumerate(self.questions):
            classes = "iq-q-btn" + (" iq-active" if index == self.current_index else "")
            nav.mount(
                Button(
                    str(index + 1),
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
            button.remove_class("iq-active", "iq-answered")
            if index == self.current_index:
                button.add_class("iq-active")
            elif self.answers.get(question["id"], "").strip():
                button.add_class("iq-answered")

    def _resolve_to_known(self, file_path: str, anchor_snippet: str) -> str:
        if file_path in self._resolved_paths:
            return self._resolved_paths[file_path]

        if file_path in self._known_files:
            self._resolved_paths[file_path] = file_path
            return file_path

        for known_path in self._known_files:
            if known_path.endswith(file_path) or file_path.endswith(known_path):
                self._resolved_paths[file_path] = known_path
                return known_path
            if known_path.split("/")[-1] == file_path.split("/")[-1]:
                self._resolved_paths[file_path] = known_path
                return known_path

        for known_path, content in self._known_files.items():
            if find_anchor_line(content, anchor_snippet):
                self._resolved_paths[file_path] = known_path
                return known_path

        self._resolved_paths[file_path] = file_path
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

    def _update_question_panel(self) -> None:
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

    def _navigate_to(self, index: int) -> None:
        self._save_current_answer()
        self.current_index = max(0, min(index, len(self.questions) - 1))
        self._update_question_panel()
        self._update_code_panel()

    @on(Button.Pressed, "#iq-prev")
    def handle_prev(self) -> None:
        self._navigate_to(self.current_index - 1)

    @on(Button.Pressed, "#iq-next")
    def handle_next(self) -> None:
        self._navigate_to(self.current_index + 1)

    @on(Button.Pressed, "#iq-grade-btn")
    def handle_grade(self) -> None:
        self._save_current_answer()
        self._state = "grading"
        self._anim_frame = 0
        if self._animate_timer is not None:
            self._animate_timer.reset()
            self._animate_timer.resume()
        self.query_one("#iq-grade-btn", Button).disabled = True
        self._do_grade()

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
        try:
            grades = grade_inline_answers(self.questions, self.answers)
            self.app.call_from_thread(self._on_grades_loaded, grades)
        except Exception as exc:
            self.app.call_from_thread(self._on_grades_failed, str(exc))

    def _on_grades_loaded(self, grades: list[InlineQuizGrade]) -> None:
        self.grades = grades
        self._state = "results"
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self._show_results()

    def _on_grades_failed(self, error: str) -> None:
        self._state = "answering"
        if self._animate_timer is not None:
            self._animate_timer.pause()
        self.query_one("#iq-grade-btn", Button).disabled = False
        self._set_status(f"채점 실패: {error[:100]}")

    def _show_results(self) -> None:
        grade_map = {grade["id"]: grade for grade in self.grades}
        avg_score = (
            sum(grade["score"] for grade in self.grades) // len(self.grades)
            if self.grades
            else 0
        )

        result = Text()
        result.append(f"평균 점수: {avg_score}점\n\n", style="bold bright_cyan")

        for question in self.questions:
            grade = grade_map.get(question["id"])
            score = grade["score"] if grade else 0
            feedback = grade["feedback"] if grade else "-"
            bar = "█" * (score // 10) + "░" * (10 - score // 10)
            type_ko = QUESTION_TYPE_KO.get(
                question["question_type"],
                question["question_type"],
            )

            result.append(f"{question['id'].upper()}. [{type_ko}]\n", style="bold")
            result.append(f"{question['question'][:80]}\n", style="dim")
            result.append(bar, style="green")
            result.append(f"  {score}점\n", style="bold yellow")
            result.append(f"{feedback}\n\n")

        self.query_one("#iq-result-content", Static).update(result)
        self.query_one("#iq-answering-group", Vertical).display = False
        self.query_one("#iq-results-group", Vertical).display = True
        self._set_status(f"채점 완료! 평균 {avg_score}점.")

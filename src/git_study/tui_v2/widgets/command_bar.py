"""CommandBar widget: status line + input for commands / answers."""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static, TextArea

# (command, description) — autocomplete candidates
_COMMANDS: list[tuple[str, str]] = [
    ("/commits", "커밋 범위 선택"),
    ("/quiz", "퀴즈 생성 (현재 범위)"),
    ("/quiz HEAD", "HEAD 커밋 퀴즈"),
    ("/quiz HEAD~3", "최근 4개 커밋 퀴즈"),
    ("/quiz HEAD~1..HEAD~4", "범위 지정 퀴즈"),
    ("/review", "커밋 해설 보기 (현재 범위)"),
    ("/review HEAD", "HEAD 커밋 해설"),
    ("/review HEAD~3", "최근 4개 커밋 해설"),
    ("/grade", "채점"),
    ("/answer", "답변 재진입"),
    ("/clear", "대화 초기화 (이전 대화는 /resume 으로 복원)"),
    ("/resume", "이전 대화 불러오기"),
    ("/repo", "저장소 전환 (URL 또는 경로)"),
    ("/apikey", "OpenAI API key 설정"),
    ("/help", "도움말"),
    ("/exit", "종료"),
]


def _filter_candidates(text: str) -> list[tuple[str, str]]:
    if not text or not text.startswith("/"):
        return []
    lower = text.lower()
    return [(cmd, desc) for cmd, desc in _COMMANDS if cmd.startswith(lower)]


class CommandBar(Widget):
    """Bottom bar: status line + command input (command mode) or textarea (answer mode)."""

    DEFAULT_CSS = """
    CommandBar {
        height: auto;
        max-height: 14;
        background: transparent;
        layout: vertical;
    }

    CommandBar #cb-status {
        height: 1;
        width: 1fr;
        padding: 0 1;
        color: $text-muted;
    }

    CommandBar #cb-status.-answer-mode {
        color: $text-muted;
    }

    CommandBar #cb-input-row {
        height: 2;
        align: left middle;
        border-top: solid $panel-lighten-1;
    }

    CommandBar #cb-answer-row {
        height: auto;
        align: left top;
        display: none;
        border-top: solid $panel-lighten-1;
    }

    CommandBar #cb-prompt {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text;
        content-align: left middle;
    }

    CommandBar #cb-answer-prompt {
        width: auto;
        padding: 0 1;
        color: $success;
        content-align: left top;
    }

    CommandBar #cb-input {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }

    CommandBar #cb-answer {
        height: auto;
        min-height: 3;
        max-height: 8;
        width: 1fr;
        border: none;
        padding: 0 0 0 1;
        background: transparent;
    }

    CommandBar #cb-autocomplete {
        height: auto;
        max-height: 5;
        display: none;
        background: transparent;
    }

    CommandBar #cb-ac-list {
        height: auto;
        padding: 0 0;
    }
    """

    BINDINGS = [
        Binding("tab", "tab_pressed", priority=True),
        Binding("shift+up", "prev_question", priority=True),
        Binding("shift+down", "next_question", priority=True),
    ]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class CommandSubmitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class AnswerSubmitted(Message):
        def __init__(self, answer: str) -> None:
            super().__init__()
            self.answer = answer

    class AnswerExited(Message):
        pass

    class PrevQuestion(Message):
        pass

    class NextQuestion(Message):
        pass

    # ------------------------------------------------------------------
    # Reactive state
    # ------------------------------------------------------------------

    mode: reactive[str] = reactive("command")
    status_text: reactive[str] = reactive("명령어를 입력하세요: /quiz, /grade, /help")

    # ------------------------------------------------------------------
    # Internal state
    # ------------------------------------------------------------------

    _history: list[str]
    _history_index: int
    _history_draft: str
    _ac_candidates: list[tuple[str, str]]
    _ac_index: int
    _showing_help: bool

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._history = []
        self._history_index = -1
        self._history_draft = ""
        self._ac_candidates = []
        self._ac_index = -1
        self._showing_help = False

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self.status_text, id="cb-status")
        with Horizontal(id="cb-input-row"):
            yield Static(">", id="cb-prompt")
            yield Input(placeholder="/quiz HEAD~3", id="cb-input")
        with Horizontal(id="cb-answer-row"):
            yield Static("A", id="cb-answer-prompt")

    # ------------------------------------------------------------------
    # Watchers
    # ------------------------------------------------------------------

    def watch_status_text(self, value: str) -> None:
        try:
            self.query_one("#cb-status", Static).update(value)
        except Exception:
            pass

    def watch_mode(self, value: str) -> None:
        try:
            status = self.query_one("#cb-status", Static)
            input_row = self.query_one("#cb-input-row", Horizontal)
            answer_row = self.query_one("#cb-answer-row", Horizontal)
            if value == "answer":
                status.add_class("-answer-mode")
                input_row.display = False
                answer_row.display = True
            else:
                status.remove_class("-answer-mode")
                input_row.display = True
                answer_row.display = False
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_answer_mode(self, hint: str) -> None:
        self.status_text = hint
        self.mode = "answer"
        # TextArea를 answer 진입 시점에 동적으로 마운트 (시작 시 IME 트리거 방지)
        try:
            self.query_one("#cb-answer", TextArea)
        except Exception:
            text_area = TextArea(id="cb-answer")
            answer_row = self.query_one("#cb-answer-row", Horizontal)
            answer_row.mount(text_area)
            self.app.call_after_refresh(text_area.focus)

    def set_command_mode(self) -> None:
        self.mode = "command"
        self.status_text = "명령어를 입력하세요: /quiz, /grade, /help"
        # TextArea 제거
        try:
            self.query_one("#cb-answer", TextArea).remove()
        except Exception:
            pass

    def get_current_answer(self) -> str:
        if self.mode == "answer":
            try:
                return self.query_one("#cb-answer", TextArea).text
            except Exception:
                return ""
        return self.query_one("#cb-input", Input).value

    def clear_input(self) -> None:
        if self.mode == "answer":
            try:
                self.query_one("#cb-answer", TextArea).clear()
            except Exception:
                pass
        else:
            self.query_one("#cb-input", Input).value = ""

    def focus_input(self) -> None:
        if self.mode == "answer":
            try:
                self.query_one("#cb-answer", TextArea).focus()
            except Exception:
                pass
        else:
            self.query_one("#cb-input", Input).focus()

    def action_prev_question(self) -> None:
        self.post_message(self.PrevQuestion())

    def action_next_question(self) -> None:
        self.post_message(self.NextQuestion())

    def action_tab_pressed(self) -> None:
        if self.mode == "command" and self._ac_candidates and 0 <= self._ac_index < len(self._ac_candidates):
            cmd, _ = self._ac_candidates[self._ac_index]
            self._close_autocomplete()
            inp = self.query_one("#cb-input", Input)
            inp.value = cmd
            inp.cursor_position = len(cmd)
        else:
            try:
                self.app.handle_tab_no_autocomplete()
            except Exception:
                self.app.action_focus_next()

    # ------------------------------------------------------------------
    # Autocomplete helpers
    # ------------------------------------------------------------------

    def _open_autocomplete(self, candidates: list[tuple[str, str]], index: int) -> None:
        self._ac_candidates = candidates
        self._ac_index = index
        self._render_autocomplete()
        try:
            self.app.query_one("#cb-autocomplete").display = True
            self.app.query_one("#mode-bar").display = False
            self.app.query_one("#bottom-pad").display = False
        except Exception:
            pass

    def _close_autocomplete(self) -> None:
        self._ac_candidates = []
        self._ac_index = -1
        try:
            self.app.query_one("#cb-autocomplete").display = False
            self.app.query_one("#mode-bar").display = True
            self.app.query_one("#bottom-pad").display = True
        except Exception:
            pass

    def show_help_panel(self, lines: list[tuple[str, str]]) -> None:
        """Show help text in the autocomplete panel area."""
        self._ac_candidates = []
        self._ac_index = -1
        self._showing_help = True
        try:
            t = Text()
            for i, (cmd, desc) in enumerate(lines):
                t.append(f"   {cmd:<25}", style="bold")
                t.append(f" {desc}", style="dim")
                if i < len(lines) - 1:
                    t.append("\n")
            self.app.query_one("#cb-ac-list", Static).update(t)
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = 10
            ac.display = True
            self.app.query_one("#mode-bar").display = False
            self.app.query_one("#bottom-pad").display = False
        except Exception:
            pass

    def _close_help_panel(self) -> None:
        self._showing_help = False
        try:
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = 5
            ac.display = False
            self.app.query_one("#mode-bar").display = True
            self.app.query_one("#bottom-pad").display = True
        except Exception:
            pass

    def _render_autocomplete(self) -> None:
        try:
            t = Text()
            for i, (cmd, desc) in enumerate(self._ac_candidates):
                if i == self._ac_index:
                    t.append(f"   {cmd:<20}", style="bold color(99)")
                    t.append(f" {desc}", style="bold color(99)")
                else:
                    t.append(f"   {cmd:<20}", style="dim")
                    t.append(f" {desc}", style="dim")
                if i < len(self._ac_candidates) - 1:
                    t.append("\n")
            self.app.query_one("#cb-ac-list", Static).update(t)
            try:
                scroll = self.app.query_one("#cb-autocomplete")
                visible_height = 5
                current_top = int(scroll.scroll_y)
                if self._ac_index >= current_top + visible_height:
                    scroll.scroll_to(y=self._ac_index - visible_height + 1, animate=False)
                elif self._ac_index < current_top:
                    scroll.scroll_to(y=self._ac_index, animate=False)
            except Exception:
                pass
        except Exception:
            pass

    def _ac_select(self) -> None:
        if 0 <= self._ac_index < len(self._ac_candidates):
            cmd, _ = self._ac_candidates[self._ac_index]
            self._close_autocomplete()
            text = cmd.strip()
            if not text.startswith("/") and (not self._history or self._history[-1] != text):
                self._history.append(text)
            self._history_index = -1
            self._history_draft = ""
            self.query_one("#cb-input", Input).value = ""
            self.post_message(self.CommandSubmitted(text))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.mode != "command":
            return
        if self._showing_help:
            if event.value:
                self._close_help_panel()
            else:
                return
        candidates = _filter_candidates(event.value)
        if candidates:
            self._open_autocomplete(candidates, 0)
        else:
            self._close_autocomplete()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.mode == "command":
            if self._ac_candidates:
                self._ac_select()
                event.stop()
                return
            text = event.value.strip()
            if text:
                if not text.startswith("/") and (not self._history or self._history[-1] != text):
                    self._history.append(text)
                self._history_index = -1
                self._history_draft = ""
                self.clear_input()
                self.post_message(self.CommandSubmitted(text))
            event.stop()

    def on_key(self, event: Key) -> None:
        if self.mode == "command":
            if event.key == "up":
                if self._ac_candidates:
                    self._ac_index = max(0, self._ac_index - 1)
                    self._render_autocomplete()
                else:
                    self._history_prev()
                event.stop()
                event.prevent_default()
            elif event.key == "down":
                if self._ac_candidates:
                    self._ac_index = min(len(self._ac_candidates) - 1, self._ac_index + 1)
                    self._render_autocomplete()
                else:
                    self._history_next()
                event.stop()
                event.prevent_default()
            elif event.key == "escape":
                if self._showing_help:
                    self._close_help_panel()
                    event.stop()
                    event.prevent_default()
                elif self._ac_candidates:
                    self._close_autocomplete()
                    event.stop()
                    event.prevent_default()
        elif self.mode == "answer":
            if event.key == "shift+enter":
                answer = self.get_current_answer().strip()
                if answer:
                    self.clear_input()
                    self.post_message(self.AnswerSubmitted(answer))
                event.stop()
                event.prevent_default()
            elif event.key == "escape":
                self.set_command_mode()
                self.post_message(self.AnswerExited())
                event.stop()
                event.prevent_default()

    # ------------------------------------------------------------------
    # History navigation
    # ------------------------------------------------------------------

    def _history_prev(self) -> None:
        if not self._history:
            return
        inp = self.query_one("#cb-input", Input)
        if self._history_index == -1:
            self._history_draft = inp.value
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        inp.value = self._history[self._history_index]
        inp.cursor_position = len(inp.value)

    def _history_next(self) -> None:
        if self._history_index == -1:
            return
        inp = self.query_one("#cb-input", Input)
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            inp.value = self._history[self._history_index]
        else:
            self._history_index = -1
            inp.value = self._history_draft
            self._history_draft = ""
        inp.cursor_position = len(inp.value)

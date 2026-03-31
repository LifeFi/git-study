"""CommandBar widget: status line + input for commands / answers."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static, TextArea

# (command, description) — autocomplete candidates
_COMMANDS: list[tuple[str, str]] = [
    ("/quiz", "퀴즈 생성 (현재 범위)"),
    ("/quiz HEAD", "HEAD 커밋 퀴즈"),
    ("/quiz HEAD~3", "최근 4개 커밋 퀴즈"),
    ("/grade", "채점"),
    ("/commits", "커밋 범위 선택"),
    ("/answer", "답변 재진입"),
    ("/help", "도움말"),
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
        height: 3;
        dock: bottom;
        margin-bottom: 3;
        background: transparent;
        layout: vertical;
    }

    CommandBar #cb-autocomplete {
        height: auto;
        display: none;
        background: $panel;
        layout: vertical;
    }

    CommandBar #cb-ac-list {
        height: auto;
        padding: 0 1;
    }

    CommandBar #cb-status {
        height: 1;
        width: 1fr;
        padding: 0 1;
        color: $text-muted;
    }

    CommandBar #cb-status.-answer-mode {
        color: $success;
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
    """

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._history = []
        self._history_index = -1
        self._history_draft = ""
        self._ac_candidates = []
        self._ac_index = -1

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="cb-autocomplete"):
            yield Static("", id="cb-ac-list")
        yield Static(self.status_text, id="cb-status")
        with Horizontal(id="cb-input-row"):
            yield Static(">", id="cb-prompt")
            yield Input(placeholder="/quiz HEAD~3", id="cb-input")
        with Horizontal(id="cb-answer-row"):
            yield Static(">", id="cb-answer-prompt")
            yield TextArea(id="cb-answer")

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
                self.styles.height = "auto"
            else:
                status.remove_class("-answer-mode")
                input_row.display = True
                answer_row.display = False
                self.styles.height = 3
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_answer_mode(self, hint: str) -> None:
        self.status_text = hint
        self.mode = "answer"

    def set_command_mode(self) -> None:
        self.mode = "command"
        self.status_text = "명령어를 입력하세요: /quiz, /grade, /help"

    def get_current_answer(self) -> str:
        if self.mode == "answer":
            return self.query_one("#cb-answer", TextArea).text
        return self.query_one("#cb-input", Input).value

    def clear_input(self) -> None:
        if self.mode == "answer":
            self.query_one("#cb-answer", TextArea).clear()
        else:
            self.query_one("#cb-input", Input).value = ""

    def focus_input(self) -> None:
        if self.mode == "answer":
            self.query_one("#cb-answer", TextArea).focus()
        else:
            self.query_one("#cb-input", Input).focus()

    # ------------------------------------------------------------------
    # Autocomplete helpers
    # ------------------------------------------------------------------

    def _open_autocomplete(self, candidates: list[tuple[str, str]], index: int) -> None:
        self._ac_candidates = candidates
        self._ac_index = index
        self._render_autocomplete()
        self.query_one("#cb-autocomplete", Vertical).display = True
        self.styles.height = "auto"

    def _close_autocomplete(self) -> None:
        self._ac_candidates = []
        self._ac_index = -1
        try:
            self.query_one("#cb-autocomplete", Vertical).display = False
            if self.mode == "command":
                self.styles.height = 3
        except Exception:
            pass

    def _render_autocomplete(self) -> None:
        try:
            t = Text()
            for i, (cmd, desc) in enumerate(self._ac_candidates):
                if i == self._ac_index:
                    t.append(f" ▶ {cmd:<20}", style="bold reverse")
                    t.append(f" {desc}", style="bold reverse")
                else:
                    t.append(f"   {cmd:<20}", style="dim")
                    t.append(f" {desc}", style="dim")
                if i < len(self._ac_candidates) - 1:
                    t.append("\n")
            self.query_one("#cb-ac-list", Static).update(t)
        except Exception:
            pass

    def _ac_select(self) -> None:
        if 0 <= self._ac_index < len(self._ac_candidates):
            cmd, _ = self._ac_candidates[self._ac_index]
            self._close_autocomplete()
            text = cmd.strip()
            if not self._history or self._history[-1] != text:
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
                if not self._history or self._history[-1] != text:
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
                if self._ac_candidates:
                    self._close_autocomplete()
                    event.stop()
                    event.prevent_default()
            elif event.key == "tab":
                if self._ac_candidates and 0 <= self._ac_index < len(self._ac_candidates):
                    cmd, _ = self._ac_candidates[self._ac_index]
                    self._close_autocomplete()
                    inp = self.query_one("#cb-input", Input)
                    inp.value = cmd
                    inp.cursor_position = len(cmd)
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

"""CommandBar widget: status line + input for commands / answers."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static


class CommandBar(Widget):
    """Two-row bar at the bottom: status line + text input."""

    DEFAULT_CSS = """
    CommandBar {
        height: 3;
        dock: bottom;
        background: $surface;
    }

    CommandBar #cb-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $boost;
    }

    CommandBar #cb-status.-answer-mode {
        color: $success;
    }

    CommandBar #cb-input-row {
        height: 2;
        align: left middle;
    }

    CommandBar #cb-prompt {
        width: auto;
        height: 2;
        padding: 0 1;
        color: $text;
        content-align: left top;
    }

    CommandBar #cb-input {
        height: 2;
        width: 1fr;
        border: none;
        padding: 0;
        content-align: left top;
    }
    """

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class CommandSubmitted(Message):
        """Fired when user presses Enter in command mode."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class AnswerSubmitted(Message):
        """Fired when user presses Shift+Enter in answer mode."""

        def __init__(self, answer: str) -> None:
            super().__init__()
            self.answer = answer

    class AnswerExited(Message):
        """Fired when user presses Esc in answer mode."""

    # ------------------------------------------------------------------
    # Reactive state
    # ------------------------------------------------------------------

    mode: reactive[str] = reactive("command")
    status_text: reactive[str] = reactive("명령어를 입력하세요: /quiz, /grade, /help")

    # ------------------------------------------------------------------
    # History state
    # ------------------------------------------------------------------

    _history: list[str]
    _history_index: int  # -1 = not browsing history
    _history_draft: str  # saves current input when starting history browse

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._history = []
        self._history_index = -1
        self._history_draft = ""

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self.status_text, id="cb-status")
        with Horizontal(id="cb-input-row"):
            yield Static(">", id="cb-prompt")
            yield Input(placeholder="/quiz HEAD~3", id="cb-input")

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
            inp = self.query_one("#cb-input", Input)
            if value == "answer":
                status.add_class("-answer-mode")
                inp.placeholder = "답변을 입력하세요 (Shift+Enter 제출, Esc 종료)"
            else:
                status.remove_class("-answer-mode")
                inp.placeholder = "/quiz HEAD~3"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_answer_mode(self, hint: str) -> None:
        """Switch to answer mode with a status hint."""
        self.mode = "answer"
        self.status_text = hint

    def set_command_mode(self) -> None:
        """Switch back to command mode."""
        self.mode = "command"
        self.status_text = "명령어를 입력하세요: /quiz, /grade, /help"

    def get_current_answer(self) -> str:
        """Return the current text in the input widget."""
        return self.query_one("#cb-input", Input).value

    def clear_input(self) -> None:
        self.query_one("#cb-input", Input).value = ""

    def focus_input(self) -> None:
        self.query_one("#cb-input", Input).focus()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter key in command mode -> fire CommandSubmitted."""
        if self.mode == "command":
            text = event.value.strip()
            if text:
                # Add to history (avoid consecutive duplicates)
                if not self._history or self._history[-1] != text:
                    self._history.append(text)
                self._history_index = -1
                self._history_draft = ""
                self.clear_input()
                self.post_message(self.CommandSubmitted(text))
            event.stop()

    def on_key(self, event: Key) -> None:
        if self.mode == "command" and event.key == "up":
            self._history_prev()
            event.stop()
            event.prevent_default()
        elif self.mode == "command" and event.key == "down":
            self._history_next()
            event.stop()
            event.prevent_default()
        elif event.key == "shift+enter" and self.mode == "answer":
            answer = self.get_current_answer().strip()
            if answer:
                self.clear_input()
                self.post_message(self.AnswerSubmitted(answer))
            event.stop()
            event.prevent_default()
        elif event.key == "escape" and self.mode == "answer":
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

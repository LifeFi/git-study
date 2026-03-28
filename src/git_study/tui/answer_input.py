from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea


class AnswerTextArea(TextArea):
    class Submit(Message):
        def __init__(self, control: "AnswerTextArea") -> None:
            self.answer_input = control
            super().__init__()

    class NavigateNext(Message):
        def __init__(self, control: "AnswerTextArea") -> None:
            self.answer_input = control
            super().__init__()

    class NavigatePrevious(Message):
        def __init__(self, control: "AnswerTextArea") -> None:
            self.answer_input = control
            super().__init__()

    def on_key(self, event: Key) -> None:
        key = event.key
        if key == "enter":
            return
        if key == "shift+enter":
            event.stop()
            self.blur()
            self.post_message(self.Submit(self))
            return
        if key in {
            "ctrl+enter",
            "meta+enter",
            "cmd+enter",
        }:
            event.stop()
            self.post_message(self.NavigateNext(self))
            return
        if key in {
            "ctrl+shift+enter",
            "shift+ctrl+enter",
            "meta+shift+enter",
            "shift+meta+enter",
            "cmd+shift+enter",
            "shift+cmd+enter",
        }:
            event.stop()
            self.post_message(self.NavigatePrevious(self))

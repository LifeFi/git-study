import time
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Markdown, MarkdownViewer, Static
from textual.widgets._markdown import MarkdownFence, MarkdownTableOfContents


class LabeledMarkdownFence(MarkdownFence):
    DEFAULT_CSS = """
    LabeledMarkdownFence {
        padding: 0;
        margin: 1 0;
        overflow: scroll hidden;
        scrollbar-size-horizontal: 0;
        scrollbar-size-vertical: 0;
        width: 1fr;
        height: auto;
        color: rgb(210,210,210);
        background: black 10%;
        &:light {
            background: white 30%;
        }
    }

    LabeledMarkdownFence > #code-language {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $panel-darken-1;
        text-style: bold;
    }

    LabeledMarkdownFence > #code-content {
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(self.lexer or "text", id="code-language")
        yield Label(self._highlighted_code, id="code-content")

    async def _update_from_block(self, block):
        await super()._update_from_block(block)
        self.query_one("#code-language", Label).update(self.lexer or "text")


class LabeledMarkdown(Markdown):
    BLOCKS = {
        **Markdown.BLOCKS,
        "fence": LabeledMarkdownFence,
        "code_block": LabeledMarkdownFence,
    }


class LabeledMarkdownViewer(MarkdownViewer):
    def compose(self) -> ComposeResult:
        markdown = LabeledMarkdown(
            parser_factory=self._parser_factory,
            open_links=self._open_links,
        )
        markdown.can_focus = True
        yield markdown
        yield MarkdownTableOfContents(markdown)


class ResultLoadScreen(ModalScreen[Path | None]):
    CSS = """
    #load-dialog {
        width: 72;
        max-width: 90%;
        height: 22;
        max-height: 80%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 4 0;
    }

    #load-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #load-help {
        color: $text-muted;
        margin-bottom: 1;
    }

    #load-file-list {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, files: list[Path]) -> None:
        super().__init__()
        self.files = files

    def compose(self) -> ComposeResult:
        with Vertical(id="load-dialog"):
            yield Label("Load Quiz File", id="load-title")
            yield Static("Enter 또는 Space로 선택, Esc로 닫기", id="load-help")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            f"{path.name}  ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(path.stat().st_mtime))})"
                        )
                    )
                    for path in self.files
                ],
                id="load-file-list",
            )

    def on_mount(self) -> None:
        file_list = self.query_one("#load-file-list", ListView)
        if self.files:
            file_list.index = 0
        file_list.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_file(self) -> Path | None:
        file_list = self.query_one("#load-file-list", ListView)
        index = file_list.index
        if index is None or not (0 <= index < len(self.files)):
            return None
        return self.files[index]

    def on_key(self, event: Key) -> None:
        if event.key == "space" and self.focused is self.query_one(
            "#load-file-list", ListView
        ):
            event.stop()
            self.dismiss(self._selected_file())

    @on(ListView.Selected, "#load-file-list")
    def handle_file_selected(self) -> None:
        self.dismiss(self._selected_file())

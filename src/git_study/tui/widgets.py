import time
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    MarkdownViewer,
    RadioButton,
    RadioSet,
    Static,
)
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


class RemoteRepoCacheScreen(ModalScreen[str | None]):
    CSS = """
    #cache-dialog {
        width: 96;
        max-width: 92%;
        height: 24;
        max-height: 85%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 4 0;
    }

    #cache-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #cache-help,
    #cache-root,
    .cache-entry-meta {
        color: $text-muted;
    }

    #cache-help,
    #cache-root {
        margin-bottom: 1;
    }

    #cache-list {
        height: 1fr;
    }

    #cache-actions {
        height: auto;
        margin-top: 1;
        align: right middle;
    }

    #cache-actions > Button {
        margin-left: 1;
    }

    .cache-entry {
        padding: 0 0 1 0;
    }

    .cache-entry-url {
        text-style: bold;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Close"),
    ]

    def __init__(
        self,
        entries: list[dict[str, str | float]],
        cache_root: Path,
        retention_days: int,
    ) -> None:
        super().__init__()
        self.entries = entries
        self.cache_root = cache_root
        self.retention_days = retention_days

    def compose(self) -> ComposeResult:
        with Vertical(id="cache-dialog"):
            yield Label("Remote Repo Cache", id="cache-title")
            yield Static(
                f"최근 {self.retention_days}일 동안 사용한 캐시를 보관합니다. Esc로 닫기",
                id="cache-help",
            )
            yield Static(f"위치: {self.cache_root}", id="cache-root")
            if self.entries:
                yield ListView(
                    *[
                        ListItem(
                            Static(
                                "\n".join(
                                    [
                                        str(entry["repo_url"]),
                                        f"Last used: {entry['last_used_label']}",
                                        f"Path: {entry['cache_path']}",
                                    ]
                                ),
                                classes="cache-entry",
                            )
                        )
                        for entry in self.entries
                    ],
                    id="cache-list",
                )
            else:
                yield Static(
                    "현재 저장된 원격 저장소 캐시가 없습니다.",
                    id="cache-empty",
                )
            with Horizontal(id="cache-actions"):
                yield Button("Remove Selected", id="cache-remove")
                yield Button("Close", id="cache-close")

    def on_mount(self) -> None:
        if self.entries:
            cache_list = self.query_one("#cache-list", ListView)
            cache_list.index = 0
            cache_list.focus()
        self._update_action_state()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_slug(self) -> str | None:
        if not self.entries:
            return None
        cache_list = self.query_one("#cache-list", ListView)
        index = cache_list.index
        if index is None or not (0 <= index < len(self.entries)):
            return None
        return str(self.entries[index]["slug"])

    def _update_action_state(self) -> None:
        self.query_one("#cache-remove", Button).disabled = not self.entries

    def on_key(self, event: Key) -> None:
        if event.key == "d":
            event.stop()
            selected_slug = self._selected_slug()
            if selected_slug:
                self.dismiss(selected_slug)
            return

    @on(Button.Pressed, "#cache-remove")
    def handle_cache_remove(self) -> None:
        selected_slug = self._selected_slug()
        if selected_slug:
            self.dismiss(selected_slug)

    @on(Button.Pressed, "#cache-close")
    def handle_cache_close(self) -> None:
        self.dismiss(None)

    @on(ListView.Highlighted, "#cache-list")
    def handle_cache_highlighted(self) -> None:
        self._update_action_state()


class ApiKeyScreen(ModalScreen[dict[str, str] | None]):
    CSS = """
    #api-key-dialog {
        width: 88;
        max-width: 92%;
        height: 22;
        max-height: 85%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 4 0;
    }

    #api-key-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #api-key-help,
    #api-key-status,
    #api-key-paths {
        color: $text-muted;
        margin-bottom: 1;
    }

    #api-key-input {
        margin-bottom: 1;
    }

    #api-key-actions {
        height: auto;
        margin-top: 1;
        align: right middle;
    }

    #api-key-actions > Button {
        margin-left: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Close")]

    def __init__(
        self,
        *,
        current_mode: str,
        current_status: str,
        settings_path: Path,
        secrets_path: Path,
    ) -> None:
        super().__init__()
        self.current_mode = current_mode
        self.current_status = current_status
        self.settings_path = settings_path
        self.secrets_path = secrets_path

    def compose(self) -> ComposeResult:
        with Vertical(id="api-key-dialog"):
            yield Label("OpenAI API Key", id="api-key-title")
            yield Static(
                "키는 전역 설정으로만 관리합니다. repo 내부 .git-study 에는 저장하지 않습니다.",
                id="api-key-help",
            )
            yield Static(f"현재 상태: {self.current_status}", id="api-key-status")
            yield Input(
                placeholder="sk-...",
                password=True,
                id="api-key-input",
            )
            yield Label("저장 방식", classes="help-text")
            with RadioSet(id="api-key-mode", compact=True):
                yield RadioButton(
                    "Session Only",
                    id="api-key-mode-session",
                    value=self.current_mode == "session",
                )
                yield RadioButton(
                    "Global File",
                    id="api-key-mode-file",
                    value=self.current_mode == "file",
                )
            yield Static(
                (
                    f"settings: {self.settings_path}\n"
                    f"secret file: {self.secrets_path}"
                ),
                id="api-key-paths",
            )
            with Horizontal(id="api-key-actions"):
                yield Button("Save", id="api-key-save")
                yield Button("Clear", id="api-key-clear")
                yield Button("Cancel", id="api-key-cancel")

    def on_mount(self) -> None:
        self.query_one("#api-key-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_mode(self) -> str:
        mode_set = self.query_one("#api-key-mode", RadioSet)
        pressed = mode_set.pressed_button
        if pressed and pressed.id == "api-key-mode-file":
            return "file"
        return "session"

    @on(Button.Pressed, "#api-key-save")
    def handle_save(self) -> None:
        self.dismiss(
            {
                "action": "save",
                "api_key": self.query_one("#api-key-input", Input).value.strip(),
                "mode": self._selected_mode(),
            }
        )

    @on(Button.Pressed, "#api-key-clear")
    def handle_clear(self) -> None:
        self.dismiss(
            {
                "action": "clear",
                "mode": self._selected_mode(),
            }
        )

    @on(Button.Pressed, "#api-key-cancel")
    def handle_cancel(self) -> None:
        self.dismiss(None)

import time
import subprocess
import sys
from pathlib import Path

from rich.text import Text
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
    TextArea,
)

from .inline_quiz import SessionInlineQuizView
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


class SessionSetupView(Vertical):
    def __init__(
        self,
        *,
        saved_commit_mode: str,
        saved_difficulty: str,
        saved_quiz_style: str,
        saved_read_request: str,
        saved_basic_request: str,
        saved_inline_request: str,
        saved_grading_request: str,
        request_placeholder: str,
        request_example_text: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.saved_commit_mode = saved_commit_mode
        self.saved_difficulty = saved_difficulty
        self.saved_quiz_style = saved_quiz_style
        self.saved_read_request = saved_read_request
        self.saved_basic_request = saved_basic_request
        self.saved_inline_request = saved_inline_request
        self.saved_grading_request = saved_grading_request
        self.request_placeholder = request_placeholder
        self.request_example_text = request_example_text

    def compose(self) -> ComposeResult:
        with Vertical(id="result-session-header"):
            with Vertical(id="control-panel"):
                with Horizontal(classes="row"):
                    with Vertical(classes="option-group"):
                        yield Label("Commit Mode", classes="help-text")
                        with RadioSet(
                            id="commit-mode",
                            classes="mode-group",
                            compact=True,
                        ):
                            yield RadioButton(
                                "Auto Fallback",
                                id="mode-auto",
                                value=self.saved_commit_mode == "auto",
                            )
                            yield RadioButton(
                                "Latest Only",
                                id="mode-latest",
                                value=self.saved_commit_mode == "latest",
                            )
                            yield RadioButton(
                                "Selected Range",
                                id="mode-selected",
                                value=self.saved_commit_mode == "selected",
                            )
                    with Vertical(classes="option-group"):
                        yield Label("Difficulty", classes="help-text")
                        with RadioSet(id="difficulty", compact=True):
                            yield RadioButton(
                                "Easy",
                                id="difficulty-easy",
                                value=self.saved_difficulty == "easy",
                            )
                            yield RadioButton(
                                "Medium",
                                id="difficulty-medium",
                                value=self.saved_difficulty == "medium",
                            )
                            yield RadioButton(
                                "Hard",
                                id="difficulty-hard",
                                value=self.saved_difficulty == "hard",
                            )
                    with Vertical(classes="option-group"):
                        yield Label("Style", classes="help-text")
                        with RadioSet(id="quiz-style", compact=True):
                            yield RadioButton(
                                "Mixed",
                                id="style-mixed",
                                value=self.saved_quiz_style == "mixed",
                            )
                            yield RadioButton(
                                "Study Session",
                                id="style-study_session",
                                value=self.saved_quiz_style == "study_session",
                            )
                            yield RadioButton(
                                "Multiple Choice",
                                id="style-multiple_choice",
                                value=self.saved_quiz_style == "multiple_choice",
                            )
                            yield RadioButton(
                                "Short Answer",
                                id="style-short_answer",
                                value=self.saved_quiz_style == "short_answer",
                            )
                            yield RadioButton(
                                "Conceptual",
                                id="style-conceptual",
                                value=self.saved_quiz_style == "conceptual",
                            )
                with Vertical(id="request-panel"):
                    yield Label("Additional Requests", classes="help-text")
                    yield Label("Read", classes="help-text")
                    yield TextArea(
                        self.saved_read_request,
                        id="request-read-input",
                        placeholder=self.request_placeholder,
                    )
                    yield Label("Basic", classes="help-text")
                    yield TextArea(
                        self.saved_basic_request,
                        id="request-basic-input",
                        placeholder=self.request_placeholder,
                    )
                    yield Label("Inline", classes="help-text")
                    yield TextArea(
                        self.saved_inline_request,
                        id="request-inline-input",
                        placeholder=self.request_placeholder,
                    )
                    yield Label("Grading", classes="help-text")
                    yield TextArea(
                        self.saved_grading_request,
                        id="request-grading-input",
                        placeholder=self.request_placeholder,
                    )
                    yield Static(self.request_example_text, classes="help-text", id="request-example")


class SessionMarkdownView(Vertical):
    def __init__(self, initial_content: str = "", *, viewer_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.initial_content = initial_content
        self.viewer_id = viewer_id

    def compose(self) -> ComposeResult:
        yield LabeledMarkdownViewer(
            self.initial_content,
            id=self.viewer_id,
            show_table_of_contents=False,
        )


class SessionQuizView(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(id="result-quiz-meta-row"):
            yield Label("", id="result-quiz-meta")
            yield Static("", id="result-quiz-meta-spacer")
            yield Static("", id="result-quiz-progress")
        yield Static("", id="result-quiz-question")
        yield TextArea("", id="result-quiz-answer")
        yield Static("", id="result-quiz-feedback")
        with Horizontal(id="result-quiz-controls"):
            yield Button(
                "Prev",
                id="result-quiz-prev",
                classes="result-tool result-action",
            )
            yield Button(
                "Next",
                id="result-quiz-next",
                classes="result-tool result-action",
            )
            yield Button(
                "Retry",
                id="result-quiz-retry",
                classes="result-tool result-action",
            )
            yield Button(
                "Grade",
                id="result-quiz-grade",
                classes="result-tool result-action",
            )
            yield Static("", id="result-quiz-status")


class SessionInlineView(Vertical):
    def compose(self) -> ComposeResult:
        yield SessionInlineQuizView(id="result-inline-widget")


class SessionReviewView(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("학습 세션 리뷰", id="result-review-title")
        with Vertical(classes="result-review-card", id="result-review-score-card"):
            yield Label("점수 요약", classes="result-review-card-title")
            yield Static("-", id="result-review-score-summary")
        with Horizontal(id="result-review-detail-row"):
            with Vertical(classes="result-review-card result-review-list-card"):
                yield Label("약한 개념", classes="result-review-card-title")
                yield Static("-", id="result-review-weak-points")
            with Vertical(classes="result-review-card result-review-list-card"):
                yield Label("다시 볼 파일", classes="result-review-card-title")
                yield Static("-", id="result-review-weak-files")
        with Vertical(classes="result-review-card", id="result-review-next-card"):
            yield Label("다음 추천", classes="result-review-card-title")
            yield Static("-", id="result-review-next-step")


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


class RemoteRepoCacheScreen(ModalScreen[dict[str, str] | None]):
    CSS = """
    #cache-dialog {
        width: 115;
        max-width: 92%;
        height: 36;
        max-height: 85%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 4 0;
    }

    #cache-title-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    #cache-title {
        text-style: bold;
        color: $accent;
    }

    #cache-title-spacer {
        width: 1fr;
    }

    #cache-close {
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

    #cache-close:hover,
    #cache-close:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #cache-help,
    .cache-entry-meta {
        color: $text-muted;
    }

    #cache-help,
    #cache-root-row {
        margin-bottom: 1;
    }

    #cache-root-row {
        height: auto;
        align: left middle;
    }

    #cache-root {
        width: auto;
        min-width: 1;
        color: $text-muted;
        margin-right: 1;
    }

    #cache-root-open {
        width: auto;
        min-width: 4;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        tint: transparent;
    }

    #cache-root-open:hover,
    #cache-root-open:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
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

    #cache-actions > Button:disabled {
        background: transparent;
        border: none;
        color: $text-muted;
        text-style: dim;
    }

    #cache-actions > Button:disabled:hover,
    #cache-actions > Button:disabled:focus {
        background: transparent;
        border: none;
        color: $text-muted;
        text-style: dim;
    }

    .cache-entry {
        padding: 0 1 1 1;
        border: round $panel;
        background: $boost;
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
        current_repo_source: str = "local",
        current_repo_url: str = "",
    ) -> None:
        super().__init__()
        self.entries = entries
        self.cache_root = cache_root
        self.retention_days = retention_days
        self.current_repo_source = current_repo_source
        self.current_repo_url = current_repo_url

    def compose(self) -> ComposeResult:
        with Vertical(id="cache-dialog"):
            with Horizontal(id="cache-title-row"):
                yield Label("GitHub Repo Clones", id="cache-title")
                yield Static("", id="cache-title-spacer")
                yield Button("Close", id="cache-close")
            yield Static(
                f"최근 {self.retention_days}일 동안 사용한 캐시를 보관합니다.",
                id="cache-help",
            )
            with Horizontal(id="cache-root-row"):
                yield Static(f"위치: {self.cache_root}", id="cache-root")
                yield Button("Open", id="cache-root-open")
            if self.entries:
                yield ListView(
                    *[
                        ListItem(
                            Static(
                                self._entry_renderable(entry),
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
                yield Button("Remove All", id="cache-remove-all")
                yield Button("Remove", id="cache-remove")
                yield Button("Select", id="cache-select")

    def on_mount(self) -> None:
        if self.entries:
            cache_list = self.query_one("#cache-list", ListView)
            selected_index = None
            if self.current_repo_source == "github" and self.current_repo_url:
                for index, entry in enumerate(self.entries):
                    if str(entry.get("repo_url", "")).strip() == self.current_repo_url:
                        selected_index = index
                        break
            cache_list.index = selected_index
            cache_list.focus()
        self._update_action_state()

    def _entry_renderable(self, entry: dict[str, str | float]) -> Text:
        text = Text()
        text.append("Url: ", style="bold")
        text.append(str(entry["repo_url"]), style="bold green")
        text.append("\n")
        text.append(f"Last used: {entry['last_used_label']}", style="dim")
        text.append("\n")
        text.append(f"Path: {entry['cache_path']}", style="dim")
        return text

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_entry(self) -> dict[str, str | float] | None:
        if not self.entries:
            return None
        cache_list = self.query_one("#cache-list", ListView)
        index = cache_list.index
        if index is None or not (0 <= index < len(self.entries)):
            return None
        return self.entries[index]

    def _update_action_state(self) -> None:
        has_selected = self._selected_entry() is not None
        self.query_one("#cache-remove-all", Button).disabled = not has_selected
        self.query_one("#cache-remove", Button).disabled = not has_selected
        self.query_one("#cache-select", Button).disabled = not has_selected

    def on_key(self, event: Key) -> None:
        if event.key == "d":
            event.stop()
            selected_entry = self._selected_entry()
            if selected_entry is not None:
                self.dismiss(
                    {
                        "action": "remove",
                        "slug": str(selected_entry["slug"]),
                    }
                )
            return

    @on(Button.Pressed, "#cache-remove-all")
    def handle_cache_remove_all(self) -> None:
        self.dismiss({"action": "remove_all"})

    @on(Button.Pressed, "#cache-remove")
    def handle_cache_remove(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is not None:
            self.dismiss(
                {
                    "action": "remove",
                    "slug": str(selected_entry["slug"]),
                }
            )

    @on(Button.Pressed, "#cache-select")
    def handle_cache_select(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is not None:
            self.dismiss(
                {
                    "action": "select",
                    "slug": str(selected_entry["slug"]),
                    "repo_url": str(selected_entry["repo_url"]),
                }
            )

    @on(Button.Pressed, "#cache-close")
    def handle_cache_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cache-root-open")
    def handle_cache_root_open(self) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(self.cache_root)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(self.cache_root)])
            else:
                subprocess.Popen(["xdg-open", str(self.cache_root)])
        except Exception as exc:
            self.notify(f"폴더를 열지 못했습니다: {exc}", severity="error")

    @on(ListView.Highlighted, "#cache-list")
    def handle_cache_highlighted(self) -> None:
        self._update_action_state()


class SessionListScreen(ModalScreen[dict[str, str] | None]):
    CSS = """
    #session-list-dialog {
        width: 115;
        max-width: 92%;
        height: 36;
        max-height: 85%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        margin: 4 0;
    }

    #session-list-title-row,
    #session-root-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    #session-list-title {
        text-style: bold;
        color: $accent;
    }

    #session-list-title-spacer {
        width: 1fr;
    }

    #session-list-close {
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

    #session-list-close:hover,
    #session-list-close:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #session-list-help,
    #session-root {
        color: $text-muted;
        margin-bottom: 1;
    }

    #session-root-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    #session-root {
        width: auto;
        min-width: 1;
        margin-right: 1;
    }

    #session-root-open {
        width: auto;
        min-width: 4;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        tint: transparent;
    }

    #session-root-open:hover,
    #session-root-open:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #session-list {
        height: 1fr;
    }

    #session-actions {
        height: auto;
        margin-top: 1;
        align: right middle;
    }

    #session-actions > Button {
        margin-left: 1;
    }

    #session-actions > Button:disabled {
        background: transparent;
        border: none;
        color: $text-muted;
        text-style: dim;
    }

    #session-actions > Button:disabled:hover,
    #session-actions > Button:disabled:focus {
        background: transparent;
        border: none;
        color: $text-muted;
        text-style: dim;
    }

    .session-entry {
        padding: 0 1 1 1;
        border: round $panel;
        background: $boost;
    }
    """

    BINDINGS = [("escape", "cancel", "Close")]

    def __init__(
        self,
        entries: list[dict[str, str]],
        session_root: Path,
        current_session_id: str = "",
    ) -> None:
        super().__init__()
        self.entries = entries
        self.session_root = session_root
        self.current_session_id = current_session_id

    def compose(self) -> ComposeResult:
        with Vertical(id="session-list-dialog"):
            with Horizontal(id="session-list-title-row"):
                yield Label("Sessions", id="session-list-title")
                yield Static("", id="session-list-title-spacer")
                yield Button("Close", id="session-list-close")
            yield Static("저장된 학습 세션을 선택하거나 삭제할 수 있습니다.", id="session-list-help")
            with Horizontal(id="session-root-row"):
                yield Static(f"위치: {self.session_root}", id="session-root")
                yield Button("Open", id="session-root-open")
            if self.entries:
                yield ListView(
                    *[
                        ListItem(Static(self._entry_renderable(entry), classes="session-entry"))
                        for entry in self.entries
                    ],
                    id="session-list",
                )
            else:
                yield Static("현재 저장된 세션이 없습니다.", id="session-empty")
            with Horizontal(id="session-actions"):
                yield Button("Remove All", id="session-remove-all")
                yield Button("Remove", id="session-remove")
                yield Button("Select", id="session-select")

    def on_mount(self) -> None:
        if self.entries:
            session_list = self.query_one("#session-list", ListView)
            selected_index = None
            if self.current_session_id:
                for index, entry in enumerate(self.entries):
                    if entry.get("session_id", "") == self.current_session_id:
                        selected_index = index
                        break
            session_list.index = selected_index
            session_list.focus()
        self._update_action_state()

    def _entry_renderable(self, entry: dict[str, str]) -> Text:
        text = Text()
        text.append("Session: ", style="dim")
        text.append(entry.get("range_summary") or entry.get("session_id", "-"), style="bold green")
        text.append("\n")
        text.append("Progress: ", style="dim")
        text.append_text(
            self._progress_text(
                read_status=entry.get("read_status", "not_started"),
                basic_status=entry.get("basic_status", "not_started"),
                inline_status=entry.get("inline_status", "not_started"),
            )
        )
        text.append("\n")
        text.append(f"Updated: {entry.get('updated_label', '-')}", style="dim")
        return text

    def _step_badge(self, status: str, *, read: bool = False) -> str:
        if status in {"ready", "in_progress"}:
            return "R"
        if status in {"completed", "graded"}:
            return "✓"
        return "_"

    def _review_badge(self, entry: dict[str, str]) -> str:
        read_done = entry.get("read_status", "") == "completed"
        basic_done = entry.get("basic_status", "") == "graded"
        inline_done = entry.get("inline_status", "") == "graded"
        return "R" if read_done and basic_done and inline_done else "_"

    def _append_badge(self, text: Text, label: str, badge: str) -> None:
        text.append(label)
        text.append("[", style="dim")
        badge_style = "green" if badge == "R" else "dim"
        text.append(badge, style=badge_style)
        text.append("]", style="dim")

    def _progress_text(
        self,
        *,
        read_status: str,
        basic_status: str,
        inline_status: str,
    ) -> Text:
        text = Text()
        self._append_badge(text, "Read", self._step_badge(read_status, read=True))
        text.append(" → ", style="dim")
        self._append_badge(text, "Basic", self._step_badge(basic_status))
        text.append(" → ", style="dim")
        self._append_badge(text, "Inline", self._step_badge(inline_status))
        text.append(" → ", style="dim")
        self._append_badge(
            text,
            "Review",
            self._review_badge(
                {
                    "read_status": read_status,
                    "basic_status": basic_status,
                    "inline_status": inline_status,
                }
            ),
        )
        return text

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_entry(self) -> dict[str, str] | None:
        if not self.entries:
            return None
        session_list = self.query_one("#session-list", ListView)
        index = session_list.index
        if index is None or not (0 <= index < len(self.entries)):
            return None
        return self.entries[index]

    def _update_action_state(self) -> None:
        has_selected = self._selected_entry() is not None
        self.query_one("#session-remove-all", Button).disabled = not has_selected
        self.query_one("#session-remove", Button).disabled = not has_selected
        self.query_one("#session-select", Button).disabled = not has_selected

    @on(Button.Pressed, "#session-remove-all")
    def handle_session_remove_all(self) -> None:
        self.dismiss({"action": "remove_all"})

    @on(Button.Pressed, "#session-remove")
    def handle_session_remove(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is not None:
            self.dismiss({"action": "remove", "session_id": selected_entry["session_id"]})

    @on(Button.Pressed, "#session-select")
    def handle_session_select(self) -> None:
        selected_entry = self._selected_entry()
        if selected_entry is not None:
            self.dismiss({"action": "select", "session_id": selected_entry["session_id"]})

    @on(Button.Pressed, "#session-list-close")
    def handle_session_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#session-root-open")
    def handle_session_root_open(self) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(self.session_root)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(self.session_root)])
            else:
                subprocess.Popen(["xdg-open", str(self.session_root)])
        except Exception as exc:
            self.notify(f"폴더를 열지 못했습니다: {exc}", severity="error")

    @on(ListView.Highlighted, "#session-list")
    def handle_session_highlighted(self) -> None:
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

    #api-key-title-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    #api-key-title {
        text-style: bold;
        color: $accent;
    }

    #api-key-title-spacer {
        width: 1fr;
    }

    #api-key-close {
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

    #api-key-close:hover,
    #api-key-close:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
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
            with Horizontal(id="api-key-title-row"):
                yield Label("OpenAI API Key", id="api-key-title")
                yield Static("", id="api-key-title-spacer")
                yield Button("Close", id="api-key-close")
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

    @on(Button.Pressed, "#api-key-close")
    def handle_close(self) -> None:
        self.dismiss(None)

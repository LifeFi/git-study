import signal
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click, Key, Resize
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)

from ..graph import (
    DEFAULT_COMMIT_LIST_LIMIT,
    build_commit_context,
    get_commit_list_snapshot,
    get_latest_commit_head,
    get_repo,
    graph,
)
from .commit_selection import (
    CommitSelection,
    selected_commit_indices,
    selection_help_text,
    selection_prefix,
    update_selection_for_index,
)
from .code_browser import CodeBrowserDock
from .repo_loading import (
    apply_commit_snapshot_state,
    current_repo_key,
    should_check_remote,
)
from .result_metadata import (
    build_result_metadata_block,
    markdown_content_for_view,
    result_content_for_save,
    selected_range_summary,
    split_result_metadata,
)
from .state import (
    DEFAULT_REQUEST,
    QUIZ_OUTPUT_DIR,
    list_saved_result_files,
    load_app_state,
    save_app_state,
)
from .widgets import LabeledMarkdownViewer, ResultLoadScreen
from .inline_quiz import InlineQuizDock, InlineQuizSavedState


QUIT_CONFIRM_SECONDS = 1.5
LOCAL_COMMIT_POLL_SECONDS = 3.0
REMOTE_COMMIT_POLL_SECONDS = 30.0
STATUS_ANIMATION_SECONDS = 0.35
COMMIT_PANEL_WIDTH = 38
COMMIT_PANEL_COLLAPSED_WIDTH = 3


class QuizGenerated(Message):
    def __init__(self, content: str, created_at: str) -> None:
        self.content = content
        self.created_at = created_at
        super().__init__()


class QuizFailed(Message):
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message
        super().__init__()


class RepoCommitsLoaded(Message):
    def __init__(
        self,
        commits: list[dict[str, str]],
        has_more_commits: bool,
        total_commit_count: int,
        announce: str,
        repo_key: str,
    ) -> None:
        self.commits = commits
        self.has_more_commits = has_more_commits
        self.total_commit_count = total_commit_count
        self.announce = announce
        self.repo_key = repo_key
        super().__init__()


class RepoCommitsFailed(Message):
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message
        super().__init__()


class GitStudyApp(App):
    TITLE = "Git Study"
    SUB_TITLE = "Learn programming through Git history"

    CSS = """
    Screen {
        layout: vertical;
    }

    #top-bar {
        height: 3;
        min-height: 3;
        width: 1fr;
        border: none;
        padding: 0;
        margin-bottom: 0;
        background: $panel;
        align: left middle;
    }

    #top-bar-title {
        color: $accent;
        text-style: bold;
        width: auto;
        content-align: left middle;
    }

    #top-bar-subtitle {
        color: $text-muted;
        width: auto;
        margin-left: 1;
        content-align: left middle;
    }

    #top-bar-spacer {
        width: 1fr;
    }

    #repo-bar {
        height: auto;
        border: round $accent;
        padding: 0 1;
        margin: 0;
        layout: vertical;
    }

    #repo-bar:focus-within {
        border: round $success;
    }

    #repo-bar-top,
    #repo-bar-bottom {
        height: auto;
        width: 1fr;
        align: left middle;
    }

    #repo-bar-top {
        margin-bottom: 1;
    }

    #repo-bar-title {
        width: 10;
        color: $accent;
        text-style: bold;
        margin-right: 1;
    }

    #top-toggle-group {
        width: auto;
        height: 1;
        align: right middle;
    }

    #top-toggle-group > Button {
        margin-left: 1;
    }

    #repo-source {
        width: auto;
        layout: horizontal;
        margin-right: 1;
    }

    #repo-source > RadioButton {
        width: auto;
        margin-right: 2;
    }

    #repo-location {
        width: 1fr;
        margin-right: 1;
        height: 3;
        min-height: 3;
        margin-top: 0;
        margin-bottom: 0;
        padding: 0 1 0 1;
    }

    #repo-open {
        width: auto;
        min-width: 6;
        margin-right: 1;
        height: 3;
        min-height: 3;
        padding: 0;
        background: transparent;
        border: none;
        border-bottom: solid transparent;
        color: cyan;
        text-style: bold;
        content-align: center bottom;
    }

    #repo-open:hover,
    #repo-open:focus {
        background: transparent;
        border: none;
        border-bottom: solid cyan;
        outline: none;
        color: cyan;
        text-style: bold;
    }

    #workspace {
        layout: horizontal;
        height: 1fr;
    }

    #left-column {
        layers: base overlay;
        width: 1fr;
        min-width: 48;
        layout: vertical;
        position: relative;
        margin-right: 1;
    }

    #left-body {
        height: 1fr;
    }

    #main-area {
        width: 1fr;
        min-width: 48;
        margin-left: 1;
    }

    #commit-panel {
        width: 38;
        min-width: 32;
        border: round $accent;
        padding: 0 1;
    }

    #commit-panel.-collapsed {
        width: 3;
        min-width: 3;
        padding: 0;
    }

    #commit-panel.-collapsed #commit-panel-title,
    #commit-panel.-collapsed #commit-panel-help,
    #commit-panel.-collapsed #commit-list {
        display: none;
    }

    #commit-panel-collapsed-indicator {
        display: none;
        width: 1fr;
        align: center middle;
    }

    #commit-panel.-collapsed #commit-panel-collapsed-indicator {
        display: block;
        color: cyan;
        text-style: bold;
    }

    #commit-panel-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #commit-panel-header-spacer {
        width: 1fr;
    }

    #commit-panel-toggle {
        width: auto;
        min-width: 1;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
    }

    #commit-panel-toggle:hover,
    #commit-panel-toggle:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #commit-detail-panel,
    #control-panel,
    #result-panel {
        border: round $accent;
        padding: 0 1;
    }

    #commit-detail-panel {
        height: 11;
    }

    #commit-detail-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #commit-detail-header-spacer {
        width: 1fr;
    }

    #commit-detail-open-code,
    #inline-quiz-open {
        width: auto;
        min-width: 5;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
    }

    #commit-detail-open-code:hover,
    #commit-detail-open-code:focus,
    #inline-quiz-open:hover,
    #inline-quiz-open:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #control-panel {
        height: 8;
    }

    #result-panel {
        height: 1fr;
    }

    #commit-list {
        height: 1fr;
        margin-top: 1;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin: 0 0 0 0;
    }

    .help-text {
        color: $text-muted;
        margin-bottom: 0;
    }

    RadioSet {
        margin: 0;
        padding: 0;
    }

    RadioSet > RadioButton {
        padding-top: 0;
        padding-bottom: 0;
        margin-top: 0;
        margin-bottom: 0;
    }

    .mode-group, .option-group {
        margin: 0;
    }

    .row {
        height: auto;
    }

    #request-input {
        height: 5;
        margin-bottom: 0;
    }

    #commit-detail-view {
        height: 1fr;
        margin-bottom: 0;
    }

    #status {
        margin-top: 0;
        color: $text-muted;
    }

    #result-markdown,
    #result-plain {
        height: 1fr;
        margin: 0;
        padding: 0;
    }

    #result-markdown {
        background: $surface;
    }

    #result-markdown MarkdownH2 {
        text-style: bold;
    }

    #result-plain {
        background: $surface;
        border: none;
    }

    #result-toolbar {
        height: auto;
        width: auto;
    }

    #result-actions-left,
    #result-actions-right {
        width: auto;
        height: auto;
        align: left middle;
    }

    #result-header-spacer {
        width: 1fr;
    }

    #result-command-group {
        width: auto;
        height: auto;
        margin-left: 1;
        padding: 0;
    }

    #result-mode-group {
        width: auto;
        height: auto;
        padding: 0;
    }

    .result-separator {
        width: auto;
        min-width: 1;
        margin: 0;
        padding: 0;
        color: $text-muted;
    }

    .result-tool {
        width: auto;
        min-width: 4;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: $text-muted;
    }

    .result-tool:hover,
    .result-tool:focus {
        background: transparent;
        color: $text;
        text-style: bold underline;
    }

    Button.result-toggle.-active {
        color: $success;
        text-style: bold;
    }

    Button.result-toggle.-active:focus,
    Button.result-toggle.-active:hover {
        color: $success;
        text-style: bold underline;
    }

    .result-action {
        color: cyan;
        text-style: bold;
    }

    #result-header {
        height: auto;
        width: 100%;
        align: left middle;
        margin-bottom: 0;
    }

    #commit-panel:focus-within,
    #commit-detail-panel:focus-within,
    #control-panel:focus-within,
    #result-panel:focus-within {
        border: round $success;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "confirm_quit", "Confirm Quit"),
        ("super+c,ctrl+shift+c", "screen.copy_text", "Copy Selection"),
        ("g", "generate_quiz", "Generate"),
        ("r", "reload_commits", "Reload Commits"),
        ("space", "toggle_commit_selection", "Toggle Commit"),
    ]

    selected_commit_index = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
        app_state = load_app_state()
        self.repo_source = app_state.get("repo_source", "local")
        self.github_repo_url = app_state.get("github_repo_url", "")
        self.saved_commit_mode = app_state.get("commit_mode", "auto")
        self.saved_difficulty = app_state.get("difficulty", "medium")
        self.saved_quiz_style = app_state.get("quiz_style", "mixed")
        self.saved_request = app_state.get("request_text", DEFAULT_REQUEST)
        initial_repo_source = self.repo_source
        initial_github_repo_url = self.github_repo_url or None
        if initial_repo_source == "github" and not initial_github_repo_url:
            initial_repo_source = "local"
            self.repo_source = "local"
        try:
            initial_snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                repo_source=initial_repo_source,
                github_repo_url=initial_github_repo_url,
            )
        except Exception:
            initial_snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                repo_source="local",
                github_repo_url=None,
            )
            self.repo_source = "local"
        self.commits = initial_snapshot["commits"]
        self.has_more_commits = initial_snapshot["has_more_commits"]
        self.total_commit_count = initial_snapshot["total_commit_count"]
        self.selected_range_start_index: int | None = None
        self.selected_range_end_index: int | None = None
        self.unseen_auto_refresh_commit_shas: set[str] = set()
        self.commit_detail_cache: dict[str, str] = {}
        self._inline_quiz_cache: dict[str, InlineQuizSavedState] = {}
        self.last_quit_attempt_at = 0.0
        self._previous_sigint_handler = None
        self._pending_sigint = False
        self._last_seen_head_sha = self.commits[0]["sha"] if self.commits else ""
        self._last_seen_total_commit_count = self.total_commit_count
        self._last_seen_repo_key = "local"
        self._last_remote_refresh_check_at = 0.0
        self.result_content = (
            "왼쪽에서 커밋을 선택하고 Generate Quiz를 누르면 결과가 여기에 표시됩니다."
        )
        self.result_view_mode = "markdown"
        self.result_metadata_expanded = False
        self._status_animation_enabled = False
        self._status_animation_frame = 0
        self._status_animation_base = "퀴즈 굽는중"
        self._result_animation_enabled = False
        self._commit_list_loading_enabled = False
        self.commit_panel_collapsed = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield Label("Git Study", id="top-bar-title")
            yield Label(
                "AI writes code. Can you explain it?",
                id="top-bar-subtitle",
            )
            yield Static("", id="top-bar-spacer")
            with Horizontal(id="top-toggle-group"):
                yield Button("Quiz", id="inline-quiz-open")
                yield Button("Code", id="commit-detail-open-code")
        with Horizontal(id="workspace"):
            with Vertical(id="left-column"):
                with Vertical(id="repo-bar"):
                    with Horizontal(id="repo-bar-top"):
                        yield Label("Repository", id="repo-bar-title")
                        with RadioSet(
                            id="repo-source", classes="mode-group", compact=True
                        ):
                            yield RadioButton(
                                "Local .git",
                                id="repo-local",
                                value=self.repo_source == "local",
                            )
                            yield RadioButton(
                                "GitHub Repo",
                                id="repo-github",
                                value=self.repo_source == "github",
                            )
                    with Horizontal(id="repo-bar-bottom"):
                        yield Input(
                            placeholder="https://github.com/nomadcoders/ai-agents-masterclass",
                            id="repo-location",
                            value=self.github_repo_url,
                        )
                        yield Button(
                            "Quiz", id="repo-open", classes="result-tool result-action"
                        )
                with Horizontal(id="left-body"):
                    with Vertical(id="commit-panel"):
                        with Horizontal(id="commit-panel-header"):
                            yield Label(
                                "Recent Commits",
                                classes="section-title",
                                id="commit-panel-title",
                            )
                            yield Static("", id="commit-panel-header-spacer")
                            yield Button("<", id="commit-panel-toggle")
                        yield Static(">", id="commit-panel-collapsed-indicator")
                        yield Static(
                            self._commit_panel_help_text(),
                            classes="help-text",
                            id="commit-panel-help",
                        )
                        yield ListView(*self._build_commit_items(), id="commit-list")
                    with Vertical(id="main-area"):
                        with Vertical(id="commit-detail-panel"):
                            with Horizontal(id="commit-detail-header"):
                                yield Label("Commit Detail", classes="section-title")
                                yield Static("", id="commit-detail-header-spacer")
                            yield TextArea(
                                "",
                                id="commit-detail-view",
                                read_only=True,
                            )
                        with Vertical(id="control-panel"):
                            yield Label("Quiz Options", classes="section-title")
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
                                            value=self.saved_quiz_style
                                            == "study_session",
                                        )
                                        yield RadioButton(
                                            "Multiple Choice",
                                            id="style-multiple_choice",
                                            value=self.saved_quiz_style
                                            == "multiple_choice",
                                        )
                                        yield RadioButton(
                                            "Short Answer",
                                            id="style-short_answer",
                                            value=self.saved_quiz_style
                                            == "short_answer",
                                        )
                                        yield RadioButton(
                                            "Conceptual",
                                            id="style-conceptual",
                                            value=self.saved_quiz_style == "conceptual",
                                        )
                            yield Label("Additional Request", classes="help-text")
                            yield TextArea(self.saved_request, id="request-input")
                            yield Static("준비됨", id="status")
                        with Vertical(id="result-panel"):
                            with Horizontal(id="result-header"):
                                yield Label("Quiz Output", classes="section-title")
                                with Horizontal(id="result-actions-left"):
                                    with Horizontal(id="result-command-group"):
                                        yield Button(
                                            "Gen",
                                            id="result-generate",
                                            classes="result-tool result-action",
                                        )
                                        yield Button(
                                            "Save",
                                            id="result-download",
                                            classes="result-tool result-action",
                                        )
                                        yield Button(
                                            "Load",
                                            id="result-load",
                                            classes="result-tool result-action",
                                        )
                                yield Static("", id="result-header-spacer")
                                with Horizontal(id="result-actions-right"):
                                    yield Button(
                                        "meta",
                                        id="result-meta-toggle",
                                        classes="result-tool result-action",
                                    )
                                    with Horizontal(id="result-mode-group"):
                                        yield Button(
                                            "md",
                                            id="result-mode-markdown",
                                            classes="result-tool result-toggle",
                                        )
                                        yield Static("|", classes="result-separator")
                                        yield Button(
                                            "plain",
                                            id="result-mode-plain",
                                            classes="result-tool result-toggle",
                                        )
                            yield LabeledMarkdownViewer(
                                self.result_content,
                                id="result-markdown",
                                show_table_of_contents=False,
                            )
                            yield TextArea(
                                self.result_content,
                                id="result-plain",
                                read_only=True,
                            )
                yield InlineQuizDock(id="inline-quiz-dock")
            yield CodeBrowserDock(id="code-browser-dock")
        yield Footer()

    def on_mount(self) -> None:
        self._previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        self.set_interval(0.1, self._poll_sigint)
        self.set_interval(LOCAL_COMMIT_POLL_SECONDS, self._poll_commit_updates)
        self.set_interval(STATUS_ANIMATION_SECONDS, self._animate_status)
        self._update_repo_context()
        commit_list = self.query_one("#commit-list", ListView)
        if self.commits:
            commit_list.index = 0
            self._show_commit_summary(0)
            self._update_commit_detail(0)
            commit_list.focus()
        self._set_result_view_mode(self.result_view_mode)
        self._update_workspace_widths()
        self._update_top_toggle_buttons()
        self._save_app_state()

    def on_unmount(self) -> None:
        if self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)

    def on_resize(self, event: Resize) -> None:
        self._update_workspace_widths()

    def _update_workspace_widths(self) -> None:
        left_column = self.query_one("#left-column", Vertical)
        code_browser = self.query_one("#code-browser-dock", CodeBrowserDock)
        inline_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        left_column.styles.width = "1fr"
        code_browser.styles.width = "1fr" if code_browser.display else "auto"
        inline_quiz.styles.width = "100%" if inline_quiz.display else "auto"
        self._update_top_toggle_buttons()

    def _reload_code_browser_if_open(self) -> None:
        code_browser = self.query_one("#code-browser-dock", CodeBrowserDock)
        if not code_browser.display:
            return
        selected_indices = sorted(self._selected_commit_indices())
        if selected_indices:
            newest_index = min(selected_indices)
            oldest_index = max(selected_indices)
        else:
            newest_index = self.selected_commit_index
            oldest_index = self.selected_commit_index

        newest_commit_sha = (
            self.commits[newest_index]["sha"]
            if newest_index < len(self.commits)
            else None
        )
        oldest_commit_sha = (
            self.commits[oldest_index]["sha"]
            if oldest_index < len(self.commits)
            else newest_commit_sha
        )
        if not newest_commit_sha or not oldest_commit_sha:
            return
        code_browser.show_range(
            repo_source=self._current_repo_source(),
            github_repo_url=self._current_github_repo_url(),
            oldest_commit_sha=oldest_commit_sha,
            newest_commit_sha=newest_commit_sha,
            title_suffix=self._selected_commit_title_suffix(),
        )
        self._update_workspace_widths()

    def _reload_inline_quiz_if_open(self) -> None:
        inline_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        if not inline_quiz.display:
            return
        if not self.commits:
            inline_quiz.show_placeholder(
                "커밋을 선택한 뒤 Open을 눌러 인라인 퀴즈를 생성해 주세요."
            )
            self._update_top_toggle_buttons()
            self._update_workspace_widths()
            return
        self._show_inline_quiz()

    def _build_commit_items(self) -> list[ListItem]:
        items: list[ListItem] = []
        for index, commit in enumerate(self.commits):
            items.append(ListItem(Label(self._commit_label_text(index))))
        if self.has_more_commits:
            items.append(ListItem(Label(self._load_more_label_text())))
            items.append(ListItem(Label(self._load_all_label_text())))
        return items

    def _refresh_commit_list_labels(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        for index, item in enumerate(commit_list.children):
            label_widget = item.query_one(Label)
            if index < len(self.commits):
                label_widget.update(self._commit_label_text(index))
            elif index == len(self.commits):
                label_widget.update(self._load_more_label_text())
            else:
                label_widget.update(self._load_all_label_text())

    def _commit_label_text(self, index: int) -> Text:
        commit = self.commits[index]
        prefix = Text("   ")
        selection = CommitSelection(
            start_index=self.selected_range_start_index,
            end_index=self.selected_range_end_index,
        )
        prefix_kind = selection_prefix(index, selection)
        if prefix_kind == "start":
            prefix = Text(" S ", style="bold green")
        elif prefix_kind == "end":
            prefix = Text(" E ", style="bold green")
        elif prefix_kind == "inside":
            prefix = Text(" · ", style="green")
        line = Text()
        line.append_text(prefix)
        style = (
            "bold bright_cyan"
            if commit["sha"] in self.unseen_auto_refresh_commit_shas
            else ""
        )
        line.append(f"{commit['short_sha']}  {commit['subject']}", style=style)
        return line

    def _load_more_label_text(self) -> Text:
        line = Text(" + ", style="bold cyan")
        line.append(f"Load More Commits (+{DEFAULT_COMMIT_LIST_LIMIT})", style="bold")
        return line

    def _load_all_label_text(self) -> Text:
        line = Text(" + ", style="bold cyan")
        line.append("Load All Commits", style="bold")
        return line

    def _commit_panel_help_text(self) -> str:
        selected_count = len(self._selected_commit_indices())
        selection_help = selection_help_text(
            CommitSelection(
                start_index=self.selected_range_start_index,
                end_index=self.selected_range_end_index,
            ),
            selected_count,
        )
        return (
            f"{selection_help} | "
            f"Loaded {len(self.commits)}/{self.total_commit_count}"
        )

    def _show_commit_summary(self, index: int) -> None:
        if not self.commits:
            return
        commit = self.commits[index]
        selected_count = len(self._selected_commit_indices())
        range_summary = self._selected_range_summary()
        status = self.query_one("#status", Static)
        status.update(
            "\n".join(
                [
                    f"선택된 커밋: {commit['short_sha']}",
                    f"제목: {commit['subject']}",
                    f"작성자: {commit['author']}",
                    f"날짜: {commit['date']}",
                    f"선택 범위 개수: {selected_count}",
                    f"범위: {range_summary}",
                ]
            )
        )

    def _commit_detail_text(self, index: int) -> str:
        commit = self.commits[index]
        cached = self.commit_detail_cache.get(commit["sha"])
        if cached is not None:
            return cached

        repo = get_repo(**self._repo_args(refresh_remote=False))
        selected_commit = repo.commit(commit["sha"])
        context = build_commit_context(
            selected_commit,
            "selected_commit",
            repo,
        )
        message_lines = selected_commit.message.splitlines()
        body = "\n".join(message_lines[1:]).strip() if len(message_lines) > 1 else ""
        detail_lines = [
            f"SHA: {context['commit_sha']}",
            f"Subject: {context['commit_subject']}",
            f"Author: {context['commit_author']}",
            f"Date: {context['commit_date']}",
        ]
        if body:
            detail_lines.extend(
                [
                    "",
                    "[Body]",
                    body,
                ]
            )
        detail_lines.extend(
            [
                "",
                "[Changed Files]",
                context["changed_files_summary"] or "No changed files.",
                "",
                "[Diff Preview]",
                context["diff_text"]
                or "텍스트 diff가 없습니다. 이 커밋은 바이너리 파일 변경이거나 코드 hunk가 없는 변경일 수 있습니다.",
            ]
        )
        detail = "\n".join(detail_lines)
        self.commit_detail_cache[commit["sha"]] = detail
        return detail

    def _update_commit_detail(self, index: int) -> None:
        detail_view = self.query_one("#commit-detail-view", TextArea)
        detail_view.text = self._commit_detail_text(index)
        detail_view.scroll_home(animate=False)

    def _current_commit_mode(self) -> str:
        pressed = self.query_one("#commit-mode", RadioSet).pressed_button
        if pressed is None:
            return "auto"
        return pressed.id.removeprefix("mode-")

    def _current_repo_source(self) -> str:
        pressed = self.query_one("#repo-source", RadioSet).pressed_button
        if pressed is None:
            return "local"
        return "github" if pressed.id == "repo-github" else "local"

    def _current_github_repo_url(self) -> str | None:
        url = self.query_one("#repo-location", Input).value.strip()
        return url or None

    def _repo_args(self, refresh_remote: bool = True) -> dict:
        return {
            "repo_source": self._current_repo_source(),
            "github_repo_url": self._current_github_repo_url(),
            "refresh_remote": refresh_remote,
        }

    def _current_repo_key(self) -> str:
        return current_repo_key(
            self._current_repo_source(),
            self._current_github_repo_url(),
        )

    def _save_app_state(self) -> None:
        save_app_state(
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            commit_mode=self._current_commit_mode(),
            difficulty=self._current_difficulty(),
            quiz_style=self._current_quiz_style(),
            request_text=(
                self.query_one("#request-input", TextArea).text
                if self.is_mounted
                else self.saved_request
            ),
        )

    def _reset_repo_tracking(self) -> None:
        self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
        self._last_seen_head_sha = ""
        self._last_seen_total_commit_count = 0
        self._last_seen_repo_key = self._current_repo_key()
        self._last_remote_refresh_check_at = 0.0

    def _load_selected_repo(self, announce: str) -> None:
        self._update_repo_context()
        self._reset_repo_tracking()
        self.commit_detail_cache.clear()
        self._clear_selected_range()
        self.selected_commit_index = 0
        if (
            self._current_repo_source() == "github"
            and not self._current_github_repo_url()
        ):
            self.commits = []
            self.has_more_commits = False
            self.total_commit_count = 0
            self._refresh_commit_list_view()
            self._update_commit_panel_help()
            self._set_status("GitHub 저장소 URL을 입력해 주세요.")
            self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
            return
        self._show_commit_list_loading("커밋 불러오는 중...")
        self._set_status("커밋 목록을 불러오는 중...")
        self._commit_list_loading_enabled = True
        self._status_animation_frame = 0
        self.query_one("#repo-open", Button).disabled = True
        self.load_repo_commits(self._repo_args(), announce, self._current_repo_key())

    def _current_difficulty(self) -> str:
        pressed = self.query_one("#difficulty", RadioSet).pressed_button
        if pressed is None:
            return "medium"
        return pressed.id.removeprefix("difficulty-").lower()

    def _current_quiz_style(self) -> str:
        pressed = self.query_one("#quiz-style", RadioSet).pressed_button
        if pressed is None:
            return "mixed"
        return pressed.id.removeprefix("style-")

    def _current_request(self) -> str:
        text = self.query_one("#request-input", TextArea).text.strip()
        return text or DEFAULT_REQUEST

    def _selected_commit_sha(self) -> str | None:
        if not self.commits:
            return None
        return self.commits[self.selected_commit_index]["sha"]

    def _selected_commit_indices(self) -> set[int]:
        return selected_commit_indices(
            CommitSelection(
                start_index=self.selected_range_start_index,
                end_index=self.selected_range_end_index,
            )
        )

    def _selected_commit_shas(self) -> list[str]:
        return [
            self.commits[index]["sha"]
            for index in sorted(self._selected_commit_indices())
        ]

    def _clear_selected_range(self) -> None:
        self.selected_range_start_index = None
        self.selected_range_end_index = None

    def _selected_range_summary(self) -> str:
        return selected_range_summary(
            self.commits,
            self.selected_range_start_index,
            self.selected_range_end_index,
        )

    def _selected_commit_title_suffix(self) -> str:
        if not self.commits:
            return ""
        if self.selected_range_start_index is not None:
            start_commit = self.commits[self.selected_range_start_index]
            if self.selected_range_end_index is None:
                return f"S {start_commit['short_sha']}"
            end_commit = self.commits[self.selected_range_end_index]
            return f"S {start_commit['short_sha']} -> E {end_commit['short_sha']}"
        if self.selected_commit_index < len(self.commits):
            commit = self.commits[self.selected_commit_index]
            return f"S {commit['short_sha']}"
        return ""

    def _result_metadata_block(
        self, extension: str, created_at: str | None = None
    ) -> str:
        selected_indices = sorted(self._selected_commit_indices())
        selected_commits = [
            self.commits[index]
            for index in selected_indices
            if index < len(self.commits)
        ]
        highlighted_commit = (
            self.commits[self.selected_commit_index]
            if self.commits and self.selected_commit_index < len(self.commits)
            else None
        )
        return build_result_metadata_block(
            extension=extension,
            created_at=created_at,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            commit_mode=self._current_commit_mode(),
            difficulty=self._current_difficulty(),
            quiz_style=self._current_quiz_style(),
            range_summary=self._selected_range_summary(),
            selected_commits=selected_commits,
            highlighted_commit=highlighted_commit,
        )

    def _set_result(self, content: str) -> None:
        self.result_content = content
        has_metadata = split_result_metadata(content) is not None
        if has_metadata:
            self.result_metadata_expanded = False
        markdown_view = self.query_one("#result-markdown", LabeledMarkdownViewer)
        markdown_view.document.update(
            markdown_content_for_view(content, self.result_metadata_expanded)
        )
        markdown_view.scroll_home(animate=False)
        plain_view = self.query_one("#result-plain", TextArea)
        plain_view.text = content
        plain_view.scroll_home(animate=False)
        meta_button = self.query_one("#result-meta-toggle", Button)
        meta_button.display = has_metadata
        meta_button.label = "meta -" if self.result_metadata_expanded else "meta +"

    def _set_result_view_mode(self, mode: str) -> None:
        self.result_view_mode = mode
        markdown_view = self.query_one("#result-markdown", LabeledMarkdownViewer)
        plain_view = self.query_one("#result-plain", TextArea)
        markdown_button = self.query_one("#result-mode-markdown", Button)
        plain_button = self.query_one("#result-mode-plain", Button)
        meta_button = self.query_one("#result-meta-toggle", Button)

        is_markdown = mode == "markdown"
        markdown_view.display = is_markdown
        plain_view.display = not is_markdown
        markdown_button.set_class(is_markdown, "-active")
        plain_button.set_class(not is_markdown, "-active")
        meta_button.display = (
            is_markdown and split_result_metadata(self.result_content) is not None
        )

    def _download_result(self) -> None:
        extension = "md" if self.result_view_mode == "markdown" else "txt"
        QUIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = (
            QUIZ_OUTPUT_DIR
            / f"quiz-output-{time.strftime('%Y%m%d-%H%M%S')}.{extension}"
        )
        file_content = result_content_for_save(self.result_content, extension)
        filename.write_text(file_content, encoding="utf-8")
        self._set_status(f"결과를 저장했습니다: {filename.name}")
        self.notify(
            f"{filename.name} 파일로 저장했습니다.",
            title="Download Complete",
            timeout=2.0,
        )

    def _saved_result_files(self) -> list[Path]:
        return list_saved_result_files()

    def _load_result_from_file(self, filename: Path) -> None:
        content = filename.read_text(encoding="utf-8")
        self._set_result(content)
        self._set_result_view_mode(
            "markdown" if filename.suffix.lower() == ".md" else "plain"
        )
        self._set_status(f"결과를 불러왔습니다: {filename.name}")
        self.notify(
            f"{filename.name} 파일을 불러왔습니다.",
            title="Load Complete",
            timeout=2.0,
        )

    def _load_result(self) -> None:
        candidates = self._saved_result_files()
        if not candidates:
            self._set_status("불러올 저장 파일이 없습니다.")
            self.notify(
                "저장된 퀴즈 파일이 없습니다.",
                title="Load Failed",
                severity="warning",
                timeout=2.0,
            )
            return

        self.push_screen(ResultLoadScreen(candidates), self._handle_loaded_result)

    def _handle_loaded_result(self, selected_file: Path | None) -> None:
        if selected_file is None:
            self._set_status("불러오기를 취소했습니다.")
            return
        self._load_result_from_file(selected_file)

    def _set_status(self, content: str) -> None:
        self.query_one("#status", Static).update(content)

    def _update_repo_context(self) -> None:
        repo_source = self._current_repo_source()
        repo_location = self.query_one("#repo-location", Input)
        repo_open = self.query_one("#repo-open", Button)
        if repo_source == "local":
            local_repo = get_repo(repo_source="local", refresh_remote=False)
            local_repo_path = local_repo.working_tree_dir or str(Path.cwd())
            repo_location.value = local_repo_path
            repo_location.tooltip = local_repo_path
            repo_open.label = "Quiz"
        else:
            local_repo = get_repo(repo_source="local", refresh_remote=False)
            local_repo_path = local_repo.working_tree_dir or str(Path.cwd())
            if repo_location.value == local_repo_path:
                repo_location.value = self.github_repo_url
            repo_location.tooltip = None
            repo_open.label = "Quiz"
        self._save_app_state()

    def _start_status_animation(self) -> None:
        self._status_animation_enabled = True
        self._result_animation_enabled = True
        self._status_animation_frame = 0
        self._animate_status()

    def _stop_status_animation(self) -> None:
        self._status_animation_enabled = False
        self._result_animation_enabled = False

    def _animate_status(self) -> None:
        if (
            not self._status_animation_enabled
            and not self._result_animation_enabled
            and not self._commit_list_loading_enabled
        ):
            return
        dots = "." * ((self._status_animation_frame % 3) + 1)
        animated_text = f"{self._status_animation_base}{dots}"
        if self._status_animation_enabled:
            self._set_status(animated_text)
        if self._result_animation_enabled:
            self._set_result(
                "\n".join(
                    [
                        f"## {animated_text}",
                        "",
                        "잠시만 기다려 주세요.",
                    ]
                )
            )
        if self._commit_list_loading_enabled:
            self._show_commit_list_loading(f"커밋 불러오는 중{dots}")
        self._status_animation_frame += 1

    def _update_commit_panel_help(self) -> None:
        self.query_one("#commit-panel-help", Static).update(
            self._commit_panel_help_text()
        )

    def _refresh_commit_list_view(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        commit_list.clear()
        for item in self._build_commit_items():
            commit_list.append(item)

    def _show_commit_list_loading(self, message: str) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        commit_list.clear()
        commit_list.append(ListItem(Label(Text(f" {message}", style="bold cyan"))))

    def _apply_commit_snapshot(
        self,
        commits: list[dict[str, str]],
        has_more_commits: bool,
        total_commit_count: int,
        announce: str | None = None,
        mark_new_commits: bool = False,
    ) -> None:
        applied_state = apply_commit_snapshot_state(
            previous_commits=self.commits,
            new_commits=commits,
            selected_commit_index=self.selected_commit_index,
            selected_range_start_index=self.selected_range_start_index,
            selected_range_end_index=self.selected_range_end_index,
            mark_new_commits=mark_new_commits,
            unseen_auto_refresh_commit_shas=self.unseen_auto_refresh_commit_shas,
            total_commit_count=total_commit_count,
        )
        self.commits = commits
        self.has_more_commits = has_more_commits
        self.total_commit_count = total_commit_count
        self._last_seen_head_sha = applied_state.last_seen_head_sha
        self._last_seen_total_commit_count = applied_state.last_seen_total_commit_count
        self.selected_range_start_index = applied_state.selected_range_start_index
        self.selected_range_end_index = applied_state.selected_range_end_index
        self.selected_commit_index = applied_state.selected_commit_index
        self.unseen_auto_refresh_commit_shas = (
            applied_state.unseen_auto_refresh_commit_shas
        )

        self._refresh_commit_list_view()
        self._update_commit_panel_help()
        self._restore_selection_after_refresh()

        if announce:
            self._set_status(announce)

    def _restore_selection_after_refresh(self) -> None:
        commit_list = self.query_one("#commit-list", ListView)
        if not self.commits:
            return

        restored_index = min(self.selected_commit_index, len(self.commits) - 1)
        commit_list.index = restored_index
        self.selected_commit_index = restored_index
        self._show_commit_summary(restored_index)
        self._update_commit_detail(restored_index)
        self._reload_inline_quiz_if_open()

    def _reload_commit_data(
        self,
        announce: str | None = None,
        mark_new_commits: bool = False,
    ) -> None:
        current_repo_key = self._current_repo_key()
        if current_repo_key != self._last_seen_repo_key:
            self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
            self._last_seen_head_sha = ""
            self._last_seen_total_commit_count = 0
            self._last_seen_repo_key = current_repo_key
        repo_args = self._repo_args()
        self._update_repo_context()
        previous_total_commit_count = self.total_commit_count
        try:
            snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                **repo_args,
            )
            if mark_new_commits and previous_total_commit_count:
                new_commit_count = max(
                    0, snapshot["total_commit_count"] - previous_total_commit_count
                )
                if new_commit_count:
                    target_limit = self.commit_list_limit + new_commit_count
                    if target_limit != self.commit_list_limit:
                        self.commit_list_limit = target_limit
                        snapshot = get_commit_list_snapshot(
                            limit=self.commit_list_limit,
                            **repo_args,
                        )
        except Exception as exc:
            self.commits = []
            self.has_more_commits = False
            self.total_commit_count = 0
            self._last_seen_head_sha = ""
            self._last_seen_total_commit_count = 0
            self._refresh_commit_list_view()
            self._update_commit_panel_help()
            self._set_status("저장소를 불러오지 못했습니다.")
            self._set_result(str(exc))
            return
        self._apply_commit_snapshot(
            snapshot["commits"],
            snapshot["has_more_commits"],
            snapshot["total_commit_count"],
            announce=announce,
            mark_new_commits=mark_new_commits,
        )

    def _focus_chain(self) -> list[Widget]:
        return [
            self.query_one("#inline-quiz-open", Button),
            self.query_one("#commit-detail-open-code", Button),
            self.query_one("#repo-source", RadioSet),
            self.query_one("#repo-location", Input),
            self.query_one("#repo-open", Button),
            self.query_one("#commit-list", ListView),
            self.query_one("#commit-detail-view", TextArea),
            self.query_one("#commit-mode", RadioSet),
            self.query_one("#difficulty", RadioSet),
            self.query_one("#quiz-style", RadioSet),
            self.query_one("#result-generate", Button),
            self.query_one("#result-download", Button),
            self.query_one("#result-load", Button),
            self.query_one("#result-meta-toggle", Button),
            self.query_one("#result-mode-markdown", Button),
            self.query_one("#result-mode-plain", Button),
            (
                self.query_one("#result-markdown", LabeledMarkdownViewer)
                if self.result_view_mode == "markdown"
                else self.query_one("#result-plain", TextArea)
            ),
        ]

    def _focus_index_for_widget(self, widget: Widget | None) -> int:
        if widget is None:
            return 0

        chain = self._focus_chain()
        for index, target in enumerate(chain):
            if widget is target:
                return index
            if target in widget.ancestors:
                return index
            if widget in target.ancestors:
                return index
        return 0

    def action_focus_next_section(self) -> None:
        chain = self._focus_chain()
        current_index = self._focus_index_for_widget(self.focused)
        next_index = (current_index + 1) % len(chain)
        chain[next_index].focus()

    def action_focus_previous_section(self) -> None:
        chain = self._focus_chain()
        current_index = self._focus_index_for_widget(self.focused)
        next_index = (current_index - 1) % len(chain)
        chain[next_index].focus()

    def action_confirm_quit(self) -> None:
        now = time.monotonic()
        if now - self.last_quit_attempt_at <= QUIT_CONFIRM_SECONDS:
            self.exit()
            return

        self.last_quit_attempt_at = now
        message = (
            f"종료하려면 {QUIT_CONFIRM_SECONDS:.1f}초 안에 Ctrl+C를 한 번 더 누르세요."
        )
        self._set_status(message)
        self.notify(
            message,
            title="Quit Confirmation",
            severity="warning",
            timeout=QUIT_CONFIRM_SECONDS,
        )

    def action_help_quit(self) -> None:
        self.action_confirm_quit()

    def _handle_sigint(self, signum, frame) -> None:
        self._pending_sigint = True

    def _poll_sigint(self) -> None:
        if not self._pending_sigint:
            return
        self._pending_sigint = False
        self.action_confirm_quit()

    def _poll_commit_updates(self) -> None:
        current_repo_key = self._current_repo_key()
        if current_repo_key != self._last_seen_repo_key:
            self._last_seen_repo_key = current_repo_key
            self._last_seen_head_sha = ""
            self._last_seen_total_commit_count = 0
            self._last_remote_refresh_check_at = 0.0
            return

        should_check, next_remote_check_at = should_check_remote(
            self._current_repo_source(),
            self._last_remote_refresh_check_at,
            time.monotonic(),
            REMOTE_COMMIT_POLL_SECONDS,
        )
        self._last_remote_refresh_check_at = next_remote_check_at
        if not should_check:
            return

        repo_args = self._repo_args()
        try:
            latest = get_latest_commit_head(**repo_args)
            latest_head_sha = latest["sha"] if latest else ""
        except Exception:
            return

        if not self._last_seen_head_sha:
            self._last_seen_head_sha = latest_head_sha
            return

        if latest_head_sha != self._last_seen_head_sha:
            self._reload_commit_data(
                "새 커밋을 감지해 목록을 갱신했습니다.",
                mark_new_commits=True,
            )

    def on_key(self, event: Key) -> None:
        if event.key == "tab":
            event.stop()
            self.action_focus_next_section()
            return
        if event.key == "shift+tab":
            event.stop()
            self.action_focus_previous_section()
            return
        if event.key in {"pageup", "pagedown"}:
            commit_list = self.query_one("#commit-list", ListView)
            if self.focused is commit_list and len(commit_list.children) > 0:
                event.stop()
                commit_list.index = (
                    0 if event.key == "pageup" else len(commit_list.children) - 1
                )
            return
        if event.key == "space":
            focused = self.focused
            if focused is self.query_one("#repo-open", Button):
                event.stop()
                self._load_selected_repo("저장소를 불러왔습니다.")
                return
            if focused is self.query_one("#commit-detail-open-code", Button):
                event.stop()
                self.action_open_code_browser()
                return
            if focused is self.query_one("#result-generate", Button):
                event.stop()
                self.action_generate_quiz()
                return
            if focused is self.query_one("#result-mode-markdown", Button):
                event.stop()
                self._set_result_view_mode("markdown")
                return
            if focused is self.query_one("#result-mode-plain", Button):
                event.stop()
                self._set_result_view_mode("plain")
                return
            if focused is self.query_one("#result-load", Button):
                event.stop()
                self._load_result()
                return
            if focused is self.query_one("#result-download", Button):
                event.stop()
                self._download_result()
                return
            if focused is self.query_one("#result-meta-toggle", Button):
                event.stop()
                self._toggle_result_metadata()
                return
            if focused is self.query_one("#inline-quiz-open", Button):
                event.stop()
                self.action_open_inline_quiz()
                return

    @on(ListView.Highlighted, "#commit-list")
    def handle_commit_highlight(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is None:
            return
        if event.list_view.index == len(self.commits):
            self._set_status("Space를 눌러 커밋을 더 불러오세요.")
            return
        if event.list_view.index == len(self.commits) + 1:
            self._set_status("Space를 눌러 커밋 전체를 불러오세요.")
            return
        highlighted_sha = self.commits[event.list_view.index]["sha"]
        if highlighted_sha in self.unseen_auto_refresh_commit_shas:
            self.unseen_auto_refresh_commit_shas.remove(highlighted_sha)
            self._refresh_commit_list_labels()
        self.selected_commit_index = event.list_view.index
        self._show_commit_summary(self.selected_commit_index)
        self._update_commit_detail(self.selected_commit_index)
        self._reload_inline_quiz_if_open()

    @on(Button.Pressed, "#result-generate")
    def handle_generate(self) -> None:
        self.action_generate_quiz()

    @on(Button.Pressed, "#result-mode-markdown")
    def handle_result_mode_markdown(self) -> None:
        self._set_result_view_mode("markdown")

    @on(Button.Pressed, "#result-mode-plain")
    def handle_result_mode_plain(self) -> None:
        self._set_result_view_mode("plain")

    @on(Button.Pressed, "#result-download")
    def handle_result_download(self) -> None:
        self._download_result()

    @on(Button.Pressed, "#result-load")
    def handle_result_load(self) -> None:
        self._load_result()

    @on(Button.Pressed, "#result-meta-toggle")
    def handle_result_meta_toggle(self) -> None:
        self._toggle_result_metadata()

    @on(Button.Pressed, "#repo-open")
    def handle_repo_open(self) -> None:
        self._load_selected_repo("저장소를 불러왔습니다.")

    @on(Button.Pressed, "#commit-detail-open-code")
    def handle_open_code_browser(self) -> None:
        self.action_open_code_browser()

    @on(Button.Pressed, "#inline-quiz-open")
    def handle_open_inline_quiz(self) -> None:
        self.action_open_inline_quiz()

    @on(Button.Pressed, "#commit-panel-toggle")
    def handle_commit_panel_toggle(self) -> None:
        self.commit_panel_collapsed = not self.commit_panel_collapsed
        panel = self.query_one("#commit-panel", Vertical)
        toggle = self.query_one("#commit-panel-toggle", Button)
        panel.set_class(self.commit_panel_collapsed, "-collapsed")
        toggle.label = ">" if self.commit_panel_collapsed else "<"
        self._update_workspace_widths()

    @on(Click, "#commit-panel")
    def handle_commit_panel_click(self, event: Click) -> None:
        if not self.commit_panel_collapsed:
            return
        self.handle_commit_panel_toggle()
        event.stop()

    @on(RadioSet.Changed, "#repo-source")
    def handle_repo_source_changed(self) -> None:
        self.repo_source = self._current_repo_source()
        self._update_repo_context()
        if (
            self._current_repo_source() == "github"
            and not self._current_github_repo_url()
        ):
            self.commits = []
            self.has_more_commits = False
            self.total_commit_count = 0
            self._refresh_commit_list_view()
            self._update_commit_panel_help()
            self._set_status("GitHub 저장소 URL을 입력해 주세요.")
            self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
            return
        self._load_selected_repo("저장소를 불러왔습니다.")

    @on(RadioSet.Changed, "#commit-mode")
    @on(RadioSet.Changed, "#difficulty")
    @on(RadioSet.Changed, "#quiz-style")
    def handle_quiz_option_changed(self) -> None:
        self._save_app_state()

    @on(Input.Submitted, "#repo-location")
    def handle_github_repo_url_submitted(self) -> None:
        self._update_repo_context()
        if self._current_repo_source() != "github":
            return
        self.github_repo_url = self._current_github_repo_url() or ""
        self._save_app_state()
        self._load_selected_repo("GitHub 저장소를 불러왔습니다.")

    @on(Input.Changed, "#repo-location")
    def handle_github_repo_url_changed(self) -> None:
        if self._current_repo_source() == "github":
            self.github_repo_url = self._current_github_repo_url() or ""
            self._save_app_state()
        self._update_repo_context()

    @on(TextArea.Changed, "#request-input")
    def handle_request_changed(self) -> None:
        self._save_app_state()

    def action_toggle_commit_selection(self) -> None:
        if not self.commits:
            return
        index = self.query_one("#commit-list", ListView).index
        if index is None:
            return
        if index == len(self.commits):
            self.action_load_more_commits()
            return
        if index == len(self.commits) + 1:
            self.action_load_all_commits()
            return
        next_selection = update_selection_for_index(
            CommitSelection(
                start_index=self.selected_range_start_index,
                end_index=self.selected_range_end_index,
            ),
            index,
        )
        self.selected_range_start_index = next_selection.start_index
        self.selected_range_end_index = next_selection.end_index
        self._refresh_commit_list_labels()
        self._update_commit_panel_help()
        self._show_commit_summary(index)
        self._reload_code_browser_if_open()
        self._reload_inline_quiz_if_open()
        self._update_top_toggle_buttons()

    def action_open_code_browser(self) -> None:
        code_browser = self.query_one("#code-browser-dock", CodeBrowserDock)
        if code_browser.display:
            code_browser.hide_panel()
            self._update_workspace_widths()
            return
        if not self.commits:
            self._set_status("표시할 커밋이 없습니다.")
            return
        selected_indices = sorted(self._selected_commit_indices())
        if selected_indices:
            newest_index = min(selected_indices)
            oldest_index = max(selected_indices)
        else:
            newest_index = self.selected_commit_index
            oldest_index = self.selected_commit_index

        newest_commit_sha = (
            self.commits[newest_index]["sha"]
            if newest_index < len(self.commits)
            else None
        )
        oldest_commit_sha = (
            self.commits[oldest_index]["sha"]
            if oldest_index < len(self.commits)
            else newest_commit_sha
        )
        if not newest_commit_sha or not oldest_commit_sha:
            self._set_status("코드 브라우저를 열 커밋이 없습니다.")
            return
        code_browser.show_range(
            repo_source=self._current_repo_source(),
            github_repo_url=self._current_github_repo_url(),
            oldest_commit_sha=oldest_commit_sha,
            newest_commit_sha=newest_commit_sha,
            title_suffix=self._selected_commit_title_suffix(),
        )
        self._update_workspace_widths()

    def action_open_inline_quiz(self) -> None:
        inline_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        if inline_quiz.display:
            inline_quiz.hide_panel()
            self._after_inline_quiz_closed()
            return
        self._show_inline_quiz()

    def _show_inline_quiz(self) -> None:
        inline_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        if not self.commits:
            inline_quiz.show_placeholder("표시할 커밋이 없습니다.")
            self._set_status("표시할 커밋이 없습니다.")
            self._update_workspace_widths()
            self._update_top_toggle_buttons()
            return
        selected_indices = sorted(self._selected_commit_indices())
        newest_index = (
            min(selected_indices) if selected_indices else self.selected_commit_index
        )
        if newest_index >= len(self.commits):
            inline_quiz.show_placeholder(
                "커밋을 선택한 뒤 Open을 눌러 인라인 퀴즈를 생성해 주세요."
            )
            self._set_status("커밋을 선택해주세요.")
            self._update_workspace_widths()
            self._update_top_toggle_buttons()
            return
        target_sha = self.commits[newest_index]["sha"]
        # 캐시 키: 선택된 전체 커밋 SHA 조합 (S와 E 모두 반영)
        cache_key = (
            ":".join(
                self.commits[i]["sha"]
                for i in selected_indices
                if i < len(self.commits)
            )
            or target_sha
        )
        repo = get_repo(**self._repo_args(refresh_remote=False))
        selected_commit = repo.commit(target_sha)
        commit_context = build_commit_context(selected_commit, "selected_commit", repo)
        if not commit_context.get("diff_text"):
            inline_quiz.show_placeholder(
                "텍스트 diff가 있는 커밋을 선택하면 인라인 퀴즈를 생성할 수 있습니다."
            )
            self._set_status(
                "이 커밋에는 텍스트 diff가 없습니다. 다른 커밋을 선택해주세요."
            )
            self._update_workspace_widths()
            self._update_top_toggle_buttons()
            return
        saved_state = self._inline_quiz_cache.get(cache_key)
        inline_quiz.show_quiz(
            commit_context=commit_context,
            repo=repo,
            target_commit_sha=target_sha,
            title_suffix=self._selected_commit_title_suffix(),
            saved_state=saved_state,
            cache_key=cache_key,
        )
        self._update_workspace_widths()
        self._update_top_toggle_buttons()

    def save_inline_quiz_state(
        self, cache_key: str, state: InlineQuizSavedState
    ) -> None:
        self._inline_quiz_cache[cache_key] = state
        self._update_top_toggle_buttons()

    def _inline_quiz_cache_key(self) -> str:
        """현재 선택에 해당하는 캐시 키 반환."""
        if not self.commits:
            return ""
        selected_indices = sorted(self._selected_commit_indices())
        newest_index = (
            min(selected_indices) if selected_indices else self.selected_commit_index
        )
        if newest_index >= len(self.commits):
            return ""
        return (
            ":".join(
                self.commits[i]["sha"]
                for i in selected_indices
                if i < len(self.commits)
            )
            or self.commits[newest_index]["sha"]
        )

    def _update_top_toggle_buttons(self) -> None:
        try:
            quiz_btn = self.query_one("#inline-quiz-open", Button)
            code_btn = self.query_one("#commit-detail-open-code", Button)
        except Exception:
            return
        code_browser = self.query_one("#code-browser-dock", CodeBrowserDock)
        inline_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        quiz_btn.label = "Quiz ▲" if inline_quiz.display else "Quiz ▼"
        code_btn.label = "Code ▲" if code_browser.display else "Code ▼"
        quiz_btn.set_class(inline_quiz.display, "-active")
        code_btn.set_class(code_browser.display, "-active")

    def _after_inline_quiz_closed(self) -> None:
        self._update_workspace_widths()
        self._update_top_toggle_buttons()

    @on(Button.Pressed, "#code-browser-close")
    def handle_code_browser_close(self) -> None:
        self.call_after_refresh(self._update_workspace_widths)

    def action_reload_commits(self) -> None:
        self._clear_selected_range()
        self.commit_detail_cache.clear()
        self.selected_commit_index = 0
        self._reload_commit_data("커밋 목록을 새로고침했습니다.")
        self._set_result("커밋 목록을 새로고침했습니다.")

    def action_load_more_commits(self) -> None:
        previous_count = len(self.commits)
        self.commit_list_limit += DEFAULT_COMMIT_LIST_LIMIT
        self._reload_commit_data()

        loaded_count = len(self.commits)
        if loaded_count == previous_count:
            self._set_result("더 불러올 커밋이 없습니다.")
        else:
            self._set_result(f"커밋 목록을 {loaded_count}개까지 확장했습니다.")

    def action_load_all_commits(self) -> None:
        self.commit_list_limit = max(self.total_commit_count, DEFAULT_COMMIT_LIST_LIMIT)
        self._reload_commit_data()
        self._set_result(f"커밋 전체 {len(self.commits)}개를 불러왔습니다.")

    @work(thread=True)
    def load_repo_commits(self, repo_args: dict, announce: str, repo_key: str) -> None:
        try:
            snapshot = get_commit_list_snapshot(
                limit=self.commit_list_limit,
                **repo_args,
            )
        except Exception as exc:
            self.post_message(RepoCommitsFailed(str(exc)))
            return

        self.post_message(
            RepoCommitsLoaded(
                commits=snapshot["commits"],
                has_more_commits=snapshot["has_more_commits"],
                total_commit_count=snapshot["total_commit_count"],
                announce=announce,
                repo_key=repo_key,
            )
        )

    def action_generate_quiz(self) -> None:
        if not self.commits:
            self._set_result("표시할 커밋이 없습니다.")
            return

        payload = {
            "messages": [{"role": "user", "content": self._current_request()}],
            "repo_source": self._current_repo_source(),
            "commit_mode": self._current_commit_mode(),
            "difficulty": self._current_difficulty(),
            "quiz_style": self._current_quiz_style(),
        }
        github_repo_url = self._current_github_repo_url()
        if payload["repo_source"] == "github":
            if not github_repo_url:
                self._set_status("GitHub 저장소 URL을 입력해 주세요.")
                self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
                return
            payload["github_repo_url"] = github_repo_url

        selected_sha = self._selected_commit_sha()
        selected_shas = self._selected_commit_shas()
        if payload["commit_mode"] == "selected":
            if selected_shas:
                payload["requested_commit_shas"] = selected_shas
            elif selected_sha:
                payload["requested_commit_sha"] = selected_sha

        self.query_one("#result-generate", Button).disabled = True
        self._start_status_animation()
        self.generate_quiz(payload)

    @work(thread=True)
    def generate_quiz(self, payload: dict) -> None:
        try:
            result = graph.invoke(
                payload,
                config={"configurable": {"thread_id": "textual-tui-session"}},
            )
        except Exception as exc:
            error_message = str(exc)
            if "OPENAI_API_KEY" in error_message:
                error_message = (
                    "텍스트 diff 기반 퀴즈 생성에는 OPENAI_API_KEY가 필요합니다."
                )
            self.post_message(QuizFailed(error_message))
            return

        final_message = result["messages"][-1]
        self.post_message(
            QuizGenerated(
                str(final_message.content),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        )

    @on(QuizGenerated)
    def handle_quiz_generated(self, message: QuizGenerated) -> None:
        self.query_one("#result-generate", Button).disabled = False
        self._stop_status_animation()
        self._set_status("완료")
        self._set_result(
            f"{self._result_metadata_block('md', created_at=message.created_at)}\n{message.content}"
        )

    @on(QuizFailed)
    def handle_quiz_failed(self, message: QuizFailed) -> None:
        self.query_one("#result-generate", Button).disabled = False
        self._stop_status_animation()
        self._set_status("오류")
        self._set_result(message.error_message)

    def _toggle_result_metadata(self) -> None:
        if split_result_metadata(self.result_content) is None:
            return
        self.result_metadata_expanded = not self.result_metadata_expanded
        meta_button = self.query_one("#result-meta-toggle", Button)
        meta_button.label = "meta -" if self.result_metadata_expanded else "meta +"
        if self.result_view_mode == "markdown":
            markdown_view = self.query_one("#result-markdown", LabeledMarkdownViewer)
            markdown_view.document.update(
                markdown_content_for_view(
                    self.result_content,
                    self.result_metadata_expanded,
                )
            )
            markdown_view.scroll_home(animate=False)

    @on(RepoCommitsLoaded)
    def handle_repo_commits_loaded(self, message: RepoCommitsLoaded) -> None:
        self.query_one("#repo-open", Button).disabled = False
        self._commit_list_loading_enabled = False
        self._last_seen_repo_key = message.repo_key
        self.unseen_auto_refresh_commit_shas.clear()
        self._apply_commit_snapshot(
            message.commits,
            message.has_more_commits,
            message.total_commit_count,
            announce=message.announce,
        )

    @on(RepoCommitsFailed)
    def handle_repo_commits_failed(self, message: RepoCommitsFailed) -> None:
        self.query_one("#repo-open", Button).disabled = False
        self._commit_list_loading_enabled = False
        self.commits = []
        self.has_more_commits = False
        self.total_commit_count = 0
        self._last_seen_head_sha = ""
        self._last_seen_total_commit_count = 0
        self._refresh_commit_list_view()
        self._update_commit_panel_help()
        self._set_status("저장소를 불러오지 못했습니다.")
        self._set_result(message.error_message)


def run() -> None:
    GitStudyApp().run()


if __name__ == "__main__":
    run()

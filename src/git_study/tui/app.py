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
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from .. import __version__
from ..domain.repo_cache import (
    REMOTE_CACHE_RETENTION_DAYS,
    cleanup_expired_remote_repo_caches,
    get_remote_repo_cache_dir,
    list_remote_repo_caches,
    normalize_github_repo_url,
    remove_remote_repo_cache,
)
from ..domain.repo_context import (
    DEFAULT_COMMIT_LIST_LIMIT,
    build_commit_context,
    get_commit_list_snapshot,
    get_latest_commit_head,
    get_repo,
)
from ..secrets import (
    clear_session_openai_api_key,
    delete_openai_api_key,
    get_openai_api_key,
    get_secrets_path,
    save_openai_api_key,
    set_session_openai_api_key,
)
from ..services.general_grade_service import (
    generate_general_quiz_grade_result,
    generate_general_quiz_grades,
    stream_general_grade_progress,
)
from ..services.read_service import run_read, stream_read_progress
from ..services.quiz_service import run_quiz, stream_quiz_progress
from ..settings import DEFAULT_MODEL, get_settings_path, load_settings, save_settings
from ..types import GeneralQuizQuestion, GradingSummary, InlineQuizGrade, InlineQuizQuestion
from .commit_selection import (
    CommitSelection,
    selected_commit_indices,
    selection_help_text,
    selection_prefix,
    update_selection_for_index,
)
from .code_browser import CodeBrowserDock
from .answer_input import AnswerTextArea
from .repo_loading import (
    apply_commit_snapshot_state,
    current_repo_key,
    should_check_remote,
)
from .result_metadata import (
    build_result_metadata_block,
    result_content_for_save,
    split_result_metadata,
)
from .state import (
    DEFAULT_REQUEST,
    find_local_repo_root,
    get_session_repo_dir,
    get_quiz_output_dir,
    list_saved_result_files,
    list_learning_sessions,
    load_app_state,
    load_learning_session_file,
    REQUEST_EXAMPLE_TEXT,
    REQUEST_PLACEHOLDER,
    remove_learning_session,
    save_app_state,
)
from .session_state import (
    complete_general_quiz_grading,
    create_learning_session,
    load_learning_session,
    make_session_id,
    mark_read_completed,
    now_timestamp,
    regenerate_read_step,
    rebuild_review_summary,
    retry_general_quiz,
    retry_inline_quiz,
    save_general_quiz_answer,
    save_general_quiz_rendered_result,
    save_learning_session,
)
from .widgets import (
    ApiKeyScreen,
    LabeledMarkdownViewer,
    RemoteRepoCacheScreen,
    ResultLoadScreen,
    SessionHomeView,
    SessionListScreen,
    SessionInlineView,
    SessionMarkdownView,
    SessionQuizView,
    SessionReviewView,
    SetupScreen,
)
from .inline_quiz import InlineQuizDock, InlineQuizSavedState, InlineQuizWidget


QUIT_CONFIRM_SECONDS = 1.5
REQUEST_INPUT_MIN_LINES = 3
REQUEST_INPUT_MAX_LINES = 8
LOCAL_COMMIT_POLL_SECONDS = 3.0
REMOTE_COMMIT_POLL_SECONDS = 30.0
STATUS_ANIMATION_SECONDS = 0.35
COMMIT_PANEL_WIDTH = 38
COMMIT_PANEL_COLLAPSED_WIDTH = 3
REQUEST_KINDS = ("read", "basic", "inline", "grading")


class QuizGenerated(Message):
    def __init__(
        self,
        content: str,
        created_at: str,
        questions: list[GeneralQuizQuestion],
        target: dict[str, object] | None = None,
    ) -> None:
        self.content = content
        self.created_at = created_at
        self.questions = questions
        self.target = target
        super().__init__()


class QuizFailed(Message):
    def __init__(
        self,
        error_message: str,
        target: dict[str, object] | None = None,
    ) -> None:
        self.error_message = error_message
        self.target = target
        super().__init__()


class ReadGenerated(Message):
    def __init__(
        self,
        content: str,
        created_at: str,
        target: dict[str, object] | None = None,
    ) -> None:
        self.content = content
        self.created_at = created_at
        self.target = target
        super().__init__()


class ReadFailed(Message):
    def __init__(
        self,
        error_message: str,
        target: dict[str, object] | None = None,
    ) -> None:
        self.error_message = error_message
        self.target = target
        super().__init__()


class GeneralGradeNodeStarted(Message):
    def __init__(self, node_name: str, label: str) -> None:
        self.node_name = node_name
        self.label = label
        super().__init__()


class GeneralQuizGraded(Message):
    def __init__(
        self,
        grades: list[dict],
        score_summary: dict,
        grading_summary: GradingSummary | None = None,
        target: dict[str, object] | None = None,
    ) -> None:
        self.grades = grades
        self.score_summary = score_summary
        self.grading_summary = grading_summary or {}
        self.target = target
        super().__init__()


class GeneralQuizGradeFailed(Message):
    def __init__(
        self,
        error_message: str,
        target: dict[str, object] | None = None,
    ) -> None:
        self.error_message = error_message
        self.target = target
        super().__init__()


class QuizNodeStarted(Message):
    def __init__(
        self,
        node_name: str,
        label: str,
        target: dict[str, object] | None = None,
    ) -> None:
        self.node_name = node_name
        self.label = label
        self.target = target
        super().__init__()


class ReadNodeStarted(Message):
    def __init__(self, node_name: str, label: str) -> None:
        self.node_name = node_name
        self.label = label
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
    def __init__(self, error_message: str, repo_key: str) -> None:
        self.error_message = error_message
        self.repo_key = repo_key
        super().__init__()


class GitStudyApp(App):
    TITLE = "Git Study"
    SUB_TITLE = f"Learn programming through Git history · v{__version__}"

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
        background: #181818;
        align: left middle;
    }

    #top-bar-title {
        color: $accent;
        text-style: bold;
        width: auto;
        margin-left: 1;
        content-align: left middle;
    }

    #top-bar-version {
        color: $text-muted;
        width: auto;
        margin-left: 1;
        content-align: left middle;
    }

    #top-bar-subtitle {
        color: $text-muted;
        width: auto;
        margin-left: 3;
        margin-right: 1;
        content-align: left middle;
    }

    #top-bar-context {
        color: $text-muted;
        display: none;
        width: 0;
        margin-left: 0;
        content-align: left middle;
    }

    #top-bar-spacer {
        width: 1fr;
    }

    #repo-bar {
        width: 48;
        min-width: 48;
        height: 6;
        min-height: 6;
        border: round #b88a3b;
        padding: 0 1;
        margin: 0;
        layout: vertical;
    }

    #repo-bar.-collapsed {
        width: 3;
        min-width: 3;
        height: 6;
        min-height: 6;
        padding: 0;
    }

    #repo-bar.-collapsed #repo-source,
    #repo-bar.-collapsed #repo-cache-open,
    #repo-bar.-collapsed #repo-bar-bottom {
        display: none;
    }

    #repo-bar.-collapsed #repo-bar-top-spacer {
        display: none;
    }

    #repo-bar.-collapsed #repo-bar-top {
        width: 1fr;
        height: 1fr;
        margin-bottom: 0;
        align: center middle;
    }

    #repo-bar.-collapsed #repo-bar-title {
        width: 1fr;
        height: 1fr;
        margin-right: 0;
        content-align: center top;
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

    #repo-bar-top-spacer {
        width: 1fr;
    }

    #repo-bar-title {
        width: 6;
        color: $accent;
        text-style: bold;
        margin-right: 0;
    }

    #top-toggle-group {
        width: auto;
        height: 1;
        align: right middle;
    }

    #top-toggle-group > Button {
        margin-left: 1;
        width: auto;
        min-width: 4;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
    }

    #top-toggle-group > Button:hover,
    #top-toggle-group > Button:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #repo-source {
        width: auto;
        layout: horizontal;
        margin-right: 1;
    }

    #repo-source > RadioButton {
        width: auto;
        margin-right: 2;
        border: none;
        background: transparent;
    }

    #repo-source > RadioButton > .toggle--button {
        color: $text-muted;
        background: transparent;
    }

    #repo-source > RadioButton > .toggle--label {
        background: transparent;
        color: $foreground;
    }

    #repo-source > RadioButton.-on > .toggle--button {
        color: $success;
        background: transparent;
    }

    #repo-source > RadioButton:focus {
        border: none;
        background: transparent;
    }

    #repo-source > RadioButton.-textual-compact:focus {
        border: none !important;
        background: transparent;
        background-tint: transparent;
    }

    #repo-source > RadioButton:focus > .toggle--label {
        background: transparent;
        color: $foreground;
        text-style: none;
    }

    #repo-source > RadioButton.-textual-compact:focus > .toggle--label {
        background: transparent;
        color: $foreground;
        text-style: none;
    }

    #repo-source > RadioButton:last-child {
        margin-right: 0;
    }

    #repo-location {
        width: 1fr;
        margin-right: 1;
        height: 1;
        min-height: 1;
        margin-top: 0;
        margin-bottom: 0;
        border: none;
        background: $surface;
        color: $foreground;
        padding: 0 1;
    }

    #repo-open,
    #repo-cache-open {
        width: auto;
        min-width: 4;
        margin-right: 1;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        content-align: center middle;
    }

    #repo-open:hover,
    #repo-open:focus,
    #repo-cache-open:hover,
    #repo-cache-open:focus {
        background: transparent;
        border: none;
        outline: none;
        color: cyan;
        text-style: bold underline;
    }

    #workspace {
        layout: horizontal;
        height: 1fr;
    }

    #main-area {
        width: 1fr;
        min-width: 0;
        height: 1fr;
    }

    #left-column {
        layers: base overlay;
        width: 48;
        min-width: 48;
        layout: vertical;
        position: relative;
    }

    #commit-collapse-strip {
        width: 3;
        min-width: 3;
        height: 1fr;
        align: center middle;
        content-align: center middle;
    }

    #commit-collapse-strip:hover {
        background: $accent 10%;
    }

    #commit-collapse-strip.-collapsed {
        width: 2;
        min-width: 2;
    }

    #commit-collapse-toggle {
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

    #commit-panel {
        width: 48;
        min-width: 48;
        border: round #b88a3b;
        padding: 0 1;
    }

    #commit-panel.-collapsed {
        width: 3;
        min-width: 3;
        padding: 0;
    }

    #commit-panel.-collapsed #commit-panel-help,
    #commit-panel.-collapsed #commit-list,
    #commit-panel.-collapsed #commit-preview-panel,
    #commit-panel.-collapsed #commit-panel-loaded {
        display: none;
    }

    #commit-panel.-collapsed #commit-panel-header {
        width: 1fr;
        align: center top;
    }

    #commit-panel.-collapsed #commit-panel-title-spacer {
        display: none;
    }

    #commit-panel.-collapsed #commit-panel-title {
        width: 1fr;
        content-align: center top;
        margin-right: 0;
    }

    #commit-panel-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #commit-panel-title-spacer {
        width: 1fr;
    }

    #commit-collapse-toggle:hover,
    #commit-collapse-toggle:focus,
    #commit-panel-toggle:hover,
    #commit-panel-toggle:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
    }

    #commit-panel-loaded {
        width: auto;
        color: $text-muted;
    }

    #result-panel,
    #settings-panel {
        border: round #b88a3b;
        padding: 0 1;
    }

    #commit-preview-panel {
        height: 9;
        border-top: solid $panel;
        margin-top: 0;
        padding-top: 0;
    }

    #commit-preview-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #commit-preview-header-spacer {
        width: 1fr;
    }

    #top-code-open,
    #commit-preview-open-code,
    #commit-preview-open-inline {
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

    #top-code-open:hover,
    #top-code-open:focus,
    #commit-preview-open-code:hover,
    #commit-preview-open-code:focus,
    #commit-preview-open-inline:hover,
    #commit-preview-open-inline:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #control-panel {
        height: auto;
        border: none;
        padding: 0;
        margin-bottom: 1;
    }

    #session-action-row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
        padding-bottom: 1;
        border-bottom: solid $panel;
    }

    #session-action-row Button {
        min-width: 14;
        margin-right: 2;
        content-align: center middle;
    }

    #settings-panel {
        height: auto;
        min-height: 3;
        margin-top: 0;
        padding-top: 0;
        padding-bottom: 0;
        background: #181818;
    }

    #settings-row {
        height: auto;
        align: left middle;
        background: #181818;
    }

    Footer {
        background: #181818;
    }

    #settings-row > .section-title {
        margin-left: 1;
    }

    #api-key-status {
        width: 1fr;
        color: $text-muted;
        margin-left: 1;
    }

    #result-panel {
        height: 1fr;
    }

    #result-body {
        height: 1fr;
        margin: 0;
        padding: 0;
        overflow: hidden;
    }

    #result-header-title {
        width: auto;
        color: $accent;
        text-style: bold;
        margin-right: 1;
    }

    #result-header-gen {
        width: auto;
        min-width: 5;
    }

    #result-session-header {
        height: auto;
        margin-bottom: 1;
    }

    #commit-list {
        height: 1fr;
        margin-top: 1;
        margin-bottom: 0;
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

    #repo-source > RadioButton.-nav-focus > .toggle--button,
    #repo-source > RadioButton.-nav-focus > .toggle--label {
        text-style: bold;
    }

    #repo-source > RadioButton.-nav-focus > .toggle--label {
        color: $success;
    }

    #commit-mode > RadioButton,
    #difficulty > RadioButton,
    #quiz-style > RadioButton {
        border: none;
        background: transparent;
    }

    #commit-mode > RadioButton:focus,
    #difficulty > RadioButton:focus,
    #quiz-style > RadioButton:focus,
    #commit-mode > RadioButton.-textual-compact:focus,
    #difficulty > RadioButton.-textual-compact:focus,
    #quiz-style > RadioButton.-textual-compact:focus {
        border: none !important;
        background: transparent;
        background-tint: transparent;
    }

    #commit-mode > RadioButton:focus > .toggle--label,
    #difficulty > RadioButton:focus > .toggle--label,
    #quiz-style > RadioButton:focus > .toggle--label,
    #commit-mode > RadioButton.-textual-compact:focus > .toggle--label,
    #difficulty > RadioButton.-textual-compact:focus > .toggle--label,
    #quiz-style > RadioButton.-textual-compact:focus > .toggle--label {
        background: transparent;
        color: $foreground;
        text-style: none;
    }

    .mode-group, .option-group {
        margin: 0;
    }

    .option-group {
        height: auto;
        width: 1fr;
    }

    .row {
        height: auto;
        align: left top;
    }

    #commit-mode,
    #difficulty,
    #quiz-style {
        height: auto;
    }

    #request-panel {
        height: auto;
        width: 1fr;
        margin-top: 1;
    }

    #request-panel TextArea {
        width: 1fr;
        min-height: 5;
        height: 5;
        margin-bottom: 0;
    }

    #request-example {
        margin: 1 0 1 2;
    }

    #commit-preview-view {
        height: 1fr;
        margin-bottom: 0;
        padding: 0;
        background: transparent;
        overflow-y: auto;
    }

    #status {
        margin-top: 0;
        color: $text-muted;
    }

    #result-setup-view,
    #result-home-view,
    #result-read-view,
    #result-quiz-view,
    #result-inline-view,
    #result-review-view {
        height: 1fr;
        margin: 0;
        padding: 0;
    }

    #result-home-content {
        margin-top: 2;
        padding: 0 1;
    }

    #result-home-scroll {
        height: 1fr;
        overflow-y: auto;
    }

    #result-setup-view {
        overflow-y: auto;
    }

    #result-inline-view {
        display: none;
        position: relative;
        layer: auto;
        offset: 0 0;
        border: none;
        padding: 0;
        background: transparent;
    }

    #result-read-markdown,
    #result-review-markdown {
        height: 1fr;
        background: $surface;
    }

    #result-read-markdown MarkdownH2,
    #result-review-markdown MarkdownH2 {
        text-style: bold;
    }

    #result-read-markdown MarkdownH2 {
        color: $success;
    }

    #result-review-view {
        display: none;
        border: none;
        padding: 0 1 1 1;
        background: $surface;
    }

    #result-review-title {
        height: auto;
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #result-review-score-card,
    #result-review-detail-row {
        height: auto;
        margin-bottom: 1;
    }

    .result-review-card {
        width: 1fr;
        border: round $panel;
        padding: 1;
        background: $boost;
        margin-right: 1;
    }

    #result-review-score-card {
        margin-right: 0;
    }

    #result-review-next-card {
        margin-right: 0;
    }

    .result-review-list-card {
        min-height: 8;
    }

    .result-review-card-title {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    .result-review-card-value {
        height: auto;
        color: $success;
        text-style: bold;
    }

    #result-review-score-summary,
    #result-review-weak-points,
    #result-review-weak-files,
    #result-review-next-step {
        height: auto;
    }

    #result-quiz-view {
        display: none;
        border: none;
        padding: 1 1 1 1;
        background: $surface;
    }

    #result-quiz-nav {
        height: auto;
        margin-bottom: 1;
    }

    .result-quiz-nav-btn {
        width: auto;
        min-width: 5;
        height: auto;
        min-height: 1;
        padding: 0 1;
        background: $boost;
        border: none;
        color: $text;
        text-style: bold;
        margin-right: 1;
    }

    .result-quiz-nav-btn.-active {
        color: $text;
        background: $accent 20%;
    }

    .result-quiz-nav-btn.-answered {
        color: $text;
    }

    #result-quiz-meta {
        height: auto;
        color: $success;
        text-style: bold;
        margin-bottom: 1;
    }

    #result-quiz-meta-row {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #result-quiz-meta-spacer {
        width: 1fr;
    }

    #result-quiz-progress {
        height: auto;
        color: $success;
        margin-left: 1;
    }

    #result-quiz-question {
        height: auto;
        margin-bottom: 1;
    }

    #result-quiz-answer-label {
        height: auto;
        color: $text-muted;
        margin-bottom: 0;
    }

    #result-quiz-answer {
        height: 8;
        margin-top: 1;
        margin-bottom: 1;
    }

    #result-quiz-feedback {
        height: auto;
        color: $text-muted;
        margin-top: 1;
        border: round $panel;
        padding: 1;
        background: $boost;
    }

    #result-quiz-controls {
        height: auto;
        align: left middle;
    }

    #result-quiz-controls-spacer {
        width: 1fr;
    }

    #result-quiz-grade,
    #result-quiz-retry,
    #result-quiz-status {
        display: none;
    }

    #result-quiz-status {
        color: $text-muted;
        margin-left: 1;
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

    #result-tab-group {
        width: auto;
        height: auto;
        padding: 0;
        margin-right: 1;
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

    .result-tab {
        width: 13;
        min-width: 13;
        content-align: left middle;
    }

    #result-tab-home {
        width: 6;
        min-width: 6;
    }

    .result-tool:hover,
    .result-tool:focus {
        background: transparent;
        color: $text;
        text-style: underline;
    }

    Button.result-toggle.is-active {
        color: $success;
        text-style: bold;
    }

    Button.result-toggle.is-active:focus,
    Button.result-toggle.is-active:hover {
        color: $success;
        text-style: bold underline;
    }

    Button.result-tab.is-active {
        color: $accent;
        text-style: bold;
        background: transparent;
    }

    Button.result-tab.is-active:focus,
    Button.result-tab.is-active:hover {
        color: $accent;
        text-style: bold underline;
    }

    .result-action {
        color: cyan;
        text-style: bold;
    }

    .result-action:disabled {
        color: $text-muted;
        text-style: none;
    }

    .result-action:disabled:hover,
    .result-action:disabled:focus {
        background: transparent;
        color: $text-muted;
        text-style: none;
    }

    #result-header {
        height: auto;
        width: 100%;
        align: left middle;
        margin-bottom: 0;
    }

    #result-command-row {
        height: auto;
        align: left middle;
        margin: 1 0 0 0;
        padding-bottom: 0;
        border-bottom: solid $panel;
    }

    #result-command-left,
    #result-command-right {
        width: auto;
        height: auto;
        align: left middle;
    }

    #result-command-spacer {
        width: 1fr;
    }

    #result-generate-label {
        width: auto;
        margin-right: 2;
    }

    #result-command-row Button {
        width: auto;
        min-width: 1;
        margin-right: 2;
        content-align: center middle;
    }

    #commit-panel:focus-within,
    #commit-detail-panel:focus-within,
    #result-panel:focus-within,
    #settings-panel:focus-within {
        border: round $success;
    }

    #control-panel:focus-within {
        border: none;
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
        cleanup_expired_remote_repo_caches()
        self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
        self.local_repo_root = find_local_repo_root()
        self.settings = load_settings()
        self.model_name = self.settings.get("model", DEFAULT_MODEL)
        app_state = load_app_state(local_repo_root=self.local_repo_root)
        self.repo_source = app_state.get("repo_source", "local")
        self.github_repo_url = app_state.get("github_repo_url", "")
        self.saved_commit_mode = app_state.get("commit_mode", "auto")
        self.saved_difficulty = app_state.get("difficulty", "medium")
        self.saved_quiz_style = app_state.get("quiz_style", "mixed")
        self.saved_read_request = app_state.get("read_request_text", DEFAULT_REQUEST)
        self.saved_basic_request = app_state.get("basic_request_text", DEFAULT_REQUEST)
        self.saved_inline_request = app_state.get("inline_request_text", DEFAULT_REQUEST)
        self.saved_grading_request = app_state.get("grading_request_text", DEFAULT_REQUEST)
        self.saved_highlighted_commit_sha = app_state.get("highlighted_commit_sha", "")
        self.saved_selected_range_start_sha = app_state.get(
            "selected_range_start_sha", ""
        )
        self.saved_selected_range_end_sha = app_state.get("selected_range_end_sha", "")
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
                refresh_remote=initial_repo_source != "github",
            )
        except (InvalidGitRepositoryError, NoSuchPathError):
            initial_snapshot = {
                "commits": [],
                "has_more_commits": False,
                "total_commit_count": 0,
            }
            if initial_repo_source == "local":
                self.repo_source = "local"
        except Exception:
            if initial_repo_source == "github":
                initial_snapshot = {
                    "commits": [],
                    "has_more_commits": False,
                    "total_commit_count": 0,
                }
            else:
                try:
                    initial_snapshot = get_commit_list_snapshot(
                        limit=self.commit_list_limit,
                        repo_source="local",
                        github_repo_url=None,
                    )
                    self.repo_source = "local"
                except (InvalidGitRepositoryError, NoSuchPathError):
                    initial_snapshot = {
                        "commits": [],
                        "has_more_commits": False,
                        "total_commit_count": 0,
                    }
                    self.repo_source = "local"
        self.commits = initial_snapshot["commits"]
        self.has_more_commits = initial_snapshot["has_more_commits"]
        self.total_commit_count = initial_snapshot["total_commit_count"]
        self.selected_range_start_index: int | None = None
        self.selected_range_end_index: int | None = None
        self.unseen_auto_refresh_commit_shas: set[str] = set()
        self._restore_saved_commit_selection()
        if self.selected_range_start_index is not None:
            self.saved_commit_mode = "selected"
        self.commit_detail_cache: dict[str, str] = {}
        self.last_quit_attempt_at = 0.0
        self._previous_sigint_handler = None
        self._pending_sigint = False
        self._last_seen_head_sha = self.commits[0]["sha"] if self.commits else ""
        self._last_seen_total_commit_count = self.total_commit_count
        self._last_seen_repo_key = "local"
        self._last_remote_refresh_check_at = 0.0
        self._result_quiz_nav_build_serial = 0
        self.home_content = self._default_result_text()
        self.home_logo_samples = self._home_logo_samples()
        self.result_content = self._default_result_text()
        self.read_content = self._default_result_text()
        self.quiz_content = self._default_result_text()
        self.inline_content = "인라인 퀴즈가 아직 없습니다."
        self.review_content = "리뷰 결과가 아직 없습니다."
        self.result_tab = "home"
        self.result_view_mode = "markdown"
        self.result_metadata_expanded = False
        self._commit_list_loading_enabled = False
        self.commit_panel_collapsed = False
        self._quiz_generation_in_progress = False
        self._quiz_animation_frame = 0
        self._quiz_status_base = "퀴즈 생성 중"
        self._quiz_progress_label = ""
        self._quiz_generation_error_message = ""
        self._quiz_progress_labels: dict[str, str] = {}
        self._quiz_error_messages: dict[str, str] = {}
        self._read_generation_in_progress = False
        self._read_animation_frame = 0
        self._read_status_base = "읽을거리 생성 중"
        self._read_progress_label = ""
        self._badge_animation_frame = 0
        self._active_generation_status_source: str | None = None
        self._suppress_repo_source_change = False
        self._repo_focus_slot: int | None = 0 if self.repo_source == "local" else 1
        self._restoring_general_quiz_answer = False
        self._general_quiz_grading_in_progress = False
        self._pending_quiz_target: dict[str, object] | None = None
        self._pending_quiz_targets: dict[str, dict[str, object]] = {}
        self._pending_read_target: dict[str, object] | None = None
        self._pending_read_targets: dict[str, dict[str, object]] = {}
        self._pending_general_quiz_grade_target: dict[str, object] | None = None
        self._inline_session_targets: dict[str, dict[str, object]] = {}
        self._pending_inline_targets: dict[str, dict[str, object]] = {}
        self._pending_inline_grade_targets: dict[str, dict[str, object]] = {}

    def _default_result_text(self) -> str:
        return (
            "왼쪽에서 커밋이나 범위를 고른 뒤, 오른쪽 `Setup` 버튼에서 설정을 확인하고 "
            "`Read`, `Quiz`, `Inline` 순서로 학습을 진행할 수 있습니다."
        )

    def _home_logo_samples(self) -> list[dict[str, object]]:
        return [
            {
                "name": "Potion Ref Yellow",
                "color": "#9aa4b2",
                "outline_color": "#9aa4b2",
                "fill_color": "#ffd400",
                "particle_color": "#ffd400",
                "cap_color": "#b5653b",
                "art": [
                    "         P   P",
                    "      CCCCC",
                    "       OOO",
                    "      OO OO",
                    "     OOFFFOO",
                    "    OOFFFFFOO",
                    "    OFFFFFFFO",
                    "    OOFFFFFOO",
                    "     OOOOOOO",
                ],
            },
            {
                "name": "INT Drift Amber",
                "color": "#3a86ff",
                "outline_color": "#3a86ff",
                "fill_color": "#ffbf00",
                "particle_color": "#ffbf00",
                "art": [
                    "                P P",
                    "           P  P",
                    "        O",
                    "     OO   OO",
                    "   OOOFFFFFFOOO",
                    "  OOFFFFFFFFFFOO",
                    "  OOFFFFFFFFFFOO",
                    "   OOFFFFFFFFOO",
                    "     OOFFFFOO",
                    "       OOFFO",
                ],
            },
            {
                "name": "INT Flask Sym A",
                "color": "#3a86ff",
                "outline_color": "#3a86ff",
                "fill_color": "#ffdf00",
                "particle_color": "#ffdf00",
                "art": [
                    "             P",
                    "          P PP",
                    "        OO  OO",
                    "      OOFFFFFOO",
                    "     OFFFFFFFFO",
                    "    OOFFFFFFFFOO",
                    "    OOFFFFFFFFOO",
                    "     OFFFFFFFFO",
                    "      OOFFFFOO",
                    "        OOOO",
                ],
            },
            {
                "name": "INT Flask Sym B",
                "color": "#4f7cff",
                "outline_color": "#4f7cff",
                "fill_color": "#ffd400",
                "particle_color": "#ffd400",
                "art": [
                    "            P P",
                    "         P PP",
                    "       OO  OO",
                    "      OOFFFFOO",
                    "    OOOFFFFFFOOO",
                    "    OFFFFFFFFFFO",
                    "    OFFFFFFFFFFO",
                    "     OOFFFFFFOO",
                    "      OOFFFFOO",
                    "        OOOO",
                ],
            },
            {
                "name": "INT Flask Sym C",
                "color": "#5dade2",
                "outline_color": "#5dade2",
                "fill_color": "#ffea00",
                "particle_color": "#ffea00",
                "art": [
                    "              P",
                    "           P PP",
                    "         OO  OO",
                    "      OOOFFFFOOO",
                    "     OOFFFFFFFFOO",
                    "    OOFFFFFFFFFFOO",
                    "    OOFFFFFFFFFFOO",
                    "     OOFFFFFFFFOO",
                    "      OOOFFFFOOO",
                    "         OOOO",
                ],
            },
            {
                "name": "Potion Gray A",
                "color": "#cfcfcf",
                "outline_color": "#cfcfcf",
                "fill_color": "#ffd400",
                "particle_color": "#ffd400",
                "art": [
                    "            P PP",
                    "         PP  P",
                    "       OO  OO",
                    "     OOFFFFOO",
                    "   OOFFFFFFFFOO",
                    "  OOFFFFFFFFFFOO",
                    "   OOFFFFFFFFOO",
                    "     OOFFFFOO",
                    "       OOFFOO",
                ],
            },
            {
                "name": "Potion Gray B",
                "color": "#d8d8d8",
                "outline_color": "#d8d8d8",
                "fill_color": "#ffdf00",
                "particle_color": "#ffdf00",
                "art": [
                    "             P",
                    "          P PP",
                    "        OO  OO",
                    "      OOFFFFFOO",
                    "    OOFFFFFFFFFOO",
                    "   OOFFFFFFFFFFFFOO",
                    "    OOFFFFFFFFFFOO",
                    "      OOFFFFFFOO",
                    "        OOFFOO",
                ],
            },
            {
                "name": "Potion Blue A",
                "color": "#4f7cff",
                "outline_color": "#4f7cff",
                "fill_color": "#ffd400",
                "particle_color": "#ffd400",
                "art": [
                    "            P PP",
                    "         PP  P",
                    "       OO  OO",
                    "     OOFFFFOO",
                    "   OOFFFFFFFFOO",
                    "  OOFFFFFFFFFFOO",
                    "   OOFFFFFFFFOO",
                    "     OOFFFFOO",
                    "       OOFFOO",
                ],
            },
            {
                "name": "Potion Blue B",
                "color": "#3a86ff",
                "outline_color": "#3a86ff",
                "fill_color": "#ffea00",
                "particle_color": "#ffea00",
                "art": [
                    "             P",
                    "          P PP",
                    "        OO  OO",
                    "      OOFFFFFOO",
                    "    OOFFFFFFFFFOO",
                    "   OOFFFFFFFFFFFFOO",
                    "    OOFFFFFFFFFFOO",
                    "      OOFFFFFFOO",
                    "        OOFFOO",
                ],
            },
            {
                "name": "Potion Blue C",
                "color": "#5dade2",
                "outline_color": "#5dade2",
                "fill_color": "#ffd60a",
                "particle_color": "#ffd60a",
                "art": [
                    "              P",
                    "           P PP",
                    "         OO  OO",
                    "      OOOFFFFOOO",
                    "    OOFFFFFFFFFOO",
                    "   OOFFFFFFFFFFFFOO",
                    "    OOFFFFFFFFFFOO",
                    "      OOOFFFFOOO",
                    "         OOFFOO",
                ],
            },
            {"name": "INT Drift Flat A", "color": "#ffd166", "art": ["              ▒ ▒", "         ▒ ▒", "      ▓▓", "    ▓▓  ▓▓", "  ▓▓▓▓▓▓▓▓▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "  ▓▓▓▒▒▒▒▒▒▒▓▓▓", "    ▓▓▓▓▓▓▓▓"]},
            {"name": "INT Drift Flat B", "color": "#ffcf5a", "art": ["               ▒", "          ▒ ▒", "      ▓▓", "    ▓▓  ▓▓", "  ▓▓▓▓▓▓▓▓▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "  ▓▓▓▒▒▒▒▒▒▒▓▓▓", "    ▓▓▓▓▓▓▓▓"]},
            {"name": "INT Drift Flat C", "color": "#e9c46a", "art": ["                ▒ ▒", "           ▒ ▒", "       ▓", "    ▓▓   ▓▓", "  ▓▓▓▓▓▓▓▓▓▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▒▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▒▓▓▓▓", "  ▓▓▓▒▒▒▒▒▒▒▒▓▓▓", "    ▓▓▓▓▓▓▓▓▓"]},
            {"name": "INT Drift Flat D", "color": "#ffdc73", "art": ["              ▒ ✦", "         ▒ ▒", "      ▓▓", "    ▓▓  ▓▓", "  ▓▓▓▓▓▓▓▓▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "  ▓▓▓▒▒▒▒▒▒▒▓▓▓", "    ▓▓▓▓▓▓▓▓"]},
            {"name": "INT Drift Flat E", "color": "#f6bd60", "art": ["                ▒", "           ▒ ▒", "      ▓▓", "    ▓▓  ▓▓", "  ▓▓▓▓▓▓▓▓▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "▓▓▓▓▒▒▒▒▒▒▒▒▒▓▓▓▓", "  ▓▓▓▒▒▒▒▒▒▒▓▓▓", "    ▓▓▓▓▓▓▓▓"]},
            {"name": "Brain", "color": "#ff7a59", "art": ["  ████████", " ███ ██ ███", "████ ██ ████", "████ ██ ████", " ███ ██ ███", "  ████████"]},
            {"name": "Lightbulb", "color": "#ffd166", "art": ["   ██████", " ███ ██ ███", " ██████████", "  ████████", "   ██████", "   ██████"]},
            {"name": "Notebook", "color": "#4ecdc4", "art": [" ██████████", " ██ ███████", " ██ ██ ████", " ██ ███████", " ██ ███████", " ██████████"]},
            {"name": "Eraser", "color": "#ff8fab", "art": ["   ████████", " ████████████", " ████  ██████", " ████  ██████", "   ████████"]},
            {"name": "Pocket Note", "color": "#95d5b2", "art": [" ██████████", " █ ████████", " █ ██ ██ ██", " █ ████████", " █ ████████", " ██████████"]},
            {"name": "Commit", "color": "#f28482", "art": ["  ████████", " ███ ███ ███", " ███████████", " ███ ███ ███", " ███████████", "  ████████"]},
            {"name": "INT Potion", "color": "#c77dff", "art": ["    ████", "   ██████", "  ███  ███", "  ████████", " ██████████", "  ████████"]},
            {"name": "Mana Potion", "color": "#5dade2", "art": ["    ████", "   ██████", "  ███  ███", "  ███ ██ ██", " ██████████", "  ████████"]},
            {"name": "Lime Potion", "color": "#80ed99", "art": ["    ████", "   ██████", "  ███  ███", "  ██ ███ ██", " ██████████", "  ████████"]},
            {"name": "Coral Potion", "color": "#ff6b6b", "art": ["    ████", "   ██████", "  ███  ███", "  ██ █  ███", " ██████████", "  ████████"]},
            {"name": "Potion A", "color": "#c77dff", "art": ["    ██████", "  ██████████", " ███      ███", "████  ▒▒▒▒  ████", "████ ▒▒▒▒▒▒ ████", " ████▒▒▒▒▒▒████", "  ████████████", "    ████████"]},
            {"name": "Potion B", "color": "#5dade2", "art": ["    ██████", "  ██████████", " ███      ███", "████  ░░░░  ████", "████ ░░░░░░ ████", " ████░░░░░░████", "  ████████████", "    ████████"]},
            {"name": "Potion C", "color": "#ffd166", "art": ["     ████", "   ████████", " ████    ████", "████  ▓▓▓▓  ████", "████ ▓▓▓▓▓▓ ████", " ████▓▓▓▓▓▓████", "  ████████████", "    ████████"]},
            {"name": "Potion D", "color": "#ff8fab", "art": ["    ██████", "  ██████████", " ███      ███", "████  ✦▒▒  ████", "████ ▒▒▒▒▒▒ ████", " ████▒▒▒▒▒▒████", "  ████████████", "    ████████"]},
            {"name": "Tilt Potion A", "color": "#3a86ff", "art": ["      ███", "    ██████", "   ███  ███", "  ███    ███", "  ██ ▒▒▒▒██", "   █▒▒▒▒██", "    █████"]},
            {"name": "Tilt Potion B", "color": "#9d4edd", "art": ["      ███", "    ██████", "   ██   ███", "  ███    ███", "  ██ ░░░░██", "   █░░░░██", "    █████"]},
            {"name": "Tilt Potion C", "color": "#ef476f", "art": ["      ███", "    ██████", "   ███  ███", "  ███    ███", "  ██ ▓▓▓▓██", "   █▓▓▓▓██", "    █████"]},
            {"name": "Bottle A", "color": "#3a86ff", "art": ["   ██", "  ████", "  ████", " ██████", " ██  ███", " ███████", "  █████"]},
            {"name": "Bottle B", "color": "#5dade2", "art": ["   ██", "  ████", " ██████", " ██  ███", " █ ▓▓ ██", " ██▓▓███", "  █████"]},
            {"name": "Bottle C", "color": "#9d4edd", "art": ["   ██", "  ████", " ██████", " ██  ███", " █ ▒▒ ██", " ██▒▒███", "  █████"]},
            {"name": "Bottle D", "color": "#ffd166", "art": ["   ██", "  ████", " ██████", " ██  ███", " █ ██ ██", " ██▒▒███", "  █████"]},
            {"name": "Bottle E", "color": "#ef476f", "art": ["   ██", "  ████", " ██████", " ██  ███", " █ ░░ ██", " ██░░███", "  █████"]},
            {"name": "Amber Drop", "color": "#f6bd60", "art": ["   ███", "  █████", " ███████", " ███ ███", "  █████", "   ███", "    █"]},
            {"name": "Knowledge Funnel", "color": "#4ecdc4", "art": ["███████████", " █████████", "  ███████", "   █████", "    ███", "     █", "    ███", "   █████"]},
            {"name": "INT Flask", "color": "#c77dff", "art": ["    █", "   ███", "  █████", " ███ ███", " ███████", " ███ ███", "  █████"]},
            {"name": "Mana Drop", "color": "#4cc9f0", "art": ["   ███", "  █████", " ███ ███", " ███████", " ███ ███", "  █████", "    █"]},
            {"name": "Ruby Drop", "color": "#ef476f", "art": ["   ███", "  █████", " ███████", " ███ ███", " ███████", "  █████", "    █"]},
            {"name": "Essence", "color": "#80ed99", "art": ["   ███", "  █████", " ███████", " ███ ███", "  █████", "   █ █", "    █"]},
            {"name": "Funnel A", "color": "#ffd166", "art": ["███████████", " █████████", "  ███████", "   █████", "    ███", "    ███", "     █"]},
            {"name": "Funnel B", "color": "#a8dadc", "art": ["████  ████", "██████████", " ████████", "  ██████", "   ████", "    ██", "    ██"]},
            {"name": "Study Extractor", "color": "#f28482", "art": ["███████████", " █████████", "  ███████", "   █████", "    ███", "     █", "    ███", "    ███"]},
            {"name": "Drop Flask", "color": "#5dade2", "art": ["    █", "   ███", "   ███", "  █████", " ███ ███", " ███████", "  █████"]},
            {"name": "Book Stack", "color": "#84a59d", "art": [" ██████████", " ███  █████", " ██████████", " ███  █████", " ██████████", " ███  █████"]},
            {"name": "Chip", "color": "#7bdff2", "art": ["  ████████", " ██ ████ ██", "████    ████", "████    ████", " ██ ████ ██", "  ████████"]},
            {"name": "Sticky", "color": "#cdb4db", "art": [" █████████", " ██████████", " ███  █████", " ██████████", " ██████████", "  ████████"]},
            {"name": "Folder", "color": "#f6bd60", "art": ["   ███████", " ███████████", "████  ██████", "████████████", "████████████", " ██████████"]},
            {"name": "Diff", "color": "#90be6d", "art": ["  ████████", " ███  ████", "███████████", "███████████", " ████  ███", "  ████████"]},
            {"name": "Terminal", "color": "#a8dadc", "art": [" ██████████", " █  ███████", " █ █  █████", " █   ██████", " ██████████", " ██████████"]},
        ]

    def _content_for_result_tab(self, tab: str) -> str:
        if tab == "home":
            return self.home_content
        if tab == "read":
            return self.read_content
        if tab == "inline":
            return self.inline_content
        if tab == "review":
            return self.review_content
        return self.quiz_content

    def _markdown_view_id_for_tab(self, tab: str) -> str | None:
        return {
            "read": "result-read-markdown",
        }.get(tab)

    def _session_view_id_for_tab(self, tab: str) -> str:
        return {
            "home": "result-home-view",
            "read": "result-read-view",
            "quiz": "result-quiz-view",
            "inline": "result-inline-view",
            "review": "result-review-view",
        }.get(tab, "result-quiz-view")

    def _update_tab_markdown(self, tab: str, content: str) -> None:
        view_id = self._markdown_view_id_for_tab(tab)
        if view_id is None or not self.is_mounted:
            return
        try:
            markdown_view = self.query_one(f"#{view_id}", LabeledMarkdownViewer)
        except Exception:
            return
        metadata_parts = split_result_metadata(content)
        display_content = metadata_parts[1] if metadata_parts is not None else content
        markdown_view.document.update(display_content)
        markdown_view.scroll_home(animate=False)

    def _update_review_view(self, session: dict | None = None) -> None:
        if not self.is_mounted:
            return
        try:
            title = self.query_one("#result-review-title", Label)
            score_summary = self.query_one("#result-review-score-summary", Static)
            weak_points = self.query_one("#result-review-weak-points", Static)
            weak_files = self.query_one("#result-review-weak-files", Static)
            next_step = self.query_one("#result-review-next-step", Static)
        except Exception:
            return

        title.update("학습 세션 리뷰")
        if session is None:
            score_summary.update("-")
            weak_points.update("-")
            weak_files.update("-")
            next_step.update("Read, Basic Quiz, Inline Quiz 순서로 진행해 주세요.")
            return

        review = session.get("review", {})
        summary = review.get("summary") or {}
        basic_status = str(session.get("general_quiz", {}).get("status", "not_started"))
        inline_status = str(session.get("inline_quiz", {}).get("status", "not_started"))
        basic_score_value = summary.get("general_quiz_score", 0)
        inline_score_value = summary.get("inline_quiz_score", 0)
        score_summary.update(
            "\n".join(
                [
                    (
                        f"- Basic: {basic_score_value}/100"
                        if basic_status == "graded"
                        else f"- Basic: {basic_status}"
                    ),
                    (
                        f"- Inline: {inline_score_value}/100"
                        if inline_status == "graded"
                        else f"- Inline: {inline_status}"
                    ),
                    f"- Session: {str(session.get('session_meta', {}).get('status', '-'))}",
                ]
            )
        )
        weak_points.update(
            "\n".join(f"- {item}" for item in summary.get("weak_points", [])) or "-"
        )
        weak_files.update(
            "\n".join(f"- {item}" for item in summary.get("weak_files", [])) or "-"
        )
        next_step.update(str(summary.get("recommended_next_step", "")).strip() or "-")

    def _set_result_tab(self, tab: str) -> None:
        if tab not in {"home", "read", "quiz", "inline", "review"}:
            tab = "home"
        previous_tab = self.result_tab
        if previous_tab == "inline" and tab != "inline":
            self._persist_open_inline_quiz_state()
        self.result_tab = tab
        try:
            home_button = self.query_one("#result-tab-home", Button)
            read_button = self.query_one("#result-tab-read", Button)
            quiz_button = self.query_one("#result-tab-quiz", Button)
            inline_button = self.query_one("#result-tab-inline", Button)
            review_button = self.query_one("#result-tab-review", Button)
            self._refresh_result_tab_labels()
            home_button.set_class(tab == "home", "is-active")
            read_button.set_class(tab == "read", "is-active")
            quiz_button.set_class(tab == "quiz", "is-active")
            inline_button.set_class(tab == "inline", "is-active")
            review_button.set_class(tab == "review", "is-active")
        except Exception:
            pass
        for candidate in {"home", "read", "quiz", "inline", "review"}:
            try:
                self.query_one(f"#{self._session_view_id_for_tab(candidate)}", Vertical).display = (
                    candidate == tab
                )
            except Exception:
                continue
        if tab in {"read", "inline"}:
            self._update_tab_markdown(tab, self._content_for_result_tab(tab))
        elif tab == "review":
            self._update_review_view(self._load_current_learning_session())
        self._refresh_result_command_row()
        self._refresh_quiz_workspace()

    def _status_badge(self, status: str, *, completed_badge: str = "✓") -> str:
        return {
            "not_started": "_",
            "ready": "R",
            "in_progress": "R",
            "completed": completed_badge,
            "graded": completed_badge,
        }.get(status, "_")

    def _tab_label_text(self, name: str, badge: str) -> Text:
        label = Text(name)
        label.append("[")
        if badge == "R":
            label.append("R", style="green")
        else:
            label.append(badge)
        label.append("]")
        return label

    def _animated_badge(self, symbol: str) -> str:
        frame = ((max(self._badge_animation_frame, 1) - 1) % 3) + 1
        return (symbol * frame).ljust(3)

    def _generating_badge(self) -> str:
        return self._animated_badge("+")

    def _grading_badge(self) -> str:
        return self._animated_badge("✓")

    def _inline_generation_in_progress(self) -> bool:
        for widget_id in ("result-inline-widget", "inline-quiz-dock"):
            try:
                inline_quiz = self.query_one(f"#{widget_id}", InlineQuizWidget)
            except Exception:
                continue
            if widget_id == "inline-quiz-dock" and not inline_quiz.display:
                continue
            if getattr(inline_quiz, "_state", "idle") == "loading":
                return True
        return False

    def _inline_grading_in_progress(self) -> bool:
        for widget_id in ("result-inline-widget", "inline-quiz-dock"):
            try:
                inline_quiz = self.query_one(f"#{widget_id}", InlineQuizWidget)
            except Exception:
                continue
            if widget_id == "inline-quiz-dock" and not inline_quiz.display:
                continue
            if getattr(inline_quiz, "_state", "idle") == "grading":
                return True
        return False

    def _current_inline_generation_in_progress(self) -> bool:
        current_session_id = self._current_learning_session_id()
        return bool(current_session_id and current_session_id in self._pending_inline_targets)

    def _current_inline_grading_in_progress(self) -> bool:
        current_session_id = self._current_learning_session_id()
        return bool(
            current_session_id and current_session_id in self._pending_inline_grade_targets
        )

    def _current_inline_quiz_session(self) -> dict | None:
        session = self._load_current_learning_session()
        if session is None:
            return None
        return session.get("inline_quiz")

    def _refresh_result_tab_labels(self) -> None:
        try:
            home_button = self.query_one("#result-tab-home", Button)
            read_button = self.query_one("#result-tab-read", Button)
            quiz_button = self.query_one("#result-tab-quiz", Button)
            inline_button = self.query_one("#result-tab-inline", Button)
            review_button = self.query_one("#result-tab-review", Button)
        except Exception:
            return

        generating_badge = self._generating_badge()
        grading_badge = self._grading_badge()
        read_badge = generating_badge if self._current_read_generation_in_progress() else "_"
        quiz_badge = "_"
        if self._current_quiz_generation_in_progress():
            quiz_badge = generating_badge
        elif self._current_general_quiz_grading_in_progress():
            quiz_badge = grading_badge
        inline_badge = "_"
        if self._current_inline_generation_in_progress():
            inline_badge = generating_badge
        elif self._current_inline_grading_in_progress():
            inline_badge = grading_badge
        review_badge = "_"

        session = self._load_current_learning_session() if self.commits else None
        if session is not None:
            read_status = str(session.get("read", {}).get("status", "not_started"))
            quiz_status = str(session.get("general_quiz", {}).get("status", "not_started"))
            inline_status = str(session.get("inline_quiz", {}).get("status", "not_started"))
            if not self._current_read_generation_in_progress():
                read_badge = self._status_badge(read_status)
            if not self._current_quiz_generation_in_progress() and not self._current_general_quiz_grading_in_progress():
                quiz_badge = self._status_badge(quiz_status)
            if not self._current_inline_generation_in_progress() and not self._current_inline_grading_in_progress():
                inline_badge = self._status_badge(inline_status)
            review_badge = (
                "R"
                if read_status == "completed"
                and quiz_status == "graded"
                and inline_status == "graded"
                else "_"
            )

        home_button.label = Text("Home")
        read_button.label = self._tab_label_text("Read", read_badge)
        quiz_button.label = self._tab_label_text("Basic", quiz_badge)
        inline_button.label = self._tab_label_text("Inline", inline_badge)
        review_button.label = self._tab_label_text("Review", review_badge)

    def _refresh_result_command_row(self) -> None:
        try:
            command_row = self.query_one("#result-command-row", Horizontal)
            setup_button = self.query_one("#result-tab-setup", Button)
            read_button = self.query_one("#result-read", Button)
            quiz_button = self.query_one("#result-generate", Button)
            inline_button = self.query_one("#result-inline-open", Button)
            sessions_button = self.query_one("#result-sessions-open", Button)
            generate_all_button = self.query_one("#result-session-generate-all", Button)
            read_done_button = self.query_one("#result-read-done-top", Button)
            quiz_retry_button = self.query_one("#result-quiz-retry-top", Button)
            quiz_grade_button = self.query_one("#result-quiz-grade-top", Button)
            inline_retry_button = self.query_one("#result-inline-retry-top", Button)
            inline_grade_button = self.query_one("#result-inline-grade-top", Button)
        except Exception:
            return

        command_row.display = self.result_tab in {"read", "quiz", "inline", "review"}
        command_row.display = self.result_tab in {"home", "read", "quiz", "inline", "review"}
        setup_button.display = True
        generate_all_button.display = self.result_tab == "home"
        read_button.display = self.result_tab == "read"
        quiz_button.display = self.result_tab == "quiz"
        inline_button.display = self.result_tab == "inline"
        sessions_button.display = self.result_tab == "home"
        read_done_button.display = self.result_tab == "read"
        quiz_retry_button.display = self.result_tab == "quiz"
        quiz_grade_button.display = self.result_tab == "quiz"
        inline_retry_button.display = self.result_tab == "inline"
        inline_grade_button.display = self.result_tab == "inline"

        general_quiz = self._current_general_quiz_session() or {}
        inline_quiz = self._current_inline_quiz_session() or {}
        session = self._load_current_learning_session() or {}
        read_step = session.get("read", {})
        general_quiz_has_questions = bool(general_quiz.get("questions", []))
        inline_quiz_has_questions = bool(inline_quiz.get("questions", []))
        read_has_content = bool(str(read_step.get("content", "")).strip())
        read_completed = str(read_step.get("status", "not_started")) == "completed"
        general_quiz_graded = str(general_quiz.get("status", "not_started")) == "graded"
        inline_quiz_graded = str(inline_quiz.get("status", "not_started")) == "graded"
        quiz_busy = (
            self._current_quiz_generation_in_progress()
            or self._current_general_quiz_grading_in_progress()
        )
        inline_busy = (
            self._current_inline_generation_in_progress()
            or self._current_inline_grading_in_progress()
        )

        setup_button.disabled = False
        read_button.disabled = self._current_read_generation_in_progress()
        quiz_button.disabled = self._current_quiz_generation_in_progress()
        inline_button.disabled = inline_busy
        sessions_button.disabled = False
        generate_all_button.disabled = (
            self._current_read_generation_in_progress()
            or self._current_quiz_generation_in_progress()
            or self._current_inline_generation_in_progress()
        )
        read_done_button.disabled = (
            self._current_read_generation_in_progress() or not read_has_content or read_completed
        )
        quiz_retry_button.disabled = quiz_busy or not general_quiz_has_questions
        quiz_grade_button.disabled = (
            quiz_busy or not general_quiz_has_questions or general_quiz_graded
        )
        inline_retry_button.disabled = inline_busy or not inline_quiz_has_questions
        inline_grade_button.disabled = (
            inline_busy or not inline_quiz_has_questions or inline_quiz_graded
        )

    def _top_bar_context_text(self) -> str:
        repo_source = self._current_repo_source() if self.is_mounted else self.repo_source
        repo_label = "local" if repo_source == "local" else (self.github_repo_url or "github")
        selection = self._selected_range_summary() if self.commits else "no selection"
        session = self._load_current_learning_session() if self.commits else None
        session_status = (
            str(session.get("session_meta", {}).get("status", "idle"))
            if session is not None
            else "idle"
        )
        return f"Repo: {repo_label}  |  Selection: {selection}  |  Session: {session_status}"

    def _refresh_top_bar_context(self) -> None:
        try:
            self.query_one("#top-bar-context", Label).update(self._top_bar_context_text())
        except Exception:
            return

    def _refresh_session_progress(self) -> None:
        try:
            self._refresh_commit_list_labels()
            self._refresh_result_tab_labels()
            self._refresh_result_command_row()
        except Exception:
            return

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield Label("Git Study", id="top-bar-title")
            yield Label(f"v{__version__}", id="top-bar-version")
            yield Label(
                "Select on the left, study on the right.",
                id="top-bar-subtitle",
            )
            yield Static("", id="top-bar-spacer")
            with Horizontal(id="top-toggle-group"):
                yield Button("Code", id="top-code-open")
        with Horizontal(id="workspace"):
            with Vertical(id="left-column"):
                with Vertical(id="repo-bar"):
                    with Horizontal(id="repo-bar-top"):
                        yield Static("Repo", id="repo-bar-title")
                        with RadioSet(
                            id="repo-source", classes="mode-group", compact=True
                        ):
                            yield RadioButton(
                                "Local",
                                id="repo-local",
                                value=self.repo_source == "local",
                            )
                            yield RadioButton(
                                "GitHub",
                                id="repo-github",
                                value=self.repo_source == "github",
                            )
                        yield Static("", id="repo-bar-top-spacer")
                        yield Button(
                            "Clones ⧉",
                            id="repo-cache-open",
                            classes="result-tool result-action",
                        )
                    with Horizontal(id="repo-bar-bottom"):
                        yield Input(
                            placeholder="https://github.com/nomadcoders/ai-agents-masterclass",
                            id="repo-location",
                            value=self.github_repo_url,
                        )
                        yield Button(
                            "Open", id="repo-open", classes="result-tool result-action"
                        )
                with Vertical(id="commit-panel"):
                    with Horizontal(id="commit-panel-header"):
                        yield Label(
                            "Commits",
                            classes="section-title",
                            id="commit-panel-title",
                        )
                        yield Static("", id="commit-panel-title-spacer")
                        yield Static("", id="commit-panel-loaded")
                    yield Static(
                        self._commit_panel_help_text(),
                        classes="help-text",
                        id="commit-panel-help",
                    )
                    yield ListView(*self._build_commit_items(), id="commit-list")
                    with Vertical(id="commit-preview-panel"):
                        with Horizontal(id="commit-preview-header"):
                            yield Label("Details", classes="help-text")
                            yield Static("", id="commit-preview-header-spacer")
                            yield Button("Code", id="commit-preview-open-code")
                        yield Static("", id="commit-preview-view")
            with Vertical(id="commit-collapse-strip"):
                yield Button("<", id="commit-collapse-toggle")
            with Vertical(id="main-area"):
                with Vertical(id="result-panel"):
                    with Horizontal(id="result-header"):
                        yield Label("Session", id="result-header-title")
                        yield Static("", id="result-header-spacer")
                        with Horizontal(id="result-tab-group"):
                            yield Button(
                                Text("Home"),
                                id="result-tab-home",
                                classes="result-tool result-tab",
                            )
                            yield Static("|", classes="result-separator")
                            yield Button(
                                Text("Read[_]"),
                                id="result-tab-read",
                                classes="result-tool result-tab",
                            )
                            yield Static("→", classes="result-separator")
                            yield Button(
                                Text("Basic[_]"),
                                id="result-tab-quiz",
                                classes="result-tool result-tab",
                            )
                            yield Static("→", classes="result-separator")
                            yield Button(
                                Text("Inline[_]"),
                                id="result-tab-inline",
                                classes="result-tool result-tab",
                            )
                            yield Static("→", classes="result-separator")
                            yield Button(
                                Text("Review[_]"),
                                id="result-tab-review",
                                classes="result-tool result-tab",
                            )
                    with Horizontal(id="result-command-row"):
                        with Horizontal(id="result-command-left"):
                            yield Button(
                                "Setup ⧉",
                                id="result-tab-setup",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Gen All",
                                id="result-session-generate-all",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Gen Read",
                                id="result-read",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Gen Basic",
                                id="result-generate",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Gen Inline",
                                id="result-inline-open",
                                classes="result-tool result-action",
                            )
                        yield Static("", id="result-command-spacer")
                        with Horizontal(id="result-command-right"):
                            yield Button(
                                "Sessions ⧉",
                                id="result-sessions-open",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Done",
                                id="result-read-done-top",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Retry",
                                id="result-quiz-retry-top",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Grade",
                                id="result-quiz-grade-top",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Retry",
                                id="result-inline-retry-top",
                                classes="result-tool result-action",
                            )
                            yield Button(
                                "Grade",
                                id="result-inline-grade-top",
                                classes="result-tool result-action",
                            )
                    with Vertical(id="result-body"):
                        yield SessionHomeView(
                            self.home_content,
                            samples=self.home_logo_samples,
                            id="result-home-view",
                        )
                        yield SessionMarkdownView(
                            self.read_content,
                            id="result-read-view",
                            viewer_id="result-read-markdown",
                        )
                        yield SessionQuizView(id="result-quiz-view")
                        yield SessionInlineView(id="result-inline-view")
                        yield SessionReviewView(id="result-review-view")
                yield InlineQuizDock(id="inline-quiz-dock")
            yield CodeBrowserDock(id="code-browser-dock")
        with Vertical(id="settings-panel"):
            with Horizontal(id="settings-row"):
                yield Label("Settings", classes="section-title")
                yield Button(
                    "API Key ⧉",
                    id="api-key-open",
                    classes="result-tool result-action",
                )
                yield Static(
                    self._api_key_status_text(),
                    id="api-key-status",
                )
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
        self._set_result_tab(self.result_tab)
        self._update_commit_panel_help()
        self._refresh_top_bar_context()
        self._refresh_session_progress()
        self._update_workspace_widths()
        self._update_top_toggle_buttons()
        self._update_request_input_height()
        self._save_app_state()

    def on_unmount(self) -> None:
        if self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)

    def on_resize(self, event: Resize) -> None:
        self._update_workspace_widths()

    def _update_request_input_height(self) -> None:
        for selector in self._request_input_selectors():
            try:
                request_input = self.query_one(selector, TextArea)
            except Exception:
                continue
            line_count = max(1, request_input.text.count("\n") + 1)
            visible_lines = max(
                REQUEST_INPUT_MIN_LINES,
                min(REQUEST_INPUT_MAX_LINES, line_count),
            )
            target_height = visible_lines + 2
            request_input.styles.height = str(target_height)

    def _request_input_selectors(self) -> list[str]:
        return [
            "#request-read-input",
            "#request-basic-input",
            "#request-inline-input",
            "#request-grading-input",
        ]

    def _request_selector_for_kind(self, kind: str) -> str:
        return {
            "read": "#request-read-input",
            "basic": "#request-basic-input",
            "inline": "#request-inline-input",
            "grading": "#request-grading-input",
        }[kind]

    def _update_workspace_widths(self) -> None:
        left_column = self.query_one("#left-column", Vertical)
        repo_bar = self.query_one("#repo-bar", Vertical)
        repo_bar_title = self.query_one("#repo-bar-title", Static)
        collapse_strip = self.query_one("#commit-collapse-strip", Vertical)
        collapse_button = self.query_one("#commit-collapse-toggle", Button)
        main_area = self.query_one("#main-area", Vertical)
        code_browser = self.query_one("#code-browser-dock", CodeBrowserDock)
        inline_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        left_column.styles.width = "3" if self.commit_panel_collapsed else "48"
        left_column.styles.min_width = "3" if self.commit_panel_collapsed else "48"
        repo_bar.set_class(self.commit_panel_collapsed, "-collapsed")
        repo_bar_title.update("Repo")
        collapse_strip.set_class(self.commit_panel_collapsed, "-collapsed")
        collapse_button.label = ">" if self.commit_panel_collapsed else "<"
        code_browser.styles.width = "1fr" if code_browser.display else "auto"
        if inline_quiz.display:
            inline_quiz.styles.width = "100%"
            available_height = max(main_area.size.height - 3, 12)
            inline_quiz.styles.height = str(available_height)
        else:
            inline_quiz.styles.width = "auto"
            inline_quiz.styles.height = "1fr"
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
        dock_quiz = self.query_one("#inline-quiz-dock", InlineQuizDock)
        if dock_quiz.display:
            self._show_inline_quiz(widget_id="inline-quiz-dock")
        if self.result_tab == "inline":
            self._restore_inline_widget_for_selection(widget_id="result-inline-widget")

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
        session_marker = self._commit_session_marker(commit["sha"])
        if session_marker is not None:
            marker_text, marker_style = session_marker
            line.append(f"{marker_text} ", style=marker_style)
        else:
            line.append("  ")
        style = (
            "bold bright_cyan"
            if commit["sha"] in self.unseen_auto_refresh_commit_shas
            else ""
        )
        line.append(f"{commit['subject']}", style=style)
        return line

    def _commit_session_marker(self, commit_sha: str) -> tuple[str, str] | None:
        try:
            repo_source = self._current_repo_source()
            github_repo_url = self.github_repo_url
            local_repo_root = self._current_local_repo_root()
        except Exception:
            repo_source = self.repo_source
            github_repo_url = self.github_repo_url
            local_repo_root = self.local_repo_root if repo_source == "local" else None
        session = load_learning_session(
            make_session_id([commit_sha]),
            repo_source=repo_source,
            github_repo_url=github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        if session is None:
            return None
        read_status = str(session.get("read", {}).get("status", "not_started"))
        quiz_status = str(session.get("general_quiz", {}).get("status", "not_started"))
        inline_status = str(session.get("inline_quiz", {}).get("status", "not_started"))
        review_ready = (
            read_status == "completed"
            and quiz_status == "graded"
            and inline_status == "graded"
        )
        if review_ready:
            return ("·", "grey62")
        return ("●", "green")

    def _load_more_label_text(self) -> Text:
        line = Text(" + ", style="bold cyan")
        line.append(f"Load More Commits (+{DEFAULT_COMMIT_LIST_LIMIT})", style="bold")
        return line

    def _load_all_label_text(self) -> Text:
        line = Text(" + ", style="bold cyan")
        line.append("Load All Commits", style="bold")
        return line

    def _commit_panel_help_text(self) -> str:
        selected_count = len(self._effective_selected_indices())
        selection_help = selection_help_text(
            CommitSelection(
                start_index=self.selected_range_start_index,
                end_index=self.selected_range_end_index,
            ),
            selected_count,
        )
        if self.selected_range_start_index is None and self.commits:
            selection_help = "현재 하이라이트 커밋이 선택 기준"
        return "\n".join(
            [
                f"Selection: {self._selected_range_summary()}",
                selection_help,
            ]
        )

    def _commit_panel_loaded_text(self) -> str:
        return f"Loaded {len(self.commits)}/{self.total_commit_count}"

    def _show_commit_summary(self, index: int) -> None:
        if not self.commits:
            return
        return

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
        changed_files = [
            line
            for line in context["changed_files_summary"].splitlines()
            if line.strip()
        ]
        detail_lines = [
            f"SHA: {context['commit_sha']}",
            f"Subject: {context['commit_subject']}",
            f"Author: {context['commit_author']}",
            f"Date: {context['commit_date']}",
            f"Changed files: {len(changed_files)}",
        ]
        detail = "\n".join(detail_lines)
        self.commit_detail_cache[commit["sha"]] = detail
        return detail

    def _update_commit_detail(self, index: int) -> None:
        detail_view = self.query_one("#commit-preview-view", Static)
        detail_view.update(self._commit_detail_text(index))

    def _current_commit_mode(self) -> str:
        if self.is_mounted:
            try:
                pressed = self.query_one("#commit-mode", RadioSet).pressed_button
            except Exception:
                pressed = None
            if pressed is not None:
                return pressed.id.removeprefix("mode-")
        return self.saved_commit_mode or "auto"

    def _current_repo_source(self) -> str:
        pressed = self.query_one("#repo-source", RadioSet).pressed_button
        if pressed is None:
            return "local"
        return "github" if pressed.id == "repo-github" else "local"

    def _current_github_repo_url(self) -> str | None:
        url = self.query_one("#repo-location", Input).value.strip()
        if not url:
            return self.github_repo_url or None
        if self._current_repo_source() == "github":
            try:
                normalize_github_repo_url(url)
            except ValueError:
                return self.github_repo_url or None
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

    def _api_key_status_text(self) -> str:
        api_key, source = get_openai_api_key()
        if api_key:
            if source == "env":
                return "환경변수 OPENAI_API_KEY 사용 중"
            if source == "file":
                return "전역 secrets.json 에 저장됨"
            if source == "session":
                return "이번 실행 동안만 저장됨"
        if self.api_key_mode == "file":
            return "전역 파일 저장 모드, 아직 키 없음"
        return "세션 전용 모드, 아직 키 없음"

    def _refresh_api_key_status(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#api-key-status", Static).update(self._api_key_status_text())

    def _ensure_api_key(self, *, action_label: str) -> bool:
        api_key, _ = get_openai_api_key()
        if api_key:
            return True
        self.prompt_api_key_settings(action_label=action_label)
        return False

    def prompt_api_key_settings(self, *, action_label: str) -> None:
        self._set_status("OpenAI API Key 설정이 필요합니다.")
        self._set_result(
            f"{action_label}을(를) 실행하려면 OpenAI API Key를 먼저 설정해 주세요."
        )
        self.push_screen(
            ApiKeyScreen(
                current_mode=self.api_key_mode,
                current_status=self._api_key_status_text(),
                settings_path=get_settings_path(),
                secrets_path=get_secrets_path(),
            ),
            self._handle_api_key_screen_closed,
        )

    def _save_app_state(self) -> None:
        highlighted_commit_sha = self._selected_commit_sha() or ""
        selected_range_start_sha = ""
        selected_range_end_sha = ""
        if (
            self.selected_range_start_index is not None
            and 0 <= self.selected_range_start_index < len(self.commits)
        ):
            selected_range_start_sha = self.commits[self.selected_range_start_index]["sha"]
        if (
            self.selected_range_end_index is not None
            and 0 <= self.selected_range_end_index < len(self.commits)
        ):
            selected_range_end_sha = self.commits[self.selected_range_end_index]["sha"]
        save_app_state(
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            commit_mode=self._current_commit_mode(),
            difficulty=self._current_difficulty(),
            quiz_style=self._current_quiz_style(),
            request_text=self._current_request("basic", use_fallback=False),
            read_request_text=self._current_request("read", use_fallback=False),
            basic_request_text=self._current_request("basic", use_fallback=False),
            inline_request_text=self._current_request("inline", use_fallback=False),
            grading_request_text=self._current_request("grading", use_fallback=False),
            highlighted_commit_sha=highlighted_commit_sha,
            selected_range_start_sha=selected_range_start_sha,
            selected_range_end_sha=selected_range_end_sha,
            local_repo_root=self.local_repo_root,
        )

    def _restore_saved_commit_selection(self) -> None:
        if not self.commits:
            return

        sha_to_index = {
            str(commit.get("sha", "")): index for index, commit in enumerate(self.commits)
        }
        start_index = sha_to_index.get(self.saved_selected_range_start_sha)
        end_index = sha_to_index.get(self.saved_selected_range_end_sha)
        highlighted_index = sha_to_index.get(self.saved_highlighted_commit_sha)

        if start_index is not None:
            self.selected_range_start_index = start_index
            self.selected_range_end_index = end_index
            self.selected_commit_index = (
                highlighted_index if highlighted_index is not None else start_index
            )
            return

        if highlighted_index is not None:
            self.selected_commit_index = highlighted_index

    def _ensure_selected_commit_mode(self) -> None:
        self.saved_commit_mode = "selected"
        if not self.is_mounted:
            return
        try:
            selected_button = self.query_one("#mode-selected", RadioButton)
        except Exception:
            return
        if not selected_button.value:
            selected_button.value = True

    def _current_local_repo_root(self) -> Path | None:
        if self._current_repo_source() != "local":
            return None
        try:
            local_repo = get_repo(repo_source="local", refresh_remote=False)
        except Exception:
            return self.local_repo_root
        working_tree_dir = local_repo.working_tree_dir
        if not working_tree_dir:
            return self.local_repo_root
        repo_root = Path(working_tree_dir).resolve()
        self.local_repo_root = repo_root
        return repo_root

    def _current_session_selected_shas(self) -> list[str]:
        return self._effective_selected_commit_shas()

    def _generation_commit_selection_payload(self) -> dict[str, object]:
        selected_shas = self._effective_selected_commit_shas()
        if selected_shas:
            payload: dict[str, object] = {
                "commit_mode": "selected",
                "requested_commit_shas": selected_shas,
            }
            if len(selected_shas) == 1:
                payload["requested_commit_sha"] = selected_shas[0]
            return payload
        return {"commit_mode": self._current_commit_mode()}

    def _current_learning_session_id(self) -> str:
        selected_shas = self._current_session_selected_shas()
        if not selected_shas:
            return ""
        return make_session_id(selected_shas)

    def _session_id_from_target(self, target: dict[str, object] | None) -> str:
        if target is None:
            return ""
        return str(target.get("session_id", "")).strip()

    def _target_matches_current_selection(
        self, target: dict[str, object] | None
    ) -> bool:
        target_session_id = self._session_id_from_target(target)
        current_session_id = self._current_learning_session_id()
        return bool(target_session_id and current_session_id and target_session_id == current_session_id)

    def _current_quiz_generation_in_progress(self) -> bool:
        current_session_id = self._current_learning_session_id()
        return bool(current_session_id and current_session_id in self._pending_quiz_targets)

    def _current_quiz_progress_label(self) -> str:
        current_session_id = self._current_learning_session_id()
        if not current_session_id:
            return ""
        return self._quiz_progress_labels.get(current_session_id, "")

    def _current_quiz_error_message(self) -> str:
        current_session_id = self._current_learning_session_id()
        if not current_session_id:
            return ""
        return self._quiz_error_messages.get(current_session_id, "")

    def _current_read_generation_in_progress(self) -> bool:
        current_session_id = self._current_learning_session_id()
        return bool(current_session_id and current_session_id in self._pending_read_targets)

    def _current_general_quiz_grading_in_progress(self) -> bool:
        return self._general_quiz_grading_in_progress and self._target_matches_current_selection(
            self._pending_general_quiz_grade_target
        )

    def _current_learning_session_selection(self) -> dict | None:
        selected_shas = self._current_session_selected_shas()
        if not selected_shas:
            return None
        highlighted_sha = self._selected_commit_sha() or selected_shas[0]
        return {
            "mode": "range" if len(selected_shas) > 1 else "single",
            "commit_mode": "selected",
            "highlighted_commit_sha": highlighted_sha,
            "start_sha": selected_shas[0],
            "end_sha": selected_shas[-1],
            "selected_shas": selected_shas,
            "range_summary": self._selected_range_summary(),
        }

    def _load_or_create_current_learning_session(self) -> dict | None:
        return self._load_or_create_learning_session_for_target(
            self._capture_session_target()
        )

    def _load_current_learning_session(self) -> dict | None:
        return self._load_learning_session_for_target(self._capture_session_target())

    def _capture_session_target(self) -> dict[str, object] | None:
        session_id = self._current_learning_session_id()
        selection = self._current_learning_session_selection()
        local_repo_root = self._current_local_repo_root()
        if not session_id or selection is None:
            return None
        return {
            "session_id": session_id,
            "selection": selection,
            "repo_source": self._current_repo_source(),
            "github_repo_url": self.github_repo_url,
            "local_repo_root": (
                str(local_repo_root) if local_repo_root is not None else None
            ),
            "preferences": {
                "difficulty": self._current_difficulty(),
                "quiz_style": self._current_quiz_style(),
                "request_text": self._current_request("basic"),
                "read_request_text": self._current_request("read"),
                "basic_request_text": self._current_request("basic"),
                "inline_request_text": self._current_request("inline"),
                "grading_request_text": self._current_request("grading"),
            },
        }

    def _load_or_create_learning_session_for_target(
        self,
        target: dict[str, object] | None,
    ) -> dict | None:
        if target is None:
            return None
        session_id = str(target.get("session_id", "")).strip()
        selection = target.get("selection")
        repo_source = str(target.get("repo_source", "local"))
        github_repo_url = str(target.get("github_repo_url", ""))
        local_repo_root = target.get("local_repo_root")
        preferences = target.get("preferences") or {}
        if not session_id or not isinstance(selection, dict):
            return None
        session = load_learning_session(
            session_id,
            repo_source=repo_source,
            github_repo_url=github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        if session is not None:
            return session
        return create_learning_session(
            session_id=session_id,
            repo={
                "source": repo_source,
                "github_repo_url": github_repo_url,
                "repository_label": (
                    str(local_repo_root)
                    if local_repo_root is not None
                    else (github_repo_url or "unknown")
                ),
            },
            selection=selection,
            preferences={
                "difficulty": str(preferences.get("difficulty", self._current_difficulty())),
                "quiz_style": str(preferences.get("quiz_style", self._current_quiz_style())),
                "request_text": str(
                    preferences.get("request_text", self._current_request("basic"))
                ),
                "read_request_text": str(
                    preferences.get(
                        "read_request_text",
                        preferences.get("request_text", self._current_request("read")),
                    )
                ),
                "basic_request_text": str(
                    preferences.get(
                        "basic_request_text",
                        preferences.get("request_text", self._current_request("basic")),
                    )
                ),
                "inline_request_text": str(
                    preferences.get(
                        "inline_request_text",
                        preferences.get("request_text", self._current_request("inline")),
                    )
                ),
                "grading_request_text": str(
                    preferences.get(
                        "grading_request_text",
                        preferences.get("request_text", self._current_request("grading")),
                    )
                ),
            },
            now=now_timestamp(),
        )

    def _load_learning_session_for_target(
        self,
        target: dict[str, object] | None,
    ) -> dict | None:
        if target is None:
            return None
        session_id = str(target.get("session_id", "")).strip()
        repo_source = str(target.get("repo_source", "local"))
        github_repo_url = str(target.get("github_repo_url", ""))
        local_repo_root = target.get("local_repo_root")
        if not session_id:
            return None
        return load_learning_session(
            session_id,
            repo_source=repo_source,
            github_repo_url=github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )

    def _inline_saved_state_for_current_selection(self) -> InlineQuizSavedState | None:
        session = self._load_current_learning_session()
        if session is None:
            return None
        inline_quiz = session.get("inline_quiz", {})
        questions = list(inline_quiz.get("questions", []))
        if not questions:
            return None
        attempt = inline_quiz.get("attempt", {})
        return InlineQuizSavedState(
            questions=questions,
            answers=dict(attempt.get("answers", {})),
            grades=list(attempt.get("grades", [])),
            current_index=int(inline_quiz.get("current_index", 0)),
            known_files=dict(inline_quiz.get("known_files", {})),
        )

    def _save_inline_quiz_state_for_selection(
        self,
        state: InlineQuizSavedState,
        grading_summary: GradingSummary | None = None,
        *,
        target: dict[str, object] | None = None,
    ) -> None:
        session_target = target or self._capture_session_target()
        session = self._load_or_create_learning_session_for_target(session_target)
        if session is None:
            return
        stamp = now_timestamp()
        has_answers = any(answer.strip() for answer in state["answers"].values())
        grades = list(state["grades"])
        inline_quiz = session["inline_quiz"]
        inline_quiz["questions"] = list(state["questions"])
        inline_quiz["attempt"] = {
            "answers": dict(state["answers"]),
            "submitted_at": stamp if grades else None,
            "grades": grades,
            "score_summary": (
                {
                    "total": sum(grade["score"] for grade in grades) // len(grades),
                    "max": 100,
                }
                if grades
                else None
            ),
        }
        inline_quiz["current_index"] = state["current_index"]
        inline_quiz["known_files"] = dict(state["known_files"])
        inline_quiz["updated_at"] = stamp
        if state["questions"] and inline_quiz["version"] == 0:
            inline_quiz["version"] = 1
        inline_quiz["status"] = (
            "graded"
            if grades
            else ("in_progress" if has_answers else ("ready" if state["questions"] else "not_started"))
        )
        if grades:
            inline_quiz["grading_summary"] = (
                grading_summary
                if grading_summary is not None
                else inline_quiz.get("grading_summary")
            )
        else:
            inline_quiz["grading_summary"] = None
        session["session_meta"]["current_step"] = (
            "review" if grades else "inline_quiz"
        )
        session["session_meta"]["updated_at"] = stamp
        session["preferences"] = dict(session_target.get("preferences", {}))
        self._refresh_review_summary_for_session(session)
        save_learning_session(
            session,
            repo_source=str(session_target.get("repo_source", "local")),
            github_repo_url=str(session_target.get("github_repo_url", "")),
            local_repo_root=(
                str(session_target.get("local_repo_root"))
                if session_target.get("local_repo_root") is not None
                else None
            ),
        )
        self._rebuild_inline_content_for_current_selection()
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _persist_open_inline_quiz_state(self) -> None:
        widget_ids = ["inline-quiz-dock", "result-inline-widget"]
        for widget_id in widget_ids:
            try:
                inline_quiz = self.query_one(f"#{widget_id}", InlineQuizWidget)
            except Exception:
                continue
            if widget_id == "inline-quiz-dock" and not inline_quiz.display:
                continue
            if not inline_quiz.questions:
                continue
            inline_quiz._save_current_answer()
            target = self._inline_session_targets.get(inline_quiz.cache_key)
            if target is None:
                return
            self._save_inline_quiz_state_for_selection(
                inline_quiz.get_saved_state(),
                target=target,
            )
            return

    def _rebuild_inline_content_for_current_selection(self) -> None:
        session = self._load_current_learning_session()
        if session is None:
            self.inline_content = "인라인 퀴즈가 아직 없습니다."
            if self.result_tab == "inline":
                self._set_tab_result("inline", self.inline_content, store=False)
            return

        inline_quiz = session.get("inline_quiz", {})
        questions = list(inline_quiz.get("questions", []))
        answers = dict(inline_quiz.get("attempt", {}).get("answers", {}))
        grades = list(inline_quiz.get("attempt", {}).get("grades", []))
        status = str(inline_quiz.get("status", "not_started"))

        answered_count = sum(1 for answer in answers.values() if str(answer).strip())
        lines = [
            "## 인라인 퀴즈",
            "",
            f"- 상태: {status}",
            f"- 질문 수: {len(questions)}",
            f"- 답변 수: {answered_count}",
            f"- 채점 수: {len(grades)}",
        ]

        if grades:
            avg_score = sum(int(grade.get("score", 0)) for grade in grades) // len(grades)
            lines.append(f"- 평균 점수: {avg_score}점")

        if questions:
            lines.extend(
                [
                    "",
                    "### 현재 질문",
                ]
            )
            for question in questions[:4]:
                lines.append(
                    f"- {question.get('id', '').upper()}: {str(question.get('question', ''))[:80]}"
                )
            lines.extend(
                [
                    "",
                    "`Open Inline` 버튼이나 `Inline` 탭으로 인라인 퀴즈 워크스페이스를 열 수 있습니다.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "아직 생성된 인라인 질문이 없습니다.",
                    "",
                    "다음 단계:",
                    "1. `Inline` 버튼으로 인라인 퀴즈 워크스페이스 열기",
                    "2. 질문 생성 및 답변 작성",
                    "3. 채점 후 `Review` 탭에서 결과 확인",
                ]
            )

        self.inline_content = "\n".join(lines)
        if self.result_tab == "inline":
            self._set_tab_result("inline", self.inline_content, store=False)

    def _current_general_quiz_session(self) -> dict | None:
        session = self._load_current_learning_session()
        if session is None:
            return None
        return session.get("general_quiz")

    def _save_general_quiz_answer_for_selection(
        self,
        *,
        question_id: str,
        answer: str,
        current_index: int,
    ) -> None:
        session = self._load_or_create_current_learning_session()
        local_repo_root = self._current_local_repo_root()
        if session is None:
            return
        save_general_quiz_answer(
            session,
            question_id=question_id,
            answer=answer,
            current_index=current_index,
            now=now_timestamp(),
        )
        self._refresh_review_summary_for_session(session)
        save_learning_session(
            session,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        self._rebuild_review_content_for_current_selection()
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _mark_read_done_for_selection(self) -> None:
        session = self._load_or_create_current_learning_session()
        local_repo_root = self._current_local_repo_root()
        if session is None:
            return
        if not str(session.get("read", {}).get("content", "")).strip():
            self._set_status("완료 처리할 읽을거리가 없습니다.")
            return
        mark_read_completed(session, now=now_timestamp())
        self._refresh_review_summary_for_session(session)
        save_learning_session(
            session,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        self._rebuild_review_content_for_current_selection()
        self._refresh_session_progress()
        self._refresh_top_bar_context()
        self._refresh_result_command_row()
        self._set_status("읽을거리 학습을 완료로 표시했습니다.")

    def _retry_general_quiz_for_selection(self) -> None:
        session = self._load_or_create_current_learning_session()
        local_repo_root = self._current_local_repo_root()
        if session is None:
            return
        retry_general_quiz(session, now=now_timestamp())
        save_learning_session(
            session,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        self._rebuild_review_content_for_current_selection()
        self._set_result_tab("quiz")
        self._set_status("일반 퀴즈 답변과 채점 결과를 초기화했습니다.")
        self._refresh_quiz_workspace()
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _grade_general_quiz_for_selection(self) -> None:
        self._persist_general_quiz_workspace_answer()
        if not self._ensure_api_key(action_label="일반 퀴즈 채점"):
            return
        session = self._load_or_create_current_learning_session()
        if session is None:
            return

        general_quiz = session.get("general_quiz", {})
        questions = list(general_quiz.get("questions", []))
        answers = dict(general_quiz.get("attempt", {}).get("answers", {}))
        if not questions:
            self._set_status("채점할 일반 퀴즈가 없습니다.")
            return
        target = self._capture_session_target()
        self._general_quiz_grading_in_progress = True
        self._badge_animation_frame = 0
        self._pending_general_quiz_grade_target = target
        self.query_one("#result-quiz-grade", Button).disabled = True
        self.query_one("#result-quiz-grade-top", Button).disabled = True
        self.query_one("#result-quiz-retry", Button).disabled = True
        self.query_one("#result-quiz-retry-top", Button).disabled = True
        self._set_status("일반 퀴즈 채점 중...")
        self._set_result_tab("quiz")
        self._refresh_quiz_workspace()
        self.grade_general_quiz(
            questions,
            answers,
            user_request=self._current_request("grading"),
            target=target,
        )

    def _retry_inline_quiz_for_selection(self) -> None:
        session = self._load_or_create_current_learning_session()
        local_repo_root = self._current_local_repo_root()
        if session is None:
            return
        retry_inline_quiz(session, now=now_timestamp())
        save_learning_session(
            session,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        inline_quiz = self.query_one("#result-inline-widget", InlineQuizWidget)
        if self.commits:
            selected_indices = sorted(self._selected_commit_indices())
            newest_index = min(selected_indices) if selected_indices else self.selected_commit_index
            if newest_index < len(self.commits):
                target_sha = self.commits[newest_index]["sha"]
                session_id = self._current_learning_session_id() or target_sha
                repo = get_repo(**self._repo_args(refresh_remote=False))
                selected_commit = repo.commit(target_sha)
                commit_context = build_commit_context(selected_commit, "selected_commit", repo)
                saved_state = self._inline_saved_state_for_current_selection()
                if saved_state is not None and saved_state["questions"]:
                    target = self._capture_session_target()
                    if target is not None:
                        self._inline_session_targets[session_id] = target
                    inline_quiz.show_quiz(
                        commit_context=commit_context,
                        repo=repo,
                        target_commit_sha=target_sha,
                        title_suffix=self._selected_commit_title_suffix(),
                        saved_state=saved_state,
                        cache_key=session_id,
                        user_request=self._current_request("inline"),
                        grading_request=self._current_request("grading"),
                    )
                elif not commit_context.get("diff_text"):
                    inline_quiz.show_placeholder(
                        "텍스트 diff가 있는 커밋을 선택하면 인라인 퀴즈를 생성할 수 있습니다."
                    )
                else:
                    inline_quiz.show_placeholder(
                        "`Inline Quiz` 버튼을 누르면 여기서 인라인 퀴즈를 생성할 수 있습니다."
                    )
            else:
                inline_quiz.show_placeholder("표시할 커밋이 없습니다.")
        else:
            inline_quiz.show_placeholder("표시할 커밋이 없습니다.")
        self._rebuild_inline_content_for_current_selection()
        self._rebuild_review_content_for_current_selection()
        self._set_result_tab("inline")
        self._set_status("인라인 퀴즈 답변과 채점 결과를 초기화했습니다.")
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _grade_inline_quiz_for_selection(self) -> None:
        inline_quiz = self.query_one("#result-inline-widget", InlineQuizWidget)
        if not inline_quiz.questions:
            self._set_status("채점할 인라인 퀴즈가 없습니다.")
            return
        if not self._ensure_api_key(action_label="인라인 퀴즈 채점"):
            return
        self._badge_animation_frame = 0
        inline_quiz._save_current_answer()
        self._save_inline_quiz_state_for_selection(inline_quiz.get_saved_state())
        if inline_quiz.cache_key:
            self.notify_inline_grade_started(inline_quiz.cache_key)
        inline_quiz.handle_grade()

    def _persist_general_quiz_workspace_answer(self) -> None:
        if self.result_tab != "quiz":
            return
        general_quiz = self._current_general_quiz_session()
        if not general_quiz:
            return
        questions = list(general_quiz.get("questions", []))
        current_index = int(general_quiz.get("current_index", 0))
        if not questions or current_index >= len(questions):
            return
        answer = self.query_one("#result-quiz-answer", TextArea).text
        self._save_general_quiz_answer_for_selection(
            question_id=str(questions[current_index].get("id", "")),
            answer=answer,
            current_index=current_index,
        )

    def _navigate_general_quiz_question(self, delta: int) -> None:
        general_quiz = self._current_general_quiz_session()
        if not general_quiz:
            return
        questions = list(general_quiz.get("questions", []))
        if not questions:
            return
        self._persist_general_quiz_workspace_answer()
        current_index = int(general_quiz.get("current_index", 0))
        next_index = max(0, min(current_index + delta, len(questions) - 1))
        session = self._load_or_create_current_learning_session()
        local_repo_root = self._current_local_repo_root()
        if session is None:
            return
        session["general_quiz"]["current_index"] = next_index
        session["general_quiz"]["updated_at"] = now_timestamp()
        save_learning_session(
            session,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        self._refresh_quiz_workspace()

    def _rebuild_result_quiz_nav(
        self,
        questions: list[dict],
        current_index: int,
        answers: dict[str, str],
    ) -> None:
        try:
            nav = self.query_one("#result-quiz-nav", Horizontal)
        except Exception:
            return
        nav.remove_children()
        self._result_quiz_nav_build_serial += 1
        for index, question in enumerate(questions):
            answered = bool(str(answers.get(str(question.get("id", "")), "")).strip())
            classes = "result-quiz-nav-btn"
            if index == current_index:
                classes += " -active"
            elif answered:
                classes += " -answered"
            nav.mount(
                Button(
                    f"{index + 1}✓" if answered else str(index + 1),
                    id=f"result-quiz-nav-{self._result_quiz_nav_build_serial}-{index}",
                    classes=classes,
                    compact=True,
                    flat=True,
                )
            )

    def _refresh_quiz_workspace(self) -> None:
        try:
            quiz_workspace = self.query_one("#result-quiz-view", Vertical)
        except Exception:
            return

        general_quiz = self._current_general_quiz_session()
        questions = list(general_quiz.get("questions", [])) if general_quiz else []
        show_workspace = self.result_tab == "quiz"
        quiz_workspace.display = show_workspace
        if show_workspace:
            if self._current_quiz_generation_in_progress():
                quiz_dots = "+" * max(1, ((self._quiz_animation_frame - 1) % 3) + 1)
                current_progress_label = self._current_quiz_progress_label()
                progress_widget = self.query_one("#result-quiz-progress", Static)
                self.query_one("#result-quiz-meta", Label).update("일반 퀴즈 생성 중")
                progress_widget.update(
                    f"[{current_progress_label}]{quiz_dots}"
                    if current_progress_label
                    else quiz_dots
                )
                progress_widget.display = True
                self.query_one("#result-quiz-question", Static).update(
                    "선택한 커밋 범위를 기준으로 일반 퀴즈를 만들고 있습니다."
                )
                self._rebuild_result_quiz_nav([], 0, {})
                answer_input = self.query_one("#result-quiz-answer", TextArea)
                answer_input.text = ""
                answer_input.disabled = True
                self.query_one("#result-quiz-feedback", Static).update(
                    "퀴즈 생성이 끝나면 여기서 바로 풀이할 수 있습니다."
                )
                self.query_one("#result-quiz-status", Static).update("")
                self.query_one("#result-quiz-prev", Button).disabled = True
                self.query_one("#result-quiz-next", Button).disabled = True
                self.query_one("#result-quiz-retry", Button).disabled = True
                self.query_one("#result-quiz-grade", Button).disabled = True
                return
            progress_widget = self.query_one("#result-quiz-progress", Static)
            progress_widget.update("")
            progress_widget.display = False
            current_error_message = self._current_quiz_error_message()
            if not questions:
                if current_error_message:
                    self.query_one("#result-quiz-meta", Label).update("일반 퀴즈 생성 오류")
                    self.query_one("#result-quiz-question", Static).update(
                        current_error_message
                    )
                else:
                    self.query_one("#result-quiz-meta", Label).update(
                        "일반 퀴즈가 아직 없습니다."
                    )
                    self.query_one("#result-quiz-question", Static).update(
                        "Generate Quiz를 누르면 일반 퀴즈를 만들고, 여기서 답변과 채점을 진행할 수 있습니다."
                    )
                self._rebuild_result_quiz_nav([], 0, {})
                answer_input = self.query_one("#result-quiz-answer", TextArea)
                answer_input.text = ""
                answer_input.disabled = True
                self.query_one("#result-quiz-feedback", Static).update("")
                self.query_one("#result-quiz-status", Static).update(
                    "status=error" if current_error_message else "status=empty"
                )
                self.query_one("#result-quiz-prev", Button).disabled = True
                self.query_one("#result-quiz-next", Button).disabled = True
                self.query_one("#result-quiz-retry", Button).disabled = True
                self.query_one("#result-quiz-grade", Button).disabled = True
                return
            current_index = max(0, min(int(general_quiz.get("current_index", 0)), len(questions) - 1))
            current_question = questions[current_index]
            answers = dict(general_quiz.get("attempt", {}).get("answers", {}))
            general_quiz_graded = str(general_quiz.get("status", "not_started")) == "graded"
            type_label = self._review_type_label(
                str(current_question.get("question_type", "intent"))
            )
            self.query_one("#result-quiz-meta", Label).update(
                f"[{current_index + 1}/{len(questions)}]  {type_label}"
            )
            progress_widget.update("")
            progress_widget.display = False
            self.query_one("#result-quiz-question", Static).update(
                "\n".join(
                    [
                        str(current_question.get("question", "")),
                        "",
                        *(
                            [f"- {choice}" for choice in current_question.get("choices", [])]
                            if current_question.get("choices")
                            else []
                        ),
                    ]
                ).strip()
            )
            answer_input = self.query_one("#result-quiz-answer", TextArea)
            self._restoring_general_quiz_answer = True
            answer_input.text = str(answers.get(current_question.get("id", ""), ""))
            self._restoring_general_quiz_answer = False
            answer_input.disabled = general_quiz_graded
            self._rebuild_result_quiz_nav(questions, current_index, answers)
            grades = {
                str(grade.get("id", "")): grade
                for grade in general_quiz.get("attempt", {}).get("grades", [])
            }
            current_grade = grades.get(str(current_question.get("id", "")))
            feedback = str(current_grade.get("feedback", "")).strip() if current_grade else ""
            self.query_one("#result-quiz-feedback", Static).update(
                self._quiz_feedback_renderable(
                    current_index=current_index,
                    total_questions=len(questions),
                    type_label=type_label,
                    score=int(current_grade.get("score", 0)),
                    feedback=feedback,
                )
                if current_grade is not None
                else "답변을 작성한 뒤 Grade로 채점할 수 있습니다."
            )
            self.query_one("#result-quiz-prev", Button).disabled = current_index <= 0
            self.query_one("#result-quiz-next", Button).disabled = (
                current_index >= len(questions) - 1
            )
            self.query_one("#result-quiz-retry", Button).disabled = False
            self.query_one("#result-quiz-grade", Button).disabled = (
                self._general_quiz_grading_in_progress or general_quiz_graded
            )
            self.query_one("#result-quiz-retry", Button).disabled = self._general_quiz_grading_in_progress
            if self._general_quiz_grading_in_progress:
                self.query_one("#result-quiz-feedback", Static).update(
                    "현재 답변을 채점 중입니다. 잠시만 기다려 주세요."
                )
            return

    def _save_general_quiz_result_for_selection(
        self,
        content: str,
        questions: list[GeneralQuizQuestion] | None = None,
        *,
        target: dict[str, object] | None = None,
    ) -> None:
        session_target = target or self._capture_session_target()
        session = self._load_or_create_learning_session_for_target(session_target)
        if session is None:
            return
        save_general_quiz_rendered_result(
            session,
            rendered_markdown=content,
            questions=questions,
            now=now_timestamp(),
        )
        session["preferences"] = dict(session_target.get("preferences", {}))
        self._refresh_review_summary_for_session(session)
        save_learning_session(
            session,
            repo_source=str(session_target.get("repo_source", "local")),
            github_repo_url=str(session_target.get("github_repo_url", "")),
            local_repo_root=(
                str(session_target.get("local_repo_root"))
                if session_target.get("local_repo_root") is not None
                else None
            ),
        )
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _save_read_result_for_selection(
        self,
        content: str,
        *,
        target: dict[str, object] | None = None,
    ) -> None:
        session_target = target or self._capture_session_target()
        session = self._load_or_create_learning_session_for_target(session_target)
        if session is None:
            return
        regenerate_read_step(
            session,
            content=content,
            summary=session["read"].get("summary"),
            now=now_timestamp(),
        )
        session["preferences"] = dict(session_target.get("preferences", {}))
        self._refresh_review_summary_for_session(session)
        save_learning_session(
            session,
            repo_source=str(session_target.get("repo_source", "local")),
            github_repo_url=str(session_target.get("github_repo_url", "")),
            local_repo_root=(
                str(session_target.get("local_repo_root"))
                if session_target.get("local_repo_root") is not None
                else None
            ),
        )
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _restore_learning_content_for_current_selection(self) -> None:
        session = self._load_current_learning_session()
        if session is None:
            self.read_content = self._default_result_text()
            self.quiz_content = self._default_result_text()
            self.inline_content = "인라인 퀴즈가 아직 없습니다."
            self._rebuild_review_content_for_current_selection()
            self._set_result_tab(self.result_tab)
            self._refresh_session_progress()
            self._refresh_top_bar_context()
            return
        rendered_markdown = str(
            session.get("general_quiz", {}).get("rendered_markdown", "")
        ).strip()
        read_content = str(session.get("read", {}).get("content", "")).strip()
        self.read_content = read_content or self._default_result_text()
        self.quiz_content = rendered_markdown or self._default_result_text()
        self._rebuild_inline_content_for_current_selection()
        self._rebuild_review_content_for_current_selection()
        if self.result_tab == "inline":
            self._restore_inline_widget_for_selection(widget_id="result-inline-widget")
        if rendered_markdown:
            self._set_status("저장된 일반 퀴즈 결과를 복원했습니다.")
        elif read_content:
            self._set_status("저장된 읽을거리를 복원했습니다.")
        self._set_result_tab(self.result_tab)
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    def _reset_repo_tracking(self) -> None:
        self.commit_list_limit = DEFAULT_COMMIT_LIST_LIMIT
        self._last_seen_head_sha = ""
        self._last_seen_total_commit_count = 0
        self._last_seen_repo_key = self._current_repo_key()
        self._last_remote_refresh_check_at = 0.0

    def _load_selected_repo(self, announce: str) -> None:
        self._persist_open_inline_quiz_state()
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
        if self.is_mounted:
            try:
                pressed = self.query_one("#difficulty", RadioSet).pressed_button
            except Exception:
                pressed = None
            if pressed is not None:
                return pressed.id.removeprefix("difficulty-").lower()
        return self.saved_difficulty or "medium"

    def _current_quiz_style(self) -> str:
        if self.is_mounted:
            try:
                pressed = self.query_one("#quiz-style", RadioSet).pressed_button
            except Exception:
                pressed = None
            if pressed is not None:
                return pressed.id.removeprefix("style-")
        return self.saved_quiz_style or "mixed"

    def _saved_request_value(self, kind: str) -> str:
        return {
            "read": self.saved_read_request,
            "basic": self.saved_basic_request,
            "inline": self.saved_inline_request,
            "grading": self.saved_grading_request,
        }.get(kind, "")

    def _set_saved_request_value(self, kind: str, value: str) -> None:
        if kind == "read":
            self.saved_read_request = value
        elif kind == "basic":
            self.saved_basic_request = value
        elif kind == "inline":
            self.saved_inline_request = value
        elif kind == "grading":
            self.saved_grading_request = value

    def _request_input_selector(self, kind: str) -> str:
        return f"#request-{kind}-input"

    def _request_fallback_text(self) -> str:
        return REQUEST_EXAMPLE_TEXT.removeprefix("예시 - ").strip()

    def _current_request(self, kind: str, *, use_fallback: bool = True) -> str:
        text = self._saved_request_value(kind).strip()
        if self.is_mounted:
            try:
                current_text = self.query_one(
                    self._request_input_selector(kind), TextArea
                ).text.strip()
            except Exception:
                current_text = ""
            if current_text:
                text = current_text
        if text:
            return text
        return self._request_fallback_text() if use_fallback else ""

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

    def _effective_selected_indices(self) -> set[int]:
        selected = self._selected_commit_indices()
        if selected:
            return selected
        if not self.commits:
            return set()
        if 0 <= self.selected_commit_index < len(self.commits):
            return {self.selected_commit_index}
        return set()

    def _selected_commit_shas(self) -> list[str]:
        return [
            self.commits[index]["sha"]
            for index in sorted(self._selected_commit_indices())
        ]

    def _effective_selected_commit_shas(self) -> list[str]:
        return [
            self.commits[index]["sha"]
            for index in sorted(self._effective_selected_indices())
            if index < len(self.commits)
        ]

    def _clear_selected_range(self) -> None:
        self.selected_range_start_index = None
        self.selected_range_end_index = None

    def _selected_range_summary(self) -> str:
        if not self.commits:
            return "없음"
        if self.selected_range_start_index is not None:
            start_commit = self.commits[self.selected_range_start_index]
            if self.selected_range_end_index is None:
                return f"S {start_commit['short_sha']}"
            end_commit = self.commits[self.selected_range_end_index]
            return f"S {start_commit['short_sha']} -> E {end_commit['short_sha']}"
        if self.commits and 0 <= self.selected_commit_index < len(self.commits):
            return f"Selection {self.commits[self.selected_commit_index]['short_sha']}"
        return "없음"

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
        selected_indices = sorted(self._effective_selected_indices())
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

    def _set_result(self, content: str, *, store: bool = True) -> None:
        self.result_content = content
        if self.result_tab == "quiz" and store:
            self.quiz_content = content
            self._refresh_quiz_workspace()

    def _set_tab_result(self, tab: str, content: str, *, store: bool = True) -> None:
        if store:
            if tab == "read":
                self.read_content = content
            elif tab == "inline":
                self.inline_content = content
            elif tab == "review":
                self.review_content = content
            else:
                self.quiz_content = content
        if tab in {"read", "inline"}:
            self._update_tab_markdown(tab, content)
        elif tab == "review":
            self._update_review_view(self._load_current_learning_session())
        elif tab == "quiz" and self.result_tab == "quiz":
            self._refresh_quiz_workspace()

    def _current_result_content_for_download(self) -> str:
        if self.result_tab in {"read", "quiz", "inline", "review"}:
            return self._content_for_result_tab(self.result_tab)
        return self.result_content

    def _set_result_view_mode(self, mode: str) -> None:
        self.result_view_mode = mode
        self._refresh_quiz_workspace()

    def _review_type_label(self, question_type: str) -> str:
        return {
            "intent": "의도",
            "behavior": "동작",
            "tradeoff": "트레이드오프",
            "vulnerability": "취약점/위험",
        }.get(question_type, question_type or "기타")

    def _score_gauge_text(self, score: int) -> str:
        clamped = max(0, min(100, int(score)))
        filled = clamped // 10
        return ("█" * filled) + ("░" * (10 - filled))

    def _score_feedback_renderable(self, score: int, feedback: str) -> Text:
        result = Text()
        result.append(self._score_gauge_text(score), style="green")
        result.append(f"  {score}점\n\n", style="bold yellow")
        result.append(feedback or "-")
        return result

    def _quiz_feedback_renderable(
        self,
        *,
        current_index: int,
        total_questions: int,
        type_label: str,
        score: int,
        feedback: str,
    ) -> Text:
        result = Text()
        result.append(
            f"채점 결과  [{current_index + 1}/{total_questions}]  {type_label}\n\n",
            style="bold bright_yellow",
        )
        result.append(self._score_gauge_text(score), style="green")
        result.append(f"  {score}점\n\n", style="bold yellow")
        result.append(feedback or "-")
        return result

    def _review_question_hint(self, question: dict) -> str:
        raw = str(question.get("question", "")).strip()
        collapsed = " ".join(raw.split())
        return collapsed[:48] + ("..." if len(collapsed) > 48 else "")

    def _merged_step_grading_summary(
        self, session: dict
    ) -> tuple[list[str], list[str], list[str], str]:
        summaries = [
            session.get("general_quiz", {}).get("grading_summary") or {},
            session.get("inline_quiz", {}).get("grading_summary") or {},
        ]
        weak_points: list[str] = []
        weak_files: list[str] = []
        next_steps: list[str] = []
        overall_comment = ""
        for summary in summaries:
            for item in summary.get("weak_points", []):
                text = str(item).strip()
                if text and text not in weak_points:
                    weak_points.append(text)
            for item in summary.get("weak_files", []):
                text = str(item).strip()
                if text and text not in weak_files:
                    weak_files.append(text)
            for item in summary.get("next_steps", []):
                text = str(item).strip()
                if text and text not in next_steps:
                    next_steps.append(text)
            if not overall_comment:
                overall_comment = str(summary.get("overall_comment", "")).strip()
        return weak_points[:4], weak_files[:4], next_steps[:4], overall_comment

    def _review_next_step_text(self, session: dict) -> str:
        read_status = str(session.get("read", {}).get("status", "not_started"))
        general_status = str(session.get("general_quiz", {}).get("status", "not_started"))
        inline_status = str(session.get("inline_quiz", {}).get("status", "not_started"))
        weak_points = session.get("review", {}).get("summary", {}).get("weak_points", [])
        weak_files = session.get("review", {}).get("summary", {}).get("weak_files", [])

        if read_status not in {"ready", "completed"}:
            return "Read를 먼저 생성해 변경 맥락을 읽어보세요."
        if general_status == "not_started":
            return "Basic Quiz를 생성해 전체 변경 이해를 점검하세요."
        if general_status in {"ready", "in_progress"}:
            return "Basic Quiz 답변을 마무리하고 Grade로 채점하세요."
        if inline_status == "not_started":
            return "Inline Quiz를 생성해 코드 앵커 단위로 이해를 점검하세요."
        if inline_status in {"ready", "in_progress"}:
            return "Inline Quiz 답변을 마무리하고 Grade로 채점하세요."
        if weak_points or weak_files:
            return "약한 유형과 파일을 중심으로 Retry 후 다시 풀어보세요."
        return "이번 범위 학습을 마무리했어요. 다음 커밋 범위로 넘어가도 좋습니다."

    def _refresh_review_summary_for_session(self, session: dict) -> dict:
        general_quiz = session.get("general_quiz", {})
        inline_quiz = session.get("inline_quiz", {})
        general_questions = {
            str(question.get("id", "")): question
            for question in general_quiz.get("questions", [])
        }
        inline_questions = {
            str(question.get("id", "")): question
            for question in inline_quiz.get("questions", [])
        }

        type_scores: dict[str, list[int]] = {}
        type_examples: dict[str, tuple[int, str]] = {}
        for grade in general_quiz.get("attempt", {}).get("grades", []):
            question = general_questions.get(str(grade.get("id", "")), {})
            question_type = str(question.get("question_type", ""))
            if question_type:
                score = int(grade.get("score", 0))
                type_scores.setdefault(question_type, []).append(score)
                example = self._review_question_hint(question)
                if question_type not in type_examples or score < type_examples[question_type][0]:
                    type_examples[question_type] = (score, example)
        for grade in inline_quiz.get("attempt", {}).get("grades", []):
            question = inline_questions.get(str(grade.get("id", "")), {})
            question_type = str(question.get("question_type", ""))
            if question_type:
                score = int(grade.get("score", 0))
                type_scores.setdefault(question_type, []).append(score)
                example = self._review_question_hint(question)
                if question_type not in type_examples or score < type_examples[question_type][0]:
                    type_examples[question_type] = (score, example)

        heuristic_weak_points = [
            (
                f"{self._review_type_label(question_type)} {int(sum(scores) / len(scores))}점"
                + (
                    f"  |  대표 질문: {type_examples[question_type][1]}"
                    if type_examples.get(question_type, ("", ""))[1]
                    else ""
                )
            )
            for question_type, scores in sorted(
                type_scores.items(),
                key=lambda item: (sum(item[1]) / len(item[1]), item[0]),
            )
            if scores and (sum(scores) / len(scores)) < 80
        ][:3]

        weak_files_with_scores: list[tuple[str, int]] = []
        for grade in inline_quiz.get("attempt", {}).get("grades", []):
            question = inline_questions.get(str(grade.get("id", "")), {})
            file_path = str(question.get("file_path", "")).strip()
            if file_path:
                weak_files_with_scores.append((file_path, int(grade.get("score", 0))))
        heuristic_weak_files = [
            file_path
            for file_path, _score in sorted(weak_files_with_scores, key=lambda item: item[1])
            if _score < 80
        ]
        heuristic_weak_files = list(dict.fromkeys(heuristic_weak_files))[:3]

        weak_points, weak_files, next_steps, overall_comment = self._merged_step_grading_summary(
            session
        )
        if not weak_points:
            weak_points = heuristic_weak_points
        if not weak_files:
            weak_files = heuristic_weak_files

        recommended_next_step = self._review_next_step_text(
            {
                **session,
                "review": {
                    **session.get("review", {}),
                    "summary": {
                        **(session.get("review", {}).get("summary") or {}),
                        "weak_points": weak_points,
                        "weak_files": weak_files,
                    },
                },
            }
        )
        if next_steps:
            recommended_next_step = " / ".join(next_steps)
        elif overall_comment and not recommended_next_step:
            recommended_next_step = overall_comment
        rebuild_review_summary(
            session,
            weak_points=weak_points,
            weak_files=weak_files,
            recommended_next_step=recommended_next_step,
            now=now_timestamp(),
        )
        return session

    def _rebuild_review_content_for_current_selection(self) -> None:
        session = self._load_current_learning_session()
        if session is None:
            self.review_content = "리뷰 결과가 아직 없습니다."
            self._update_review_view(None)
            if self.result_tab == "review":
                self._set_tab_result("review", self.review_content, store=False)
            return

        review = session.get("review", {})
        summary = review.get("summary")
        read_status = str(session.get("read", {}).get("status", "not_started"))
        general_quiz = session.get("general_quiz", {})
        inline_quiz = session.get("inline_quiz", {})
        general_status = str(general_quiz.get("status", "not_started"))
        inline_status = str(inline_quiz.get("status", "not_started"))
        general_question_count = len(general_quiz.get("questions", []))
        inline_question_count = len(inline_quiz.get("questions", []))

        if not review.get("available") or summary is None:
            self.review_content = "\n".join(
                [
                    "## 학습 세션 리뷰",
                    "",
                    "아직 요약할 학습 결과가 충분하지 않습니다.",
                    "",
                    "추천 순서:",
                    "1. `Read`로 변경 맥락 읽기",
                    "2. `Basic Quiz`로 전체 이해 점검",
                    "3. `Inline Quiz`로 코드 단위 이해 점검",
                ]
            )
            self._update_review_view(session)
            if self.result_tab == "review":
                self._set_tab_result("review", self.review_content, store=False)
            return

        general_score = summary.get("general_quiz_score", 0)
        inline_score = summary.get("inline_quiz_score", 0)
        weak_points = list(summary.get("weak_points", []))
        weak_files = list(summary.get("weak_files", []))
        recommended_next_step = str(summary.get("recommended_next_step", "")).strip()
        review_updated_at = str(review.get("updated_at", "") or "-")

        lines = [
            "## 학습 세션 리뷰",
            "",
            f"- 읽을거리: {read_status}",
            f"- Basic 퀴즈: {general_status} ({general_question_count}문제)",
            f"- Inline 퀴즈: {inline_status} ({inline_question_count}문제)",
            f"- Read 완료 처리: {'예' if summary.get('read_completed') else '아니오'}",
            f"- Review 갱신: {review_updated_at}",
        ]

        if general_status == "graded":
            lines.append(f"- Basic 점수: {general_score}/100")
        if inline_status == "graded":
            lines.append(f"- Inline 점수: {inline_score}/100")

        if weak_points:
            lines.extend(["", "### 약한 유형"])
            lines.extend(f"- {point}" for point in weak_points)

        if weak_files:
            lines.extend(["", "### 다시 볼 파일"])
            lines.extend(f"- {file_path}" for file_path in weak_files)

        if recommended_next_step:
            lines.extend(["", "### 다음 추천", recommended_next_step])

        if not weak_points and not weak_files and general_status == "graded" and inline_status == "graded":
            lines.extend(["", "강한 약점은 아직 보이지 않습니다. 다음 커밋 범위로 넘어가도 좋습니다."])

        self.review_content = "\n".join(lines)
        self._update_review_view(session)
        if self.result_tab == "review":
            self._set_tab_result("review", self.review_content, store=False)

    def _download_result(self) -> None:
        extension = "md" if self.result_view_mode == "markdown" else "txt"
        quiz_output_dir = get_quiz_output_dir(
            repo_source=self._current_repo_source(),
            local_repo_root=self._current_local_repo_root(),
        )
        quiz_output_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            quiz_output_dir / f"quiz-output-{time.strftime('%Y%m%d-%H%M%S')}.{extension}"
        )
        file_content = result_content_for_save(self._current_result_content_for_download(), extension)
        filename.write_text(file_content, encoding="utf-8")
        self._set_status(f"결과를 저장했습니다: {filename.name}")
        self.notify(
            f"{filename.name} 파일로 저장했습니다.",
            title="Download Complete",
            timeout=2.0,
        )

    def _saved_result_files(self) -> list[Path]:
        return list_saved_result_files(
            repo_source=self._current_repo_source(),
            local_repo_root=self._current_local_repo_root(),
        )

    def _load_result_from_file(self, filename: Path) -> None:
        content = filename.read_text(encoding="utf-8")
        if self.result_tab in {"read", "quiz", "inline", "review"}:
            self._set_tab_result(self.result_tab, content)
        else:
            self.result_content = content
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
        return

    def _update_repo_context(self) -> None:
        repo_source = self._current_repo_source()
        repo_location = self.query_one("#repo-location", Input)
        repo_open = self.query_one("#repo-open", Button)
        if repo_source == "local":
            local_repo_path = str(Path.cwd())
            try:
                local_repo = get_repo(repo_source="local", refresh_remote=False)
                local_repo_path = local_repo.working_tree_dir or local_repo_path
                self.local_repo_root = Path(local_repo_path).resolve()
            except (InvalidGitRepositoryError, NoSuchPathError):
                self.local_repo_root = None
            repo_location.value = local_repo_path
            repo_location.tooltip = local_repo_path
            repo_open.label = "Open"
        else:
            local_repo_path = str(Path.cwd())
            try:
                local_repo = get_repo(repo_source="local", refresh_remote=False)
                local_repo_path = local_repo.working_tree_dir or local_repo_path
            except (InvalidGitRepositoryError, NoSuchPathError):
                pass
            if repo_location.value == local_repo_path:
                repo_location.value = self.github_repo_url
            repo_location.tooltip = None
            repo_open.label = "Open"
        self._save_app_state()

    def _quiz_progress_markdown(self, animated_text: str) -> str:
        return "\n".join(
            [
                f"## {animated_text}",
                "",
                "잠시만 기다려 주세요.",
            ]
        )

    def _read_progress_markdown(self, animated_text: str) -> str:
        return "\n".join(
            [
                f"## {animated_text}",
                "",
                "읽을거리를 준비하고 있습니다.",
            ]
        )

    def _current_generation_status_text(self) -> str | None:
        if self._active_generation_status_source == "quiz" and self._current_quiz_generation_in_progress():
            label = self._current_quiz_progress_label()
            return f"퀴즈 생성 중 [{label}]" if label else "퀴즈 생성 중"
        if self._active_generation_status_source == "read" and self._current_read_generation_in_progress():
            return self._read_status_base
        if self._current_quiz_generation_in_progress():
            label = self._current_quiz_progress_label()
            return f"퀴즈 생성 중 [{label}]" if label else "퀴즈 생성 중"
        if self._current_read_generation_in_progress():
            return self._read_status_base
        return None

    def _start_status_animation(self) -> None:
        self._quiz_generation_in_progress = bool(self._pending_quiz_targets)
        self._quiz_animation_frame = 0
        self._badge_animation_frame = 0
        self._quiz_status_base = "퀴즈 생성 중"
        self._quiz_progress_label = ""
        self._quiz_generation_error_message = ""
        self._active_generation_status_source = "quiz"
        self._animate_status()
        self._refresh_result_command_row()
        self._refresh_quiz_workspace()

    def _stop_quiz_status_animation(self) -> None:
        self._quiz_generation_in_progress = bool(self._pending_quiz_targets)
        self._quiz_progress_label = ""
        self._quiz_status_base = "퀴즈 생성 중"
        if self._active_generation_status_source == "quiz":
            self._active_generation_status_source = "read" if self._read_generation_in_progress else None
        status_text = self._current_generation_status_text()
        if status_text is not None:
            self._set_status(status_text)
        self._refresh_result_command_row()
        self._refresh_quiz_workspace()

    def _set_quiz_progress_node(
        self,
        label: str,
        *,
        target: dict[str, object] | None = None,
    ) -> None:
        session_id = self._session_id_from_target(target) if target is not None else ""
        if session_id:
            self._quiz_progress_labels[session_id] = label
        if self._target_matches_current_selection(target):
            self._quiz_progress_label = label
            self._quiz_status_base = f"퀴즈 생성 중 [{label}]"
        if self._quiz_generation_in_progress:
            self._animate_status()
        self._refresh_quiz_workspace()

    def _start_read_status_animation(self) -> None:
        self._read_generation_in_progress = bool(self._pending_read_targets)
        self._read_animation_frame = 0
        self._badge_animation_frame = 0
        self._read_status_base = "읽을거리 생성 중"
        self._read_progress_label = ""
        self._active_generation_status_source = "read"
        self._animate_status()
        self._refresh_result_command_row()
        self._refresh_quiz_workspace()

    def _set_read_progress_node(self, label: str) -> None:
        self._read_progress_label = label
        self._read_status_base = f"읽을거리 생성 중 [{label}]"
        if self._read_generation_in_progress:
            self._animate_status()

    def _stop_read_status_animation(self) -> None:
        self._read_generation_in_progress = bool(self._pending_read_targets)
        self._read_progress_label = ""
        self._read_status_base = "읽을거리 생성 중"
        if self._active_generation_status_source == "read":
            self._active_generation_status_source = "quiz" if self._quiz_generation_in_progress else None
        status_text = self._current_generation_status_text()
        if status_text is not None:
            self._set_status(status_text)
        self._refresh_result_command_row()

    def _animate_status(self) -> None:
        if (
            not self._quiz_generation_in_progress
            and not self._general_quiz_grading_in_progress
            and not self._read_generation_in_progress
            and not self._inline_generation_in_progress()
            and not self._inline_grading_in_progress()
            and not self._commit_list_loading_enabled
        ):
            return
        self._refresh_result_tab_labels()
        quiz_text = None
        read_text = None
        if self._quiz_generation_in_progress:
            quiz_dots = "+" * ((self._quiz_animation_frame % 3) + 1)
            quiz_text = f"{self._quiz_status_base}{quiz_dots}"
            self._quiz_animation_frame += 1
            self._refresh_quiz_workspace()
        if self._read_generation_in_progress:
            read_dots = "+" * ((self._read_animation_frame % 3) + 1)
            read_text = f"{self._read_status_base}{read_dots}"
            self._read_animation_frame += 1
            if self._current_read_generation_in_progress():
                self._set_tab_result("read", self._read_progress_markdown(read_text))
        if (
            self._quiz_generation_in_progress
            or self._read_generation_in_progress
            or self._general_quiz_grading_in_progress
            or self._inline_generation_in_progress()
            or self._inline_grading_in_progress()
        ):
            self._badge_animation_frame += 1
        else:
            self._badge_animation_frame = 0
        status_text = None
        if self.result_tab == "quiz" and quiz_text is not None:
            status_text = quiz_text
        elif self.result_tab == "read" and read_text is not None:
            status_text = read_text
        elif self._active_generation_status_source == "quiz" and quiz_text is not None:
            status_text = quiz_text
        elif self._active_generation_status_source == "read" and read_text is not None:
            status_text = read_text
        elif quiz_text is not None:
            status_text = quiz_text
        elif read_text is not None:
            status_text = read_text
        if status_text is not None:
            self._set_status(status_text)
        if self._commit_list_loading_enabled:
            dots = "." * ((max(self._quiz_animation_frame, self._read_animation_frame, 1) - 1) % 3 + 1)
            self._show_commit_list_loading(f"커밋 불러오는 중{dots}")

    def _update_commit_panel_help(self) -> None:
        self.query_one("#commit-panel-help", Static).update(
            self._commit_panel_help_text()
        )
        self.query_one("#commit-panel-loaded", Static).update(
            self._commit_panel_loaded_text()
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
        self._restore_learning_content_for_current_selection()
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
        chain: list[Widget] = []
        widgets: list[Widget | None] = [
            self._focusable_widget("#top-code-open", Button),
            self._focusable_widget("#repo-source", RadioSet),
            self._focusable_widget("#commit-list", ListView),
            self._active_result_tab_button(),
            self._focusable_widget("#result-tab-setup", Button),
            self._first_visible_command_button(),
        ]
        widgets.append(self._focusable_widget("#api-key-open", Button))

        for widget in widgets:
            if widget is not None and widget not in chain:
                chain.append(widget)
        return chain

    def _focusable_widget(
        self,
        selector: str,
        widget_type: type[Widget],
    ) -> Widget | None:
        try:
            widget = self.query_one(selector, widget_type)
        except Exception:
            return None
        if not widget.display:
            return None
        if isinstance(widget, Button) and widget.disabled:
            return None
        return widget

    def _active_result_tab_button(self) -> Button | None:
        return self._focusable_widget(
            f"#result-tab-{self.result_tab}",
            Button,
        )

    def _first_visible_command_button(self) -> Button | None:
        for selector in [
            "#result-tab-setup",
            "#result-read",
            "#result-generate",
            "#result-inline-open",
            "#result-sessions-open",
            "#result-read-done-top",
            "#result-quiz-retry-top",
            "#result-quiz-grade-top",
            "#result-inline-retry-top",
            "#result-inline-grade-top",
        ]:
            widget = self._focusable_widget(selector, Button)
            if widget is not None:
                return widget
        return None

    def _visible_button_group(self, selectors: list[str]) -> list[Button]:
        buttons: list[Button] = []
        for selector in selectors:
            button = self._focusable_widget(selector, Button)
            if isinstance(button, Button):
                buttons.append(button)
        return buttons

    def _move_focus_in_button_group(self, selectors: list[str], direction: int) -> bool:
        buttons = self._visible_button_group(selectors)
        if len(buttons) < 2:
            return False
        focused = self.focused
        if focused is None:
            return False
        current_index = None
        for index, button in enumerate(buttons):
            if focused is button or button in focused.ancestors or focused in button.ancestors:
                current_index = index
                break
        if current_index is None:
            return False
        next_index = (current_index + direction) % len(buttons)
        buttons[next_index].focus()
        return True

    def _focused_in_widget(self, selector: str, widget_type: type[Widget]) -> bool:
        focused = self.focused
        if focused is None:
            return False
        widget = self._focusable_widget(selector, widget_type)
        if widget is None:
            return False
        return focused is widget or widget in focused.ancestors or focused in widget.ancestors

    def _setup_radio_group_definitions(self) -> list[tuple[str, list[str]]]:
        return [
            ("#commit-mode", ["#mode-auto", "#mode-latest", "#mode-selected"]),
            (
                "#difficulty",
                ["#difficulty-easy", "#difficulty-medium", "#difficulty-hard"],
            ),
            (
                "#quiz-style",
                [
                    "#style-mixed",
                    "#style-study_session",
                    "#style-multiple_choice",
                    "#style-short_answer",
                    "#style-conceptual",
                ],
            ),
        ]

    def _setup_radio_buttons(self) -> list[RadioButton]:
        buttons: list[RadioButton] = []
        for _, button_selectors in self._setup_radio_group_definitions():
            for button_selector in button_selectors:
                button = self._focusable_widget(button_selector, RadioButton)
                if isinstance(button, RadioButton):
                    buttons.append(button)
        return buttons

    def _move_focus_in_setup_horizontal(self, direction: int) -> bool:
        return False

    def _selected_repo_focus_slot(self) -> int:
        repo_source = self._focusable_widget("#repo-source", RadioSet)
        if isinstance(repo_source, RadioSet):
            pressed = repo_source.pressed_button
            if pressed is not None and pressed.id == "repo-github":
                return 1
        return 0

    def _update_repo_focus_indicator(self, active: bool) -> None:
        try:
            repo_local = self.query_one("#repo-local", RadioButton)
            repo_github = self.query_one("#repo-github", RadioButton)
        except Exception:
            return

        repo_local.remove_class("-nav-focus")
        repo_github.remove_class("-nav-focus")
        if not active:
            return
        if (self._repo_focus_slot or 0) == 0:
            repo_local.add_class("-nav-focus")
        else:
            repo_github.add_class("-nav-focus")

    def _move_focus_in_repo_section(self, direction: int) -> bool:
        focused = self.focused
        if focused is None:
            return False

        repo_source = self._focusable_widget("#repo-source", RadioSet)
        repo_cache_open = self._focusable_widget("#repo-cache-open", Button)
        repo_location = self._focusable_widget("#repo-location", Input)
        repo_open = self._focusable_widget("#repo-open", Button)
        if not isinstance(repo_source, RadioSet):
            return False

        in_repo_source = focused is repo_source or repo_source in focused.ancestors
        if in_repo_source:
            current_slot = (
                self._repo_focus_slot
                if self._repo_focus_slot in {0, 1}
                else self._selected_repo_focus_slot()
            )
            if direction > 0:
                if current_slot == 0:
                    self._repo_focus_slot = 1
                    self._update_repo_focus_indicator(True)
                    repo_source.focus()
                    return True
                if repo_cache_open is not None:
                    self._repo_focus_slot = 1
                    self._update_repo_focus_indicator(False)
                    repo_cache_open.focus()
                    return True
                return False
            if current_slot == 1:
                self._repo_focus_slot = 0
                self._update_repo_focus_indicator(True)
                repo_source.focus()
                return True
            if repo_open is not None:
                self._repo_focus_slot = 0
                self._update_repo_focus_indicator(False)
                repo_open.focus()
                return True
            return False

        if repo_cache_open is not None and (
            focused is repo_cache_open
            or repo_cache_open in focused.ancestors
            or focused in repo_cache_open.ancestors
        ):
            if direction > 0:
                if repo_location is not None:
                    self._update_repo_focus_indicator(False)
                    repo_location.focus()
                    return True
                return False
            self._repo_focus_slot = 1
            self._update_repo_focus_indicator(True)
            repo_source.focus()
            return True

        if repo_location is not None and (
            focused is repo_location
            or repo_location in focused.ancestors
            or focused in repo_location.ancestors
        ):
            if direction > 0:
                if repo_open is not None:
                    self._update_repo_focus_indicator(False)
                    repo_open.focus()
                    return True
                return False
            if repo_cache_open is not None:
                self._update_repo_focus_indicator(False)
                repo_cache_open.focus()
                return True
            return False

        if repo_open is not None and (
            focused is repo_open
            or repo_open in focused.ancestors
            or focused in repo_open.ancestors
        ):
            if direction > 0:
                self._repo_focus_slot = 0
                self._update_repo_focus_indicator(True)
                repo_source.focus()
                return True
            if repo_location is not None:
                self._update_repo_focus_indicator(False)
                repo_location.focus()
                return True
            return True

        return False

    def _focus_index_for_widget(self, widget: Widget | None) -> int:
        if widget is None:
            return 0

        chain = self._focus_chain()
        for index, target in enumerate(chain):
            if widget is target:
                return index
            if self._widget_in_section(widget, target):
                return index
            if target in widget.ancestors:
                return index
            if widget in target.ancestors:
                return index
        return 0

    def _widget_in_section(self, widget: Widget, target: Widget) -> bool:
        try:
            result_tab_group = self.query_one("#result-tab-group", Horizontal)
        except Exception:
            result_tab_group = None
        try:
            result_command_row = self.query_one("#result-command-row", Horizontal)
        except Exception:
            result_command_row = None

        if result_tab_group is not None:
            if (
                target in result_tab_group.children
                and (widget is result_tab_group or result_tab_group in widget.ancestors)
            ):
                return True

        if result_command_row is not None:
            if (
                target in result_command_row.query("Button")
                and (widget is result_command_row or result_command_row in widget.ancestors)
            ):
                return True

        return False

    def _focused_in_result_command_row(self) -> bool:
        focused = self.focused
        if focused is None:
            return False
        try:
            result_command_row = self.query_one("#result-command-row", Horizontal)
        except Exception:
            return False
        return focused is result_command_row or result_command_row in focused.ancestors

    def _move_focus_in_setup_vertical(self, direction: int) -> bool:
        return False

    def action_focus_next_section(self) -> None:
        chain = self._focus_chain()
        if not chain:
            return
        current_index = self._focus_index_for_widget(self.focused)
        next_index = (current_index + 1) % len(chain)
        if chain[next_index] is self._focusable_widget("#repo-source", RadioSet):
            self._repo_focus_slot = self._selected_repo_focus_slot()
            self._update_repo_focus_indicator(True)
        else:
            self._update_repo_focus_indicator(False)
        chain[next_index].focus()

    def action_focus_previous_section(self) -> None:
        chain = self._focus_chain()
        if not chain:
            return
        current_index = self._focus_index_for_widget(self.focused)
        next_index = (current_index - 1) % len(chain)
        if chain[next_index] is self._focusable_widget("#repo-source", RadioSet):
            self._repo_focus_slot = self._selected_repo_focus_slot()
            self._update_repo_focus_indicator(True)
        else:
            self._update_repo_focus_indicator(False)
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
        if event.key in {"left", "right"}:
            direction = -1 if event.key == "left" else 1
            if self._move_focus_in_repo_section(direction):
                event.stop()
                return
            if self._move_focus_in_setup_horizontal(direction):
                event.stop()
                return
            if self._move_focus_in_button_group(
                [
                    "#result-tab-home",
                    "#result-tab-read",
                    "#result-tab-quiz",
                    "#result-tab-inline",
                    "#result-tab-review",
                ],
                direction,
            ):
                event.stop()
                return
            if self._move_focus_in_button_group(
                [
                    "#result-tab-setup",
                    "#result-session-generate-all",
                    "#result-read",
                    "#result-generate",
                    "#result-inline-open",
                    "#result-sessions-open",
                    "#result-read-done-top",
                    "#result-quiz-retry-top",
                    "#result-quiz-grade-top",
                    "#result-inline-retry-top",
                    "#result-inline-grade-top",
                ],
                direction,
            ):
                event.stop()
                return
        if event.key in {"down", "up"}:
            direction = 1 if event.key == "down" else -1
            if self._move_focus_in_setup_vertical(direction):
                event.stop()
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
            if focused is self.query_one("#top-code-open", Button):
                event.stop()
                self.action_open_code_browser()
                return
            for button in self._setup_radio_buttons():
                if focused is button:
                    event.stop()
                    button.value = True
                    return
            if focused is self.query_one("#repo-source", RadioSet):
                event.stop()
                repo_local = self.query_one("#repo-local", RadioButton)
                repo_github = self.query_one("#repo-github", RadioButton)
                if (self._repo_focus_slot or 0) == 0:
                    repo_github.value = False
                    repo_local.value = True
                else:
                    repo_local.value = False
                    repo_github.value = True
                return
            if focused is self.query_one("#repo-open", Button):
                event.stop()
                self._load_selected_repo("저장소를 불러왔습니다.")
                return
            if focused is self.query_one("#commit-preview-open-code", Button):
                event.stop()
                self.action_open_code_browser()
                return
            if focused is self.query_one("#result-session-generate-all", Button):
                event.stop()
                self.action_generate_session_all()
                return
            if focused is self.query_one("#result-generate", Button):
                event.stop()
                self.action_generate_quiz()
                return
            if focused is self.query_one("#result-read", Button):
                event.stop()
                self.action_generate_read()
                return
            if focused is self.query_one("#result-read-done-top", Button):
                event.stop()
                self._mark_read_done_for_selection()
                return
            if focused is self.query_one("#result-inline-open", Button):
                event.stop()
                self._show_inline_quiz(widget_id="result-inline-widget", force_regenerate=True)
                self._rebuild_inline_content_for_current_selection()
                return
            if focused is self.query_one("#result-quiz-retry-top", Button):
                event.stop()
                self._retry_general_quiz_for_selection()
                return
            if focused is self.query_one("#result-quiz-grade-top", Button):
                event.stop()
                self._grade_general_quiz_for_selection()
                return
            if focused is self.query_one("#result-inline-retry-top", Button):
                event.stop()
                self._retry_inline_quiz_for_selection()
                return
            if focused is self.query_one("#result-inline-grade-top", Button):
                event.stop()
                self._grade_inline_quiz_for_selection()
                return
            if focused is self.query_one("#result-tab-setup", Button):
                event.stop()
                self.action_open_setup()
                return
            if focused is self.query_one("#result-tab-home", Button):
                event.stop()
                self._set_result_tab("home")
                return
            if focused is self.query_one("#result-tab-read", Button):
                event.stop()
                self._set_result_tab("read")
                return
            if focused is self.query_one("#result-tab-quiz", Button):
                event.stop()
                self._set_result_tab("quiz")
                return
            if focused is self.query_one("#result-tab-inline", Button):
                event.stop()
                self._rebuild_inline_content_for_current_selection()
                self._set_result_tab("inline")
                return
            if focused is self.query_one("#result-tab-review", Button):
                event.stop()
                self._set_result_tab("review")
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
        if self.selected_commit_index != event.list_view.index:
            self._persist_open_inline_quiz_state()
        self.selected_commit_index = event.list_view.index
        self._show_commit_summary(self.selected_commit_index)
        self._update_commit_detail(self.selected_commit_index)
        self._restore_learning_content_for_current_selection()
        self._reload_inline_quiz_if_open()
        self._refresh_top_bar_context()
        self._save_app_state()

    @on(ListView.Selected, "#commit-list")
    def handle_commit_selected(self, event: ListView.Selected) -> None:
        if event.list_view.index is None:
            return
        if event.list_view.index == len(self.commits):
            event.stop()
            self.action_load_more_commits()
            return
        if event.list_view.index == len(self.commits) + 1:
            event.stop()
            self.action_load_all_commits()

    @on(Button.Pressed, "#result-read")
    def handle_generate_read(self) -> None:
        self.action_generate_read()

    @on(Button.Pressed, "#result-read-done-top")
    def handle_read_done_top(self) -> None:
        self._mark_read_done_for_selection()

    @on(Button.Pressed, "#result-session-generate-all")
    def handle_generate_session_all(self) -> None:
        self.action_generate_session_all()

    @on(Button.Pressed, "#result-sessions-open")
    def handle_result_sessions_open(self) -> None:
        self.action_open_sessions()

    @on(Button.Pressed, "#result-generate")
    def handle_generate(self) -> None:
        self.action_generate_quiz()

    @on(Button.Pressed, "#result-inline-open")
    def handle_result_inline_open(self) -> None:
        self._show_inline_quiz(widget_id="result-inline-widget", force_regenerate=True)
        self._rebuild_inline_content_for_current_selection()

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

    @on(Button.Pressed, "#result-tab-setup")
    def handle_result_tab_setup(self) -> None:
        self.action_open_setup()

    @on(Button.Pressed, "#result-tab-home")
    def handle_result_tab_home(self) -> None:
        self._set_result_tab("home")

    @on(Button.Pressed, "#result-tab-read")
    def handle_result_tab_read(self) -> None:
        self._set_result_tab("read")

    @on(Button.Pressed, "#result-tab-quiz")
    def handle_result_tab_quiz(self) -> None:
        self._set_result_tab("quiz")

    @on(Button.Pressed, "#result-tab-inline")
    def handle_result_tab_inline(self) -> None:
        self._restore_inline_widget_for_selection(widget_id="result-inline-widget")
        self._rebuild_inline_content_for_current_selection()
        self._set_result_tab("inline")

    @on(Button.Pressed, "#result-tab-review")
    def handle_result_tab_review(self) -> None:
        self._rebuild_review_content_for_current_selection()
        self._set_result_tab("review")

    @on(Button.Pressed, "#result-quiz-prev")
    def handle_result_quiz_prev(self) -> None:
        self._navigate_general_quiz_question(-1)

    @on(Button.Pressed, "#result-quiz-next")
    def handle_result_quiz_next(self) -> None:
        self._navigate_general_quiz_question(1)

    @on(Button.Pressed)
    def handle_result_quiz_nav_button(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("result-quiz-nav-"):
            return
        try:
            index = int(button_id.rsplit("-", 1)[-1])
        except ValueError:
            return
        self._persist_general_quiz_workspace_answer()
        general_quiz = self._current_general_quiz_session()
        if not general_quiz:
            return
        questions = list(general_quiz.get("questions", []))
        if not questions or not (0 <= index < len(questions)):
            return
        session = self._load_or_create_current_learning_session()
        local_repo_root = self._current_local_repo_root()
        if session is None:
            return
        session["general_quiz"]["current_index"] = index
        session["general_quiz"]["updated_at"] = now_timestamp()
        save_learning_session(
            session,
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        self._refresh_quiz_workspace()
        event.stop()

    @on(Button.Pressed, "#result-quiz-retry")
    def handle_result_quiz_retry(self) -> None:
        self._retry_general_quiz_for_selection()

    @on(Button.Pressed, "#result-quiz-retry-top")
    def handle_result_quiz_retry_top(self) -> None:
        self._retry_general_quiz_for_selection()

    @on(Button.Pressed, "#result-quiz-grade")
    def handle_result_quiz_grade(self) -> None:
        self._grade_general_quiz_for_selection()

    @on(Button.Pressed, "#result-quiz-grade-top")
    def handle_result_quiz_grade_top(self) -> None:
        self._grade_general_quiz_for_selection()

    @on(Button.Pressed, "#result-inline-retry-top")
    def handle_result_inline_retry_top(self) -> None:
        self._retry_inline_quiz_for_selection()

    @on(Button.Pressed, "#result-inline-grade-top")
    def handle_result_inline_grade_top(self) -> None:
        self._grade_inline_quiz_for_selection()

    @on(TextArea.Changed, "#result-quiz-answer")
    def handle_result_quiz_answer_changed(self) -> None:
        if self._restoring_general_quiz_answer:
            return
        self._persist_general_quiz_workspace_answer()

    @on(AnswerTextArea.Submit)
    def handle_result_quiz_answer_submit(self, message: AnswerTextArea.Submit) -> None:
        if message.answer_input is not self.query_one("#result-quiz-answer", AnswerTextArea):
            return
        self._persist_general_quiz_workspace_answer()
        self._refresh_quiz_workspace()

    @on(AnswerTextArea.NavigatePrevious)
    def handle_result_quiz_answer_previous(
        self, message: AnswerTextArea.NavigatePrevious
    ) -> None:
        if message.answer_input is not self.query_one("#result-quiz-answer", AnswerTextArea):
            return
        self._persist_general_quiz_workspace_answer()
        self._navigate_general_quiz_question(-1)

    @on(AnswerTextArea.NavigateNext)
    def handle_result_quiz_answer_next(
        self, message: AnswerTextArea.NavigateNext
    ) -> None:
        if message.answer_input is not self.query_one("#result-quiz-answer", AnswerTextArea):
            return
        self._persist_general_quiz_workspace_answer()
        general_quiz = self._current_general_quiz_session()
        questions = list(general_quiz.get("questions", [])) if general_quiz else []
        current_index = int(general_quiz.get("current_index", 0)) if general_quiz else 0
        if questions and current_index >= len(questions) - 1:
            self._grade_general_quiz_for_selection()
        else:
            self._navigate_general_quiz_question(1)


    @on(Button.Pressed, "#top-code-open")
    def handle_top_code_open(self) -> None:
        self.action_open_code_browser()

    @on(Button.Pressed, "#repo-open")
    def handle_repo_open(self) -> None:
        self._load_selected_repo("저장소를 불러왔습니다.")

    @on(Button.Pressed, "#repo-cache-open")
    def handle_repo_cache_open(self) -> None:
        cleanup_expired_remote_repo_caches()
        self.push_screen(
            RemoteRepoCacheScreen(
                list_remote_repo_caches(),
                get_remote_repo_cache_dir(),
                REMOTE_CACHE_RETENTION_DAYS,
                current_repo_source=self._current_repo_source(),
                current_repo_url=self._current_github_repo_url() or self.github_repo_url,
            ),
            self._handle_repo_cache_screen_closed,
        )

    def _handle_repo_cache_screen_closed(
        self, result: dict[str, str] | None
    ) -> None:
        if not result:
            return
        action = result.get("action", "")
        if action == "remove":
            selected_slug = result.get("slug", "")
            if not selected_slug:
                return
            if remove_remote_repo_cache(selected_slug):
                self._set_status("선택한 원격 저장소 캐시를 제거했습니다.")
            else:
                self._set_status("제거할 원격 저장소 캐시를 찾지 못했습니다.")
            return

        if action == "remove_all":
            removed_count = 0
            for entry in list_remote_repo_caches():
                slug = str(entry.get("slug", "")).strip()
                if slug and remove_remote_repo_cache(slug):
                    removed_count += 1
            if removed_count:
                self._set_status(f"원격 저장소 캐시 {removed_count}개를 제거했습니다.")
            else:
                self._set_status("제거할 원격 저장소 캐시가 없습니다.")
            return

        if action == "select":
            repo_url = result.get("repo_url", "").strip()
            if not repo_url:
                self._set_status("선택할 원격 저장소 URL을 찾지 못했습니다.")
                return
            self.repo_source = "github"
            self.github_repo_url = repo_url
            if self.is_mounted:
                self._suppress_repo_source_change = True
                self.query_one("#repo-location", Input).value = repo_url
                self.query_one("#repo-github", RadioButton).value = True
                self._suppress_repo_source_change = False
                self._update_repo_context()
            self._save_app_state()
            self._load_selected_repo("원격 저장소 캐시를 선택했습니다.")

    def action_open_sessions(self) -> None:
        local_repo_root = self._current_local_repo_root()
        session_root = get_session_repo_dir(
            repo_source=self._current_repo_source(),
            github_repo_url=self.github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        self.push_screen(
            SessionListScreen(
                list_learning_sessions(
                    repo_source=self._current_repo_source(),
                    github_repo_url=self.github_repo_url,
                    local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
                ),
                session_root,
                current_session_id=self._current_learning_session_id(),
            ),
            self._handle_session_list_screen_closed,
        )

    def action_open_setup(self) -> None:
        self.push_screen(
            SetupScreen(
                saved_commit_mode=self.saved_commit_mode,
                saved_difficulty=self.saved_difficulty,
                saved_quiz_style=self.saved_quiz_style,
                saved_read_request=self.saved_read_request,
                saved_basic_request=self.saved_basic_request,
                saved_inline_request=self.saved_inline_request,
                saved_grading_request=self.saved_grading_request,
                request_placeholder=REQUEST_PLACEHOLDER,
                request_example_text=REQUEST_EXAMPLE_TEXT,
            ),
            self._handle_setup_screen_closed,
        )

    def _handle_setup_screen_closed(
        self, result: dict[str, str] | None
    ) -> None:
        self._save_app_state()
        if result and result.get("action") == "generate_all":
            self.action_generate_session_all()

    def _handle_session_list_screen_closed(
        self, result: dict[str, str] | None
    ) -> None:
        if not result:
            return
        local_repo_root = self._current_local_repo_root()
        repo_source = self._current_repo_source()
        github_repo_url = self.github_repo_url
        if result.get("action") == "remove":
            session_id = result.get("session_id", "")
            if session_id and remove_learning_session(
                session_id,
                repo_source=repo_source,
                github_repo_url=github_repo_url,
                local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
            ):
                self._set_status("선택한 세션을 제거했습니다.")
                self._refresh_session_progress()
                self.action_open_sessions()
            else:
                self._set_status("제거할 세션을 찾지 못했습니다.")
            return

        if result.get("action") == "remove_all":
            removed_count = 0
            for entry in list_learning_sessions(
                repo_source=repo_source,
                github_repo_url=github_repo_url,
                local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
            ):
                session_id = str(entry.get("session_id", "")).strip()
                if session_id and remove_learning_session(
                    session_id,
                    repo_source=repo_source,
                    github_repo_url=github_repo_url,
                    local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
                ):
                    removed_count += 1
            self._set_status(
                f"세션 {removed_count}개를 제거했습니다." if removed_count else "제거할 세션이 없습니다."
            )
            self._refresh_session_progress()
            self.action_open_sessions()
            return

        if result.get("action") != "select":
            return

        session_id = result.get("session_id", "").strip()
        if not session_id:
            return
        session = load_learning_session_file(
            session_id,
            repo_source=repo_source,
            github_repo_url=github_repo_url,
            local_repo_root=str(local_repo_root) if local_repo_root is not None else None,
        )
        if not isinstance(session, dict):
            self._set_status("선택한 세션을 불러오지 못했습니다.")
            return

        selection = session.get("selection", {}) if isinstance(session.get("selection"), dict) else {}
        preferences = session.get("preferences", {}) if isinstance(session.get("preferences"), dict) else {}
        self.saved_commit_mode = str(selection.get("commit_mode", "selected"))
        self.saved_selected_range_start_sha = str(selection.get("start_sha", ""))
        self.saved_selected_range_end_sha = str(selection.get("end_sha", ""))
        self.saved_highlighted_commit_sha = str(selection.get("highlighted_commit_sha", ""))
        self.saved_difficulty = str(preferences.get("difficulty", self.saved_difficulty))
        self.saved_quiz_style = str(preferences.get("quiz_style", self.saved_quiz_style))
        legacy_request = str(preferences.get("request_text", ""))
        self.saved_read_request = str(
            preferences.get("read_request_text", legacy_request or self.saved_read_request)
        )
        self.saved_basic_request = str(
            preferences.get("basic_request_text", legacy_request or self.saved_basic_request)
        )
        self.saved_inline_request = str(
            preferences.get("inline_request_text", legacy_request or self.saved_inline_request)
        )
        self.saved_grading_request = str(
            preferences.get(
                "grading_request_text",
                legacy_request or self.saved_grading_request,
            )
        )

        self._restore_saved_commit_selection()
        self._refresh_commit_list_labels()
        self._update_commit_panel_help()
        if self.commits:
            restored_index = min(self.selected_commit_index, len(self.commits) - 1)
            self.selected_commit_index = max(0, restored_index)
            self.query_one("#commit-list", ListView).index = self.selected_commit_index
            self._show_commit_summary(self.selected_commit_index)
            self._update_commit_detail(self.selected_commit_index)
        self._restore_learning_content_for_current_selection()
        self._refresh_top_bar_context()
        self._refresh_session_progress()
        self._save_app_state()
        self._set_status("선택한 세션을 복원했습니다.")

    @on(Button.Pressed, "#api-key-open")
    def handle_api_key_open(self) -> None:
        self.push_screen(
            ApiKeyScreen(
                current_mode=self.api_key_mode,
                current_status=self._api_key_status_text(),
                settings_path=get_settings_path(),
                secrets_path=get_secrets_path(),
            ),
            self._handle_api_key_screen_closed,
        )

    def _handle_api_key_screen_closed(
        self, result: dict[str, str] | None
    ) -> None:
        if not result:
            return
        action = result.get("action")
        mode = result.get("mode", "session")
        self.api_key_mode = mode if mode in {"session", "file"} else "session"

        if action == "clear":
            clear_session_openai_api_key()
            delete_openai_api_key()
            save_settings(model=self.model_name)
            self.settings = load_settings()
            self._refresh_api_key_status()
            self._set_status("저장된 API Key를 제거했습니다.")
            return

        if action != "save":
            return

        api_key = result.get("api_key", "").strip()
        if not api_key:
            self._set_status("API Key를 입력해 주세요.")
            return

        if self.api_key_mode == "file":
            save_openai_api_key(api_key)
            clear_session_openai_api_key()
        else:
            set_session_openai_api_key(api_key)
            delete_openai_api_key()

        save_settings(model=self.model_name)
        self.settings = load_settings()
        self._refresh_api_key_status()
        if self.api_key_mode == "file":
            self._set_status("API Key를 전역 파일에 저장했습니다.")
        else:
            self._set_status("API Key를 이번 실행 동안만 저장했습니다.")

    @on(Button.Pressed, "#commit-preview-open-code")
    def handle_open_code_browser(self) -> None:
        self.action_open_code_browser()

    @on(Button.Pressed, "#commit-preview-open-inline")
    def handle_open_inline_quiz(self) -> None:
        self.action_open_inline_quiz()

    @on(Button.Pressed, "#commit-collapse-toggle")
    def handle_commit_panel_toggle(self) -> None:
        self.commit_panel_collapsed = not self.commit_panel_collapsed
        panel = self.query_one("#commit-panel", Vertical)
        panel.set_class(self.commit_panel_collapsed, "-collapsed")
        self._update_workspace_widths()

    @on(Click, "#commit-collapse-strip")
    def handle_commit_collapse_strip_click(self, event: Click) -> None:
        self.handle_commit_panel_toggle()
        event.stop()

    @on(Click, "#repo-bar-title")
    @on(Click, "#commit-panel-title")
    def handle_collapsed_panel_title_click(self, event: Click) -> None:
        if not self.commit_panel_collapsed:
            return
        self.handle_commit_panel_toggle()
        event.stop()

    @on(Click, "#commit-panel")
    def handle_commit_panel_click(self, event: Click) -> None:
        if not self.commit_panel_collapsed:
            return
        self.handle_commit_panel_toggle()
        event.stop()

    @on(RadioSet.Changed, "#repo-source")
    def handle_repo_source_changed(self) -> None:
        if self._suppress_repo_source_change:
            return
        self.repo_source = self._current_repo_source()
        self._repo_focus_slot = 0 if self.repo_source == "local" else 1
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
        self.saved_commit_mode = self._current_commit_mode()
        self.saved_difficulty = self._current_difficulty()
        self.saved_quiz_style = self._current_quiz_style()
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

    @on(TextArea.Changed, "#request-read-input")
    @on(TextArea.Changed, "#request-basic-input")
    @on(TextArea.Changed, "#request-inline-input")
    @on(TextArea.Changed, "#request-grading-input")
    def handle_request_changed(self, event: TextArea.Changed) -> None:
        input_id = event.text_area.id or ""
        kind = input_id.removeprefix("request-").removesuffix("-input")
        if kind in REQUEST_KINDS:
            self._set_saved_request_value(kind, event.text_area.text)
        self._update_request_input_height()
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
        self._persist_open_inline_quiz_state()
        next_selection = update_selection_for_index(
            CommitSelection(
                start_index=self.selected_range_start_index,
                end_index=self.selected_range_end_index,
            ),
            index,
        )
        self.selected_range_start_index = next_selection.start_index
        self.selected_range_end_index = next_selection.end_index
        if self.selected_range_start_index is not None:
            self._ensure_selected_commit_mode()
        self.selected_commit_index = index
        self._refresh_commit_list_labels()
        self._update_commit_panel_help()
        self._show_commit_summary(index)
        self._update_commit_detail(index)
        self._restore_learning_content_for_current_selection()
        self._reload_code_browser_if_open()
        self._reload_inline_quiz_if_open()
        self._update_top_toggle_buttons()
        self._refresh_top_bar_context()
        self._save_app_state()

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
        self._show_inline_quiz(widget_id="inline-quiz-dock")
        self._rebuild_inline_content_for_current_selection()
        self._set_result_tab("inline")

    def action_open_quiz_section(self) -> None:
        self.action_open_setup()
        self._set_status("Setup 화면을 열었습니다.")

    def action_generate_session_all(self) -> None:
        if not self.commits:
            self._set_status("표시할 커밋이 없습니다.")
            return
        self._set_status("Read, Basic, Inline 생성을 시작합니다.")
        self.action_generate_read()
        self.action_generate_quiz()
        api_key, _ = get_openai_api_key()
        if api_key:
            self._show_inline_quiz(
                widget_id="result-inline-widget",
                prompt_for_api_key=False,
                force_regenerate=True,
            )
            self._rebuild_inline_content_for_current_selection()
        else:
            self.prompt_api_key_settings(action_label="인라인 퀴즈 생성")

    def _show_inline_quiz(
        self,
        *,
        widget_id: str,
        prompt_for_api_key: bool = True,
        force_regenerate: bool = False,
    ) -> None:
        inline_quiz = self.query_one(f"#{widget_id}", InlineQuizWidget)
        api_key, _ = get_openai_api_key()
        if not api_key:
            inline_quiz.show_placeholder(
                "OpenAI API Key를 설정하면 인라인 퀴즈를 생성할 수 있습니다."
            )
            self._update_workspace_widths()
            self._update_top_toggle_buttons()
            if prompt_for_api_key:
                self.prompt_api_key_settings(action_label="인라인 퀴즈 생성")
            return
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
        session_id = self._current_learning_session_id() or target_sha
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
        saved_state = None if force_regenerate else self._inline_saved_state_for_current_selection()
        target = self._capture_session_target()
        if target is not None:
            self._inline_session_targets[session_id] = target
        inline_quiz.show_quiz(
            commit_context=commit_context,
            repo=repo,
            target_commit_sha=target_sha,
            title_suffix=self._selected_commit_title_suffix(),
            saved_state=saved_state,
            cache_key=session_id,
            user_request=self._current_request("inline"),
            grading_request=self._current_request("grading"),
        )
        self._update_workspace_widths()
        self._update_top_toggle_buttons()

    def _restore_inline_widget_for_selection(self, *, widget_id: str) -> None:
        inline_quiz = self.query_one(f"#{widget_id}", InlineQuizWidget)
        if not self.commits:
            inline_quiz.show_placeholder("표시할 커밋이 없습니다.")
            return

        selected_indices = sorted(self._selected_commit_indices())
        newest_index = min(selected_indices) if selected_indices else self.selected_commit_index
        if newest_index >= len(self.commits):
            inline_quiz.show_placeholder(
                "커밋을 선택한 뒤 `Inline Quiz` 버튼을 눌러 인라인 퀴즈를 생성해 주세요."
            )
            return

        target_sha = self.commits[newest_index]["sha"]
        session_id = self._current_learning_session_id() or target_sha
        current_state = getattr(inline_quiz, "_state", "idle")
        if (
            inline_quiz.target_commit_sha == target_sha
            and inline_quiz.cache_key == session_id
            and current_state in {"loading", "grading", "answering", "results"}
            and (inline_quiz.questions or current_state in {"loading", "grading"})
        ):
            return

        repo = get_repo(**self._repo_args(refresh_remote=False))
        selected_commit = repo.commit(target_sha)
        commit_context = build_commit_context(selected_commit, "selected_commit", repo)
        saved_state = self._inline_saved_state_for_current_selection()

        if saved_state is not None and saved_state["questions"]:
            target = self._capture_session_target()
            if target is not None:
                self._inline_session_targets[session_id] = target
            inline_quiz.show_quiz(
                commit_context=commit_context,
                repo=repo,
                target_commit_sha=target_sha,
                title_suffix=self._selected_commit_title_suffix(),
                saved_state=saved_state,
                cache_key=session_id,
                user_request=self._current_request("inline"),
                grading_request=self._current_request("grading"),
            )
            return

        if not commit_context.get("diff_text"):
            inline_quiz.show_placeholder(
                "텍스트 diff가 있는 커밋을 선택하면 인라인 퀴즈를 생성할 수 있습니다."
            )
            return

        inline_quiz.show_placeholder(
            "`Inline Quiz` 버튼을 누르면 여기서 인라인 퀴즈를 생성할 수 있습니다."
        )

    def save_inline_quiz_state(
        self,
        cache_key: str,
        state: InlineQuizSavedState,
        grading_summary: GradingSummary | None = None,
    ) -> None:
        target = self._inline_session_targets.get(cache_key)
        if target is None:
            return
        self._save_inline_quiz_state_for_selection(
            state,
            grading_summary,
            target=target,
        )
        self._update_top_toggle_buttons()

    def notify_inline_quiz_started(self, cache_key: str) -> None:
        target = self._inline_session_targets.get(cache_key)
        if target is None:
            return
        self._pending_inline_targets[cache_key] = target
        self._refresh_result_tab_labels()
        self._refresh_result_command_row()

    def notify_inline_quiz_finished(self, cache_key: str) -> None:
        self._pending_inline_targets.pop(cache_key, None)
        self._refresh_result_tab_labels()
        self._refresh_result_command_row()

    def notify_inline_grade_started(self, cache_key: str) -> None:
        target = self._inline_session_targets.get(cache_key)
        if target is None:
            return
        self._pending_inline_grade_targets[cache_key] = target
        self._refresh_result_tab_labels()
        self._refresh_result_command_row()

    def notify_inline_grade_finished(self, cache_key: str) -> None:
        self._pending_inline_grade_targets.pop(cache_key, None)
        self._refresh_result_tab_labels()
        self._refresh_result_command_row()

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
            code_btn = self.query_one("#top-code-open", Button)
        except Exception:
            return
        code_browser = self.query_one("#code-browser-dock", CodeBrowserDock)
        code_btn.label = "Code ▲" if code_browser.display else "Code ▼"
        code_btn.set_class(code_browser.display, "-active")

    def _after_inline_quiz_closed(self) -> None:
        self._rebuild_inline_content_for_current_selection()
        self._update_workspace_widths()
        self._update_top_toggle_buttons()

    @on(Button.Pressed, "#code-browser-close")
    def handle_code_browser_close(self) -> None:
        self.query_one("#code-browser-dock", CodeBrowserDock).hide_panel()
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
            self.post_message(RepoCommitsFailed(str(exc), repo_key))
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
        if not self._ensure_api_key(action_label="퀴즈 생성"):
            return

        selection_payload = self._generation_commit_selection_payload()
        payload = {
            "messages": [{"role": "user", "content": self._current_request("basic")}],
            "repo_source": self._current_repo_source(),
            "commit_mode": str(selection_payload["commit_mode"]),
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

        if payload["commit_mode"] == "selected":
            payload["requested_commit_shas"] = list(
                selection_payload.get("requested_commit_shas", [])
            )
            requested_commit_sha = selection_payload.get("requested_commit_sha")
            if isinstance(requested_commit_sha, str) and requested_commit_sha:
                payload["requested_commit_sha"] = requested_commit_sha

        self.query_one("#result-generate", Button).disabled = True
        self.query_one("#result-quiz-retry", Button).disabled = True
        self.query_one("#result-quiz-grade", Button).disabled = True
        self.query_one("#result-quiz-retry-top", Button).disabled = True
        self.query_one("#result-quiz-grade-top", Button).disabled = True
        target = self._capture_session_target()
        if target is not None:
            session_id = self._session_id_from_target(target)
            if session_id:
                self._pending_quiz_targets[session_id] = target
                self._quiz_progress_labels[session_id] = ""
                self._quiz_error_messages.pop(session_id, None)
                self._pending_quiz_target = target
        self._start_status_animation()
        self.generate_quiz(payload, target)

    def action_generate_read(self) -> None:
        if not self.commits:
            self._set_result("표시할 커밋이 없습니다.")
            return
        if not self._ensure_api_key(action_label="읽을거리 생성"):
            return

        selection_payload = self._generation_commit_selection_payload()
        payload = {
            "messages": [{"role": "user", "content": self._current_request("read")}],
            "repo_source": self._current_repo_source(),
            "commit_mode": str(selection_payload["commit_mode"]),
            "difficulty": self._current_difficulty(),
        }
        github_repo_url = self._current_github_repo_url()
        if payload["repo_source"] == "github":
            if not github_repo_url:
                self._set_status("GitHub 저장소 URL을 입력해 주세요.")
                self._set_result("GitHub Repo 모드에서는 저장소 URL이 필요합니다.")
                return
            payload["github_repo_url"] = github_repo_url

        if payload["commit_mode"] == "selected":
            payload["requested_commit_shas"] = list(
                selection_payload.get("requested_commit_shas", [])
            )
            requested_commit_sha = selection_payload.get("requested_commit_sha")
            if isinstance(requested_commit_sha, str) and requested_commit_sha:
                payload["requested_commit_sha"] = requested_commit_sha

        self.query_one("#result-read", Button).disabled = True
        target = self._capture_session_target()
        if target is not None:
            session_id = self._session_id_from_target(target)
            if session_id:
                self._pending_read_targets[session_id] = target
                self._pending_read_target = target
        self._start_read_status_animation()
        self.generate_read(payload, target)

    @work(thread=True)
    def generate_quiz(
        self,
        payload: dict,
        target: dict[str, object] | None = None,
    ) -> None:
        try:
            result = None
            for event in stream_quiz_progress(payload):
                if event.get("type") == "node":
                    self.post_message(
                        QuizNodeStarted(
                            str(event.get("node", "")),
                            str(event.get("label", "")),
                            target,
                        )
                    )
                elif event.get("type") == "result":
                    result = event.get("result")
            if result is None:
                result = run_quiz(payload)
        except Exception as exc:
            error_message = str(exc)
            if "OPENAI_API_KEY" in error_message or "API key" in error_message:
                error_message = (
                    "텍스트 diff 기반 퀴즈 생성에는 OpenAI API Key가 필요합니다. API Key 버튼에서 설정해 주세요."
                )
            self.post_message(QuizFailed(error_message, target))
            return

        final_message = result["messages"][-1]
        self.post_message(
            QuizGenerated(
                str(final_message.content),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                list(result.get("quiz_questions", [])),
                target,
            )
        )

    @work(thread=True)
    def generate_read(
        self,
        payload: dict,
        target: dict[str, object] | None = None,
    ) -> None:
        try:
            result = None
            for event in stream_read_progress(payload):
                if event.get("type") == "node":
                    self.post_message(
                        ReadNodeStarted(
                            str(event.get("node", "")),
                            str(event.get("label", "")),
                        )
                    )
                elif event.get("type") == "result":
                    result = event.get("result")
            if result is None:
                result = run_read(payload)
        except Exception as exc:
            error_message = str(exc)
            if "OPENAI_API_KEY" in error_message or "API key" in error_message:
                error_message = (
                    "읽을거리 생성에는 OpenAI API Key가 필요합니다. API Key 버튼에서 설정해 주세요."
                )
            self.post_message(ReadFailed(error_message, target))
            return

        final_message = result["messages"][-1]
        self.post_message(
            ReadGenerated(
                str(final_message.content),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                target,
            )
        )

    @work(thread=True)
    def grade_general_quiz(
        self,
        questions: list[dict],
        answers: dict[str, str],
        user_request: str = "",
        target: dict[str, object] | None = None,
    ) -> None:
        try:
            grades = None
            grading_summary: dict = {}
            for event in stream_general_grade_progress(
                questions,
                answers,
                user_request=user_request,
            ):
                if event.get("type") == "node":
                    self.post_message(
                        GeneralGradeNodeStarted(
                            str(event.get("node", "")),
                            str(event.get("label", "")),
                        )
                    )
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    grades = result.get("final_grades", [])
                    grading_summary = dict(result.get("grading_summary", {}))
            if grades is None:
                result = generate_general_quiz_grade_result(
                    questions,
                    answers,
                    user_request=user_request,
                )
                grades = result.get("final_grades", [])
                grading_summary = dict(result.get("grading_summary", {}))
        except Exception as exc:
            error_message = str(exc)
            if "OPENAI_API_KEY" in error_message or "API key" in error_message:
                error_message = (
                    "일반 퀴즈 채점에는 OpenAI API Key가 필요합니다. API Key 버튼에서 설정해 주세요."
                )
            self.post_message(GeneralQuizGradeFailed(error_message, target))
            return

        score_summary = {
            "total": (sum(int(grade.get("score", 0)) for grade in grades) // len(grades))
            if grades
            else 0,
            "max": 100,
        }
        self.post_message(
            GeneralQuizGraded(grades, score_summary, grading_summary, target)
        )

    @on(QuizNodeStarted)
    def handle_quiz_node_started(self, message: QuizNodeStarted) -> None:
        self._set_quiz_progress_node(message.label, target=message.target)

    @on(ReadNodeStarted)
    def handle_read_node_started(self, message: ReadNodeStarted) -> None:
        self._set_read_progress_node(message.label)

    @on(GeneralGradeNodeStarted)
    def handle_general_grade_node_started(self, message: GeneralGradeNodeStarted) -> None:
        self._set_status(f"일반 퀴즈 채점 중 [{message.label}]")

    @on(QuizGenerated)
    def handle_quiz_generated(self, message: QuizGenerated) -> None:
        target = message.target
        session_id = self._session_id_from_target(target)
        if session_id:
            self._pending_quiz_targets.pop(session_id, None)
            self._quiz_progress_labels.pop(session_id, None)
            self._quiz_error_messages.pop(session_id, None)
        if self._pending_quiz_target is not None and self._session_id_from_target(
            self._pending_quiz_target
        ) == session_id:
            self._pending_quiz_target = None
        self._stop_quiz_status_animation()
        self._quiz_generation_error_message = ""
        content = (
            f"{self._result_metadata_block('md', created_at=message.created_at)}\n"
            f"{message.content}"
        )
        self._save_general_quiz_result_for_selection(
            content,
            message.questions,
            target=target,
        )
        if self._target_matches_current_selection(target):
            self.quiz_content = content
            self._set_status(self._current_generation_status_text() or "완료")
            self._refresh_quiz_workspace()
            self._rebuild_review_content_for_current_selection()
        self._refresh_session_progress()
        self._refresh_result_command_row()
        self._refresh_top_bar_context()

    @on(QuizFailed)
    def handle_quiz_failed(self, message: QuizFailed) -> None:
        target = message.target
        session_id = self._session_id_from_target(target)
        if session_id:
            self._pending_quiz_targets.pop(session_id, None)
            self._quiz_progress_labels.pop(session_id, None)
            self._quiz_error_messages[session_id] = message.error_message
        if self._pending_quiz_target is not None and self._session_id_from_target(
            self._pending_quiz_target
        ) == session_id:
            self._pending_quiz_target = None
        self._stop_quiz_status_animation()
        self._quiz_generation_error_message = message.error_message
        if self._target_matches_current_selection(target):
            self._set_status(self._current_generation_status_text() or "오류")
            self._refresh_quiz_workspace()
            self._set_tab_result("quiz", message.error_message)
        self._refresh_result_command_row()
        self._refresh_session_progress()

    @on(ReadGenerated)
    def handle_read_generated(self, message: ReadGenerated) -> None:
        target = message.target
        session_id = self._session_id_from_target(target)
        if session_id:
            self._pending_read_targets.pop(session_id, None)
        if self._pending_read_target is not None and self._session_id_from_target(
            self._pending_read_target
        ) == session_id:
            self._pending_read_target = None
        self._stop_read_status_animation()
        content = (
            f"{self._result_metadata_block('md', created_at=message.created_at)}\n"
            f"{message.content}"
        )
        self._save_read_result_for_selection(
            content,
            target=target,
        )
        if self._target_matches_current_selection(target):
            self._set_tab_result("read", content)
            self._set_status(self._current_generation_status_text() or "완료")
            self._rebuild_review_content_for_current_selection()
        self._refresh_session_progress()
        self._refresh_result_command_row()
        self._refresh_top_bar_context()

    @on(ReadFailed)
    def handle_read_failed(self, message: ReadFailed) -> None:
        target = message.target
        session_id = self._session_id_from_target(target)
        if session_id:
            self._pending_read_targets.pop(session_id, None)
        if self._pending_read_target is not None and self._session_id_from_target(
            self._pending_read_target
        ) == session_id:
            self._pending_read_target = None
        self._stop_read_status_animation()
        if self._target_matches_current_selection(target):
            self._set_status(self._current_generation_status_text() or "오류")
            self._set_tab_result("read", message.error_message)
        self._refresh_result_command_row()
        self._refresh_session_progress()

    @on(GeneralQuizGraded)
    def handle_general_quiz_graded(self, message: GeneralQuizGraded) -> None:
        self._general_quiz_grading_in_progress = False
        self.query_one("#result-quiz-grade", Button).disabled = False
        self.query_one("#result-quiz-grade-top", Button).disabled = False
        self.query_one("#result-quiz-retry", Button).disabled = False
        self.query_one("#result-quiz-retry-top", Button).disabled = False
        target = message.target or self._pending_general_quiz_grade_target
        session = self._load_or_create_learning_session_for_target(target)
        if session is None:
            self._pending_general_quiz_grade_target = None
            return
        complete_general_quiz_grading(
            session,
            grades=message.grades,
            score_summary=message.score_summary,
            grading_summary=message.grading_summary,
            now=now_timestamp(),
        )
        self._refresh_review_summary_for_session(session)
        save_learning_session(
            session,
            repo_source=str(target.get("repo_source", "local")) if target else self._current_repo_source(),
            github_repo_url=str(target.get("github_repo_url", "")) if target else self.github_repo_url,
            local_repo_root=(
                str(target.get("local_repo_root"))
                if target and target.get("local_repo_root") is not None
                else (
                    str(self._current_local_repo_root())
                    if self._current_local_repo_root() is not None
                    else None
                )
            ),
        )
        self._pending_general_quiz_grade_target = None
        if self._target_matches_current_selection(target):
            self._set_result_tab("quiz")
            self._set_status(
                f"일반 퀴즈 채점 완료 ({message.score_summary.get('total', 0)}/{message.score_summary.get('max', 100)})"
            )
            self._rebuild_review_content_for_current_selection()
            self._refresh_quiz_workspace()
        self._refresh_session_progress()
        self._refresh_top_bar_context()

    @on(GeneralQuizGradeFailed)
    def handle_general_quiz_grade_failed(self, message: GeneralQuizGradeFailed) -> None:
        self._general_quiz_grading_in_progress = False
        self._pending_general_quiz_grade_target = None
        self.query_one("#result-quiz-grade", Button).disabled = False
        self.query_one("#result-quiz-grade-top", Button).disabled = False
        self.query_one("#result-quiz-retry", Button).disabled = False
        self.query_one("#result-quiz-retry-top", Button).disabled = False
        if self._target_matches_current_selection(message.target):
            self._set_status("오류")
            self.query_one("#result-quiz-feedback", Static).update(message.error_message)
            self._refresh_quiz_workspace()

    def _toggle_result_metadata(self) -> None:
        content = self._current_result_content_for_download()
        if split_result_metadata(content) is None:
            return
        self.result_metadata_expanded = not self.result_metadata_expanded
        try:
            meta_button = self.query_one("#result-meta-toggle", Button)
            meta_button.label = "meta -" if self.result_metadata_expanded else "meta +"
        except Exception:
            pass
        if self.result_view_mode == "markdown" and self.result_tab in {"read", "inline"}:
            self._update_tab_markdown(self.result_tab, self._content_for_result_tab(self.result_tab))

    @on(RepoCommitsLoaded)
    def handle_repo_commits_loaded(self, message: RepoCommitsLoaded) -> None:
        if message.repo_key != self._current_repo_key():
            return
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
        if message.repo_key != self._current_repo_key():
            return
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

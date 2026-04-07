"""AppStatusBar widget: persistent bottom status bar showing repo, range, and mode."""

from rich.text import Text
from rich.text import Text as RichText
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static


class AppStatusBar(Widget):
    """Two-row bar docked at the very bottom: separator line + status content."""

    DEFAULT_CSS = """
    AppStatusBar {
        height: 2;
        background: transparent;
        color: $text;
        layout: vertical;
    }

    AppStatusBar .asb-sep {
        height: 1;
        background: transparent;
        color: $panel-lighten-1;
        overflow: hidden hidden;
    }

    AppStatusBar #asb-row {
        height: 1;
        padding: 0 1;
    }

    AppStatusBar #asb-line {
        width: 1fr;
        height: 1;
    }

    AppStatusBar #asb-notify {
        width: auto;
        height: 1;
        color: $text-muted;
        content-align: right middle;
    }
    """

    _repo_name: str = ""
    _oldest_sha: str = ""
    _newest_sha: str = ""
    _commit_count: int = 0
    _mode: str = "idle"
    _quiz_progress: tuple[int, int] = (0, 0)
    _notification: str = ""
    _hook_installed: bool | None = None
    _queue_label: str = ""

    def compose(self) -> ComposeResult:
        # yield Static(RichText("─" * 500, no_wrap=True), classes="asb-sep")
        with Horizontal(id="asb-row"):
            yield Static("", id="asb-line")
            yield Static("", id="asb-notify")

    def on_mount(self) -> None:
        self._refresh()

    def set_repo(self, repo_name: str) -> None:
        self._repo_name = repo_name
        self._refresh()

    def set_range(self, oldest_sha: str, newest_sha: str, count: int) -> None:
        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha
        self._commit_count = count
        self._refresh()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._refresh()

    def set_quiz_progress(self, current: int, total: int) -> None:
        self._quiz_progress = (current, total)
        self._refresh()

    def set_hook(self, installed: bool | None) -> None:
        self._hook_installed = installed
        self._refresh()

    def set_queue(self, label: str) -> None:
        self._queue_label = label
        self._refresh()

    def set_notification(self, text: str) -> None:
        self._notification = text
        self._refresh()

    def clear_notification(self) -> None:
        self._notification = ""
        self._refresh()

    def _refresh(self) -> None:
        try:
            widget = self.query_one("#asb-line", Static)
            t = Text(no_wrap=True)
            sep = Text(" | ", style="dim")

            if self._repo_name:
                t.append(self._repo_name, style="")

            if self._oldest_sha:
                t.append_text(sep)
                if self._oldest_sha == self._newest_sha:
                    t.append(self._oldest_sha[:7], style="bold color(214)")
                else:
                    t.append(self._oldest_sha[:7], style="bold color(214)")
                    t.append("..", style="dim")
                    t.append(self._newest_sha[:7], style="bold color(214)")
                if self._commit_count > 0:
                    t.append(f" ({self._commit_count})", style="dim")

            if self._hook_installed is not None:
                t.append_text(sep)
                if self._hook_installed:
                    t.append("hook:ON", style="bold bright_yellow")
                else:
                    t.append("hook:OFF", style="dim")

            t.append_text(sep)
            mode_labels = {
                "idle": "IDLE",
                "quiz_loading": "LOADING",
                "quiz_answering": "QUIZ",
                "grading": "GRADING",
                "reviewing": "REVIEWING",
                "chatting": "CHAT",
            }
            mode_label = mode_labels.get(self._mode, self._mode.upper())
            current, total = self._quiz_progress
            active_modes = {"quiz_loading", "grading", "reviewing", "chatting"}
            if total > 0 and self._mode == "quiz_answering":
                mode_label = f"{mode_label} {current}/{total}"
                t.append(mode_label, style="color(214)")
            elif self._mode in active_modes:
                t.append(mode_label, style="bold bright_yellow")
            else:
                t.append(mode_label, style="dim")

            if self._queue_label:
                t.append_text(sep)
                t.append(self._queue_label, style="bold color(214)")

            widget.update(t)

            notify_widget = self.query_one("#asb-notify", Static)
            if self._notification:
                n = Text(no_wrap=True)
                n.append(self._notification, style="dim")
                notify_widget.update(n)
            else:
                notify_widget.update("")
        except Exception:
            pass

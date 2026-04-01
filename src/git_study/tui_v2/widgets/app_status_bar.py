"""AppStatusBar widget: persistent bottom status bar showing repo, range, and mode."""

from rich.text import Text
from rich.text import Text as RichText
from textual.app import ComposeResult
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

    AppStatusBar #asb-line {
        height: 1;
        padding: 0 1;
    }
    """

    _repo_name: str = ""
    _oldest_sha: str = ""
    _newest_sha: str = ""
    _commit_count: int = 0
    _mode: str = "idle"
    _quiz_progress: tuple[int, int] = (0, 0)

    def compose(self) -> ComposeResult:
        yield Static(RichText("─" * 500, no_wrap=True), classes="asb-sep")
        yield Static("", id="asb-line")

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
                    t.append(f" ({self._commit_count})", style="dim")
                else:
                    t.append(self._oldest_sha[:7], style="bold color(214)")
                    t.append("..", style="dim")
                    t.append(self._newest_sha[:7], style="bold color(214)")
                    t.append(f" ({self._commit_count})", style="dim")

            t.append_text(sep)
            mode_labels = {
                "idle": "IDLE",
                "quiz_loading": "LOADING",
                "quiz_answering": "QUIZ",
                "grading": "GRADING",
            }
            mode_label = mode_labels.get(self._mode, self._mode.upper())
            current, total = self._quiz_progress
            if total > 0 and self._mode == "quiz_answering":
                mode_label = f"{mode_label} Q{current}/{total}"
                t.append(mode_label, style="color(214)")
            else:
                t.append(mode_label, style="dim")

            widget.update(t)
        except Exception:
            pass

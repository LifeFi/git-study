"""HistoryView widget: scrollable activity log (Claude Code-style)."""

from __future__ import annotations

from __future__ import annotations

from importlib.metadata import version, PackageNotFoundError
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static


def _get_version() -> str:
    try:
        return version("git-study")
    except PackageNotFoundError:
        return "?"

_CHEVRON = ">"
_RESULT_PREFIX = "  └ "


class HistoryView(Widget):
    """Scrollable activity log displayed when no code view is active."""

    DEFAULT_CSS = """
    HistoryView {
        width: 1fr;
        height: auto;
    }

    HistoryView #hv-content {
        width: 1fr;
        height: auto;
        padding: 1 0;
    }

    HistoryView .hv-welcome {
        color: $text-muted;
        padding: 0 2;
        margin-bottom: 1;
    }

    HistoryView .hv-cmd-block {
        height: auto;
        margin-bottom: 1;
    }

    HistoryView .hv-cmd-row {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
    }

    HistoryView .hv-result-row {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }

    HistoryView .hv-result-row.-success {
        color: $success;
    }

    HistoryView .hv-result-row.-error {
        color: $error;
    }

    HistoryView .hv-result-row.-info {
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="hv-content"):
            yield Static(self._welcome_text(), classes="hv-welcome")

    def _welcome_text(self) -> Text:
        v = _get_version()
        t = Text()
        t.append("git-study", style="bold white")
        t.append(f" v{v}\n", style="dim")
        t.append("\n")
        t.append("  /commits", style="bold cyan")
        t.append("  커밋 범위 선택\n", style="dim")
        t.append("  /quiz", style="bold cyan")
        t.append("     퀴즈 생성\n", style="dim")
        t.append("  /grade", style="bold cyan")
        t.append("    채점\n", style="dim")
        t.append("  /help", style="bold cyan")
        t.append("     도움말\n", style="dim")
        t.append("  Ctrl+Q", style="bold cyan")
        t.append("    종료\n", style="dim")
        return t

    def append_command(self, cmd: str) -> Vertical:
        """Append a command row (▶ /cmd style). Returns the block for adding results."""
        container = self.query_one("#hv-content", Vertical)
        cmd_text = Text()
        cmd_text.append(f"{_CHEVRON} ", style="bold dim")
        cmd_text.append(cmd)
        block = Vertical(classes="hv-cmd-block")
        cmd_row = Static(cmd_text, classes="hv-cmd-row")
        container.mount(block)
        block.mount(cmd_row)
        return block

    def append_result(self, text: str, style: str = "info", block: Vertical | None = None) -> None:
        """Append a result row (└ text) under the last command block or given block."""
        if block is None:
            # Find last hv-cmd-block
            container = self.query_one("#hv-content", Vertical)
            blocks = list(container.query(".hv-cmd-block"))
            block = blocks[-1] if blocks else container
        result_text = Text()
        result_text.append(_RESULT_PREFIX, style="dim")
        result_text.append(text)
        result_row = Static(result_text, classes=f"hv-result-row -{style}")
        block.mount(result_row)

    def append(self, text: str, style: str = "info") -> None:
        """Append a standalone result row (no command parent)."""
        container = self.query_one("#hv-content", Vertical)
        result_text = Text()
        result_text.append(_RESULT_PREFIX, style="dim")
        result_text.append(text)
        entry = Static(result_text, classes=f"hv-result-row -{style}")
        container.mount(entry)

    def clear(self) -> None:
        """Clear all history entries (keep welcome)."""
        container = self.query_one("#hv-content", Vertical)
        for child in list(container.children):
            if "hv-welcome" not in child.classes:
                child.remove()

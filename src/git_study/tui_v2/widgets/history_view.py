"""HistoryView widget: scrollable activity log (Claude Code-style)."""

from __future__ import annotations

from __future__ import annotations

from importlib.metadata import version, PackageNotFoundError
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Markdown, Static


def _get_version() -> str:
    try:
        return version("git-study")
    except PackageNotFoundError:
        return "?"

_CHEVRON = ">"
_RESULT_PREFIX = "  └ "
_cmd_block_counter: int = 0
_SPINNER_FRAMES = ["|", "/", "—", "\\"]


class LoadingRow(Widget):
    """스피너 애니메이션이 포함된 한 줄짜리 진행 상태 위젯."""

    DEFAULT_CSS = """
    LoadingRow {
        height: 1;
        padding: 0 2;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(classes="hv-loading-row")
        self._text = text
        self._frame_idx = 0

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
        self.refresh()

    def set_text(self, text: str) -> None:
        self._text = text
        self.refresh()

    def render(self) -> Text:
        frame = _SPINNER_FRAMES[self._frame_idx]
        t = Text()
        t.append(f"{frame} ", style="bold yellow")
        t.append(self._text, style="dim")
        return t


class HistoryView(Widget):
    """Scrollable activity log displayed when no code view is active."""

    DEFAULT_CSS = """
    HistoryView {
        width: 1fr;
        height: auto;
    }

    HistoryView .hv-separator {
        height: 1;
        padding: 0 1;
        color: $text-disabled;
        margin: 1 0;
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
        color: $text;
        text-style: dim;
    }

    HistoryView .hv-result-row.-error {
        color: $error;
    }

    HistoryView .hv-result-row.-info {
        color: $text-muted;
    }

    HistoryView .hv-markdown {
        margin: 0 2;
        padding: 0;
    }

    HistoryView .hv-chat-block {
        height: auto;
        margin-bottom: 1;
    }

    HistoryView .hv-user-msg {
        height: auto;
        padding: 0 2;
        background: $panel;
        color: $text;
    }

    HistoryView .hv-assistant-streaming {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }

    HistoryView .hv-tool-call {
        height: 1;
        padding: 0 3;
        color: $text-disabled;
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
        t.append("AI writes. But do you?\n", style="dim italic")
        t.append("\n")
        t.append(" /commits", style="bold cyan")
        t.append("  커밋 범위 선택\n", style="dim")
        t.append(" /quiz", style="bold cyan")
        t.append("     퀴즈 생성\n", style="dim")
        t.append(" /review", style="bold cyan")
        t.append("   커밋 해설 보기\n", style="dim")
        t.append(" /grade", style="bold cyan")
        t.append("    채점\n", style="dim")
        t.append(" /clear", style="bold cyan")
        t.append("    대화 초기화\n", style="dim")
        t.append(" /resume", style="bold cyan")
        t.append("   이전 대화 불러오기\n", style="dim")
        t.append(" /help", style="bold cyan")
        t.append("     도움말\n", style="dim")
        t.append(" Ctrl+Q", style="bold cyan")
        t.append("    종료\n", style="dim")
        return t

    def append_command(self, cmd: str) -> Vertical:
        """Append a command row (▶ /cmd style). Returns the block for adding results."""
        global _cmd_block_counter
        _cmd_block_counter += 1
        block_id = f"hvcb{_cmd_block_counter}"
        container = self.query_one("#hv-content", Vertical)
        cmd_text = Text()
        cmd_text.append(f"{_CHEVRON} ", style="bold dim")
        cmd_text.append(cmd)
        block = Vertical(classes="hv-cmd-block", id=block_id)
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
        result_text.append(" ")
        result_text.append(text)
        entry = Static(result_text, classes=f"hv-result-row -{style}")
        container.mount(entry)

    def _scroll_to_end(self) -> None:
        """실제 스크롤 컨테이너(#scroll-wrapper)를 맨 아래로 스크롤."""
        try:
            self.app.query_one("#scroll-wrapper").scroll_end(animate=False)
        except Exception:
            pass

    def append_markdown(self, md_text: str, block: Vertical | None = None) -> None:
        """마크다운 텍스트를 렌더링해 HistoryView 블록에 추가."""
        target = block or self.query_one("#hv-content", Vertical)
        md_widget = Markdown(md_text, classes="hv-markdown")
        target.mount(md_widget)
        if block is not None:
            # 특정 block에 추가할 때는 해당 block이 보이도록 스크롤
            # (끝으로 스크롤하면 중간에 삽입된 결과가 뷰포트 밖으로 나감)
            self.call_after_refresh(block.scroll_visible, animate=False)
        else:
            self._scroll_to_end()

    def append_separator(self, text: str = "─" * 40) -> None:
        """구분선 추가 (세션 복원 시 이전/현재 세션 구분용)."""
        container = self.query_one("#hv-content", Vertical)
        sep = Static(text, classes="hv-separator")
        container.mount(sep)

    def append_user_message(self, text: str) -> Vertical:
        """유저 채팅 메시지 블록 추가. 결과 붙일 block 반환."""
        container = self.query_one("#hv-content", Vertical)
        block = Vertical(classes="hv-chat-block")
        user_text = Text()
        user_text.append(f"{_CHEVRON} ", style="bold dim")
        user_text.append(text)
        user_row = Static(user_text, classes="hv-user-msg")
        container.mount(block)
        block.mount(user_row)
        self._scroll_to_end()
        return block

    def begin_streaming(self, block: Vertical) -> Static:
        """스트리밍 시작 — block에 plain text Static 추가 후 반환."""
        block.mount(Static("", classes="hv-assistant-streaming"))
        streaming_widget = Static("▌", classes="hv-assistant-streaming")
        block.mount(streaming_widget)
        self._scroll_to_end()
        return streaming_widget

    def update_streaming(self, widget: Static, text: str) -> None:
        """스트리밍 중 누적 텍스트 업데이트."""
        widget.update(text + "▌")
        self._scroll_to_end()

    def end_streaming(self, block: Vertical, widget: Static, full_text: str) -> None:
        """스트리밍 완료 — Static을 Markdown으로 교체."""
        widget.remove()
        if full_text.strip():
            md = Markdown(full_text, classes="hv-markdown")
            block.mount(md)
        self._scroll_to_end()

    def append_tool_call(self, name: str, block: Vertical | None = None) -> None:
        """tool 호출 표시 (한 줄 요약)."""
        target = block or self.query_one("#hv-content", Vertical)
        row = Static(f"  🔧 {name} 실행 중...", classes="hv-tool-call")
        target.mount(row)

    def begin_progress(self, text: str, block: Vertical | None = None) -> LoadingRow:
        """스피너 로딩 위젯을 block 아래에 추가. 반환된 위젯으로 업데이트/제거."""
        target = block or self.query_one("#hv-content", Vertical)
        row = LoadingRow(text)
        target.mount(row)
        self._scroll_to_end()
        return row

    def end_progress(self, row: LoadingRow) -> None:
        """로딩 위젯 제거."""
        try:
            row.remove()
        except Exception:
            pass

    def clear(self) -> None:
        """Clear all history entries (keep welcome)."""
        container = self.query_one("#hv-content", Vertical)
        for child in list(container.children):
            if "hv-welcome" not in child.classes:
                child.remove()

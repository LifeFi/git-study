"""HistoryView widget: scrollable activity log (Claude Code-style)."""

from __future__ import annotations

from __future__ import annotations

from importlib.metadata import version, PackageNotFoundError
from rich.console import Group as RichGroup
from rich.markdown import Markdown as RichMarkdown
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


_CHEVRON = "❯"  # ❯
_RESULT_PREFIX = "⎿  "  # └ ⎿
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
        self._ticks = 0  # 0.1초 단위 카운터

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
        self._ticks += 1
        self.refresh()

    def set_text(self, text: str) -> None:
        self._text = text
        self.refresh()

    @property
    def elapsed_seconds(self) -> int:
        return self._ticks // 10

    def _elapsed_str(self) -> str | None:
        seconds = self.elapsed_seconds
        if seconds < 10:
            return None
        m, s = divmod(seconds, 60)
        if m > 0:
            return f"({m}m {s:02d}s)"
        return f"({s}s)"

    def render(self) -> Text:
        frame = _SPINNER_FRAMES[self._frame_idx]
        t = Text()
        t.append(f"{frame} ", style="bold yellow")
        t.append(self._text, style="dim")
        elapsed = self._elapsed_str()
        if elapsed:
            t.append(f"  {elapsed}", style="dim")
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
        background: $boost;
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
        padding: 0 1;
        background: $boost;
        color: $text;
    }

    HistoryView .hv-assistant-streaming {
        height: auto;
        padding: 0 2;
    }

    HistoryView .hv-tool-call {
        height: 1;
        padding: 0 3;
        color: $text-disabled;
    }

    HistoryView .hv-token-usage {
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="hv-content"):
            yield Static(self._welcome_text(), classes="hv-welcome")

    def _logo(self, v: str) -> Text:
        """로고 블록 — 교체 시 이 메서드만 수정.

        현재: amber 포션 + 방울 3개

            ○  ○
               ○
             ╭╯ ╰╮    git-study vX.X
            ▐▒▒▒▒▒▌   AI writes. But do you?
             ╰───╯
        """
        t = Text()
        t.append("        ○ ○\n", style="color(229)")
        t.append("       ○\n", style="color(229)")
        t.append("     ╭╯ ╰╮", style="color(245)")
        t.append("    ")
        t.append("git-study", style="bold white")
        t.append(f" v{v}\n", style="dim")
        t.append("    ▐", style="color(245)")
        t.append("▒▒▒▒▒", style="color(214)")
        t.append("▌", style="color(245)")
        t.append("   ")
        t.append("AI writes. But do you?\n", style="dim italic")
        t.append("     ╰───╯\n", style="color(245)")
        return t

    def _welcome_text(self) -> Text:
        v = _get_version()
        t = Text()
        t.append_text(self._logo(v))
        t.append("\n")
        t.append("  Step 1  ", style="dim")
        t.append("/commits", style="bold cyan")
        t.append("    pick a commit range\n", style="dim")
        t.append("  Step 2  ", style="dim")
        t.append("/quiz", style="bold cyan")
        t.append("       generate questions from the diff\n", style="dim")
        t.append("  Step 3  ", style="dim")
        t.append("/answer", style="bold cyan")
        t.append("     click a quiz block · or type /answer\n", style="dim")
        t.append("  Step 4  ", style="dim")
        t.append("/grade", style="bold cyan")
        t.append("      get feedback\n", style="dim")
        t.append("\n")
        t.append("  /help", style="cyan")
        t.append(" for all commands   ", style="dim")
        t.append("Shift+Tab", style="cyan")
        t.append(" toggle panels   ", style="dim")
        t.append("Ctrl+Q", style="cyan")
        t.append(" to quit\n", style="dim")
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

    def append_result(
        self, text: "str | Text", style: str = "info", block: Vertical | None = None
    ) -> None:
        """Append a result row (└ text) under the last command block or given block."""
        if block is None:
            # Find last hv-cmd-block
            container = self.query_one("#hv-content", Vertical)
            blocks = list(container.query(".hv-cmd-block"))
            block = blocks[-1] if blocks else container
        result_text = Text()
        result_text.append(_RESULT_PREFIX, style="dim")
        if isinstance(text, Text):
            result_text.append_text(text)
        else:
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

    def append_markdown(self, md_text: str, block: Vertical | None = None) -> None:
        """마크다운 텍스트를 렌더링해 HistoryView 블록에 추가."""
        target = block or self.query_one("#hv-content", Vertical)
        md_widget = Markdown(md_text, classes="hv-markdown")
        target.mount(md_widget)
        if block is not None:
            # 특정 block에 추가할 때는 해당 block이 보이도록 스크롤
            # (끝으로 스크롤하면 중간에 삽입된 결과가 뷰포트 밖으로 나감)
            self.call_after_refresh(block.scroll_visible, animate=False)

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
        return block

    def begin_streaming(self, block: Vertical) -> Static:
        """스트리밍 시작 — block에 plain text Static 추가 후 반환."""
        streaming_widget = Static("▌", classes="hv-assistant-streaming")
        block.mount(streaming_widget)
        return streaming_widget

    def update_streaming(self, widget: Static, text: str) -> None:
        """스트리밍 중 누적 텍스트를 마크다운으로 업데이트."""
        widget.update(RichMarkdown(text + " ▌"))

    def end_streaming(self, block: Vertical, widget: Static, full_text: str) -> None:
        """스트리밍 완료 — 최종 마크다운 렌더링 (widget 교체 없이)."""
        if full_text.strip():
            widget.remove_class("hv-assistant-streaming")
            widget.add_class("hv-markdown")
            widget.update(RichMarkdown(full_text))
        else:
            widget.remove()

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
        return row

    def end_progress(self, row: LoadingRow) -> None:
        """로딩 위젯 제거."""
        try:
            row.remove()
        except Exception:
            pass

    def append_rich(self, rich_text: Text, block: Vertical | None = None) -> None:
        """Rich Text 객체를 직접 렌더링해 추가 (바 차트 등 커스텀 레이아웃용)."""
        target = block or self.query_one("#hv-content", Vertical)
        widget = Static(rich_text, classes="hv-result-row -info")
        target.mount(widget)

    def append_token_usage(
        self,
        block: Vertical | None,
        input_tokens: int,
        output_tokens: int,
        insert_before: Widget | None = None,
        model_name: str | None = None,
    ) -> None:
        """토큰 사용량을 한 줄로 표시 (LLM 호출 완료 후).

        insert_before: 지정 시 해당 위젯 앞에 삽입 (chat에서 명령어 바로 아래 위치용).
        """
        t = Text()
        if model_name:
            t.append(f"  {model_name}", style="dim")
            t.append("  ", style="dim")
        else:
            t.append("  ", style="dim")
        t.append("↑ ", style="dim")
        t.append(f"{input_tokens:,}", style="dim cyan")
        t.append("  ↓ ", style="dim")
        t.append(f"{output_tokens:,}", style="dim green")
        t.append(" tokens", style="dim")
        target = block or self.query_one("#hv-content", Vertical)
        # 이전 토큰 행의 여백을 0으로 리셋 — 마지막 행만 아래 여백을 가짐 (CSS default=1)
        for prev in target.query(".hv-token-usage"):
            prev.styles.margin_bottom = 0
        row = Static(t, classes="hv-token-usage hv-result-row -info")
        if insert_before is not None:
            target.mount(row, before=insert_before)
        else:
            target.mount(row)

    def clear(self) -> None:
        """Clear all history entries (keep welcome)."""
        container = self.query_one("#hv-content", Vertical)
        for child in list(container.children):
            if "hv-welcome" not in child.classes:
                child.remove()

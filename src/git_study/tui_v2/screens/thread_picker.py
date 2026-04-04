"""ThreadPickerScreen — modal for selecting a previous chat thread."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class ThreadPickerScreen(ModalScreen[dict | None]):
    """이전 대화 목록에서 하나를 선택하는 모달.

    Returns selected thread dict on confirm, None on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("enter", "confirm", "선택", priority=True),
    ]

    CSS = """
    ThreadPickerScreen {
        align: center middle;
    }

    ThreadPickerScreen > Vertical {
        width: 76;
        height: auto;
        max-height: 80vh;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    ThreadPickerScreen #tp-title {
        text-align: center;
        color: $text;
        padding-bottom: 1;
    }

    ThreadPickerScreen #tp-list {
        height: auto;
        max-height: 60vh;
        border: solid $panel;
    }

    ThreadPickerScreen #tp-hint {
        color: $text-muted;
        text-align: center;
        padding-top: 1;
    }

    ThreadPickerScreen #tp-list ListItem {
        margin-bottom: 1;
    }
    """

    def __init__(self, threads: list[dict], current_id: str) -> None:
        super().__init__()
        # 최신순 정렬
        self._threads = sorted(threads, key=lambda t: t.get("created_at", ""), reverse=True)
        self._current_id = current_id

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        with Vertical():
            yield Static("이전 대화 선택", id="tp-title")
            yield ListView(
                *[self._make_item(t) for t in self._threads],
                id="tp-list",
            )
            yield Static("Enter: 선택  Esc: 취소", id="tp-hint")

    def _make_item(self, thread: dict) -> ListItem:
        is_current = thread["id"] == self._current_id
        msg_count = thread.get("msg_count", 0)
        previews: list[str] = thread.get("previews", [])
        created_at = thread.get("created_at", "")

        # created_at 형식: "2026-04-01T14:32:05" → "2026-04-01 14:32:05"
        dt_display = created_at.replace("T", " ")[:19] if created_at else ""

        # 1줄: 날짜+시분초  메시지 수  [← 현재]
        line1 = Text()
        line1.append("  ")
        line1.append(dt_display, style="bold" if is_current else "")
        line1.append(f"  ({msg_count}개)", style="dim")
        if is_current:
            line1.append("  ← 현재", style="cyan")

        # 2~3줄: 미리보기 2개
        preview_lines = []
        if previews:
            for p in previews:
                t = Text()
                t.append("  > ", style="dim green")
                t.append(p, style="dim")
                preview_lines.append(t)
        else:
            t = Text()
            t.append("  (대화 없음)", style="dim")
            preview_lines.append(t)

        parts: list = [line1]
        for pl in preview_lines:
            parts += ["\n", pl]

        combined = Text.assemble(*parts)
        return ListItem(Label(combined))

    def action_confirm(self) -> None:
        lv = self.query_one("#tp-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._threads):
            self.dismiss(self._threads[idx])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

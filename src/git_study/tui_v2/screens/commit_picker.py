"""CommitPickerScreen — modal for selecting a commit range (v1 style)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from ...tui.commit_selection import (
    CommitSelection,
    selected_commit_indices,
    selection_help_text,
    selection_prefix,
    update_selection_for_index,
)
from ...tui.state import load_learning_session_file


class CommitPickerScreen(ModalScreen[CommitSelection | None]):
    """Fullscreen modal for picking a commit range.

    Returns ``CommitSelection`` on confirm, ``None`` on cancel.
    Layout mirrors the v1 commit panel:
      [S/E/·/   ] [●/·/  ] subject
    Bottom panel shows details of the focused commit.
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("enter", "confirm", "확인", priority=True),
        Binding("space", "toggle_select", "선택/해제", priority=True),
    ]

    CSS = """
    CommitPickerScreen {
        align: center middle;
    }

    #picker-container {
        width: 92%;
        height: 92%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }

    #picker-title {
        text-style: bold;
        padding: 1 0 0 0;
        color: $text;
    }

    #picker-help {
        color: $text-muted;
        padding-bottom: 1;
    }

    #commit-list {
        height: 1fr;
        border: solid $primary-darken-2;
    }

    #detail-panel {
        height: 8;
        border-top: solid $panel;
        padding: 0 1;
    }

    #detail-view {
        height: 1fr;
        background: transparent;
        padding: 0;
    }

    #status-bar {
        height: 1;
        padding-bottom: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        commits: list[dict],
        initial_selection: CommitSelection | None = None,
        *,
        repo_source: str = "local",
        github_repo_url: str = "",
        local_repo_root=None,
    ) -> None:
        super().__init__()
        self._commits = commits
        self._selection: CommitSelection = initial_selection or CommitSelection()
        self._repo_source = repo_source
        self._github_repo_url = github_repo_url
        self._local_repo_root = local_repo_root

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-container"):
            yield Static("커밋 범위 선택", id="picker-title")
            yield Static(
                "Space: S→E 선택  |  Enter: 확인  |  Esc: 취소",
                id="picker-help",
            )
            with ListView(id="commit-list"):
                for i in range(len(self._commits)):
                    yield ListItem(
                        Label(self._commit_label_text(i)),
                        id=f"ci-{i}",
                    )
            with Vertical(id="detail-panel"):
                yield Static("", id="detail-view")
            yield Static(self._build_status(), id="status-bar")

    def on_mount(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        if self._commits:
            start = self._selection.start_index
            focus_index = start if start is not None else 0
            lv.index = focus_index
            self._update_detail(focus_index)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_select(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        idx = lv.index
        if idx is None:
            return
        self._selection = update_selection_for_index(self._selection, idx)
        self._refresh_all_labels()

    def action_confirm(self) -> None:
        self.dismiss(self._selection)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        # Extract index from id "ci-{i}"
        item_id = event.item.id or ""
        if item_id.startswith("ci-"):
            try:
                idx = int(item_id[3:])
                self._update_detail(idx)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Label rendering (v1 style)
    # ------------------------------------------------------------------

    def _commit_label_text(self, index: int) -> Text:
        commit = self._commits[index]
        prefix_kind = selection_prefix(index, self._selection)
        if prefix_kind == "start":
            prefix = Text(" S ", style="bold green")
        elif prefix_kind == "end":
            prefix = Text(" E ", style="bold green")
        elif prefix_kind == "inside":
            prefix = Text(" · ", style="green")
        else:
            prefix = Text("   ")

        line = Text()
        line.append_text(prefix)

        # Session marker
        marker = self._session_marker(commit.get("sha", ""))
        if marker is not None:
            marker_text, marker_style = marker
            line.append(f"{marker_text} ", style=marker_style)
        else:
            line.append("  ")

        # Subject (first line of message)
        msg = commit.get("message", commit.get("msg", commit.get("subject", "")))
        subject = msg.splitlines()[0][:72] if msg else ""
        line.append(subject)
        return line

    def _session_marker(self, sha: str) -> tuple[str, str] | None:
        """Check if a v2 session exists for this single commit SHA."""
        if not sha:
            return None
        session_id = f"{sha[:7]}-{sha[:7]}"
        try:
            payload = load_learning_session_file(
                session_id,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            return None
        if payload is None:
            return None
        grades = payload.get("grades", [])
        if grades:
            return ("·", "grey62")
        return ("●", "green")

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------

    def _update_detail(self, index: int) -> None:
        if index >= len(self._commits):
            return
        commit = self._commits[index]
        sha = commit.get("sha", "")
        msg = commit.get("message", commit.get("msg", commit.get("subject", "")))
        subject = msg.splitlines()[0] if msg else ""
        author = commit.get("author", commit.get("author_name", ""))
        date = commit.get("date", commit.get("committed_datetime", ""))[:19]

        lines = Text()
        lines.append(f"SHA:     ", style="dim")
        lines.append(f"{sha[:40]}\n", style="cyan")
        lines.append(f"Subject: ", style="dim")
        lines.append(f"{subject}\n")
        lines.append(f"Author:  ", style="dim")
        lines.append(f"{author}\n")
        lines.append(f"Date:    ", style="dim")
        lines.append(f"{date}\n")

        self.query_one("#detail-view", Static).update(lines)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_status(self) -> str:
        count = len(selected_commit_indices(self._selection))
        return selection_help_text(self._selection, count)

    def _refresh_all_labels(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        for i, item in enumerate(lv.children):
            if i >= len(self._commits):
                break
            try:
                label = item.query_one(Label)
                label.update(self._commit_label_text(i))
            except Exception:
                pass
        self.query_one("#status-bar", Static).update(self._build_status())

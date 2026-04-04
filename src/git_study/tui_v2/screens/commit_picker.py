"""CommitPickerScreen — modal for selecting a commit range (v1 style)."""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from ...domain.repo_context import DEFAULT_COMMIT_LIST_LIMIT, get_commit_list_snapshot
from ...tui.commit_selection import (
    CommitSelection,
    selected_commit_indices,
    selection_help_text,
    selection_prefix,
    update_selection_for_index,
)
from ...tui.state import get_session_repo_dir, load_learning_session_file


class CommitPickerScreen(ModalScreen[tuple[CommitSelection, list[dict]] | None]):
    """Fullscreen modal for picking a commit range.

    Returns ``CommitSelection`` on confirm, ``None`` on cancel.
    Layout mirrors the v1 commit panel:
      [S/E/·/   ] [●/·/  ] subject
    Bottom panel shows details of the focused commit.
    """

    BINDINGS = [
        Binding("shift+escape", "clear_selection", "선택 해제", priority=True),
        Binding("escape", "cancel", "취소"),
        Binding("enter", "confirm", "확인", priority=True),
        Binding("space", "toggle_select", "선택/해제", priority=True),
        Binding("home", "jump_top", "맨 위로", priority=True),
        Binding("end", "jump_bottom", "맨 아래로", priority=True),
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
        height: 12;
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
        has_more: bool = False,
        total_count: int = 0,
    ) -> None:
        super().__init__()
        self._commits = commits
        self._selection: CommitSelection = initial_selection or CommitSelection()
        self._repo_source = repo_source
        self._github_repo_url = github_repo_url
        self._local_repo_root = local_repo_root
        self._has_more = has_more
        self._total_count = total_count
        self._limit = len(commits)
        # Cache session markers to avoid repeated I/O per render
        self._session_cache: dict[str, tuple[str, str] | None] = {}
        # All multi-commit range sessions for badge display
        self._range_sessions: list[dict] = []

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-container"):
            yield Static("커밋 범위 선택", id="picker-title")
            yield Static(
                "Space: S→E 선택  |  Shift+Esc: 전체 해제  |  Enter: 확인  |  Esc: 취소",
                id="picker-help",
            )
            with ListView(id="commit-list"):
                for i in range(len(self._commits)):
                    yield ListItem(
                        Label(self._commit_label_text(i)),
                        id=f"ci-{i}",
                    )
                if self._has_more:
                    yield ListItem(Label(self._load_more_label()), id="ci-load-more")
                    yield ListItem(Label(self._load_all_label()), id="ci-load-all")
            with Vertical(id="detail-panel"):
                yield Static("", id="detail-view")
            yield Static(self._build_status(), id="status-bar")

    def on_mount(self) -> None:
        # Pre-load all session markers once to avoid repeated file I/O during rendering
        for commit in self._commits:
            sha = commit.get("sha", "")
            if sha:
                self._session_cache[sha] = self._load_session_marker(sha)
        self._range_sessions = self._load_all_range_sessions()
        self._refresh_all_labels()
        lv = self.query_one("#commit-list", ListView)
        if self._commits:
            start = self._selection.start_index
            focus_index = start if start is not None else 0
            lv.index = focus_index
            self._update_detail(focus_index)
        self._update_range_session_banner()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_clear_selection(self) -> None:
        self._selection = CommitSelection()
        self._refresh_all_labels()
        self._update_range_session_banner()

    async def action_toggle_select(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        idx = lv.index
        if idx is None:
            return
        # 버튼 항목은 해당 동작 수행
        item = lv.highlighted_child
        if item is not None and item.id == "ci-load-more":
            await self._do_load_more()
            return
        if item is not None and item.id == "ci-load-all":
            await self._do_load_all()
            return
        self._selection = update_selection_for_index(self._selection, idx)
        self._refresh_all_labels()
        self._update_range_session_banner()

    async def action_confirm(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        item = lv.highlighted_child
        if item is not None:
            if item.id == "ci-load-more":
                await self._do_load_more()
                return
            if item.id == "ci-load-all":
                await self._do_load_all()
                return
        self.dismiss((self._selection, self._commits))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_jump_top(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        if self._commits:
            lv.index = 0
            self._update_detail(0)

    def action_jump_bottom(self) -> None:
        lv = self.query_one("#commit-list", ListView)
        last = len(self._commits) - 1
        if last >= 0:
            lv.index = last
            self._update_detail(last)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        item_id = event.item.id or ""
        if item_id.startswith("ci-"):
            try:
                idx = int(item_id[3:])
                self._update_detail(idx)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Load more / Load all
    # ------------------------------------------------------------------

    async def _do_load_more(self) -> None:
        new_limit = self._limit + DEFAULT_COMMIT_LIST_LIMIT
        await self._fetch_and_refresh(new_limit)

    async def _do_load_all(self) -> None:
        await self._fetch_and_refresh(self._total_count or 99999)

    async def _fetch_and_refresh(self, new_limit: int) -> None:
        try:
            snapshot = get_commit_list_snapshot(
                limit=new_limit,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or None,
                refresh_remote=False,
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            return
        new_commits = snapshot.get("commits", [])
        if not new_commits:
            return
        prev_count = len(self._commits)
        self._commits = new_commits
        self._limit = len(new_commits)
        self._has_more = snapshot.get("has_more_commits", False)
        self._total_count = snapshot.get("total_commit_count", self._limit)
        # 세션 캐시 갱신 (새 커밋 추가분만)
        for commit in self._commits:
            sha = commit.get("sha", "")
            if sha and sha not in self._session_cache:
                self._session_cache[sha] = self._load_session_marker(sha)
        self._range_sessions = self._load_all_range_sessions()
        await self._rebuild_list(prev_count)

    async def _rebuild_list(self, prev_count: int) -> None:
        """기존 커밋 레이블 갱신 + 새 커밋 항목 append + 버튼 재배치."""
        lv = self.query_one("#commit-list", ListView)

        # 기존 버튼 제거 (await로 DOM 반영 보장)
        for btn_id in ("ci-load-more", "ci-load-all"):
            try:
                await lv.query_one(f"#{btn_id}").remove()
            except Exception:
                pass

        # 기존 커밋 항목 레이블 갱신
        self._refresh_all_labels()

        # 새로 추가된 커밋 항목만 append
        for i in range(prev_count, len(self._commits)):
            await lv.append(ListItem(Label(self._commit_label_text(i)), id=f"ci-{i}"))

        # 버튼 재추가
        if self._has_more:
            await lv.append(ListItem(Label(self._load_more_label()), id="ci-load-more"))
            await lv.append(ListItem(Label(self._load_all_label()), id="ci-load-all"))

        self._update_range_session_banner()

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

        # Range session badges: [1][2] etc.
        for r_idx, r in enumerate(self._range_sessions):
            if index in r["indices"]:
                marker_style = r["marker"][1]
                line.append(f" [{r_idx + 1}]", style=marker_style)

        return line

    def _load_more_label(self) -> Text:
        t = Text(" + ", style="bold cyan")
        t.append(f"커밋 더 불러오기 (+{DEFAULT_COMMIT_LIST_LIMIT})", style="bold")
        return t

    def _load_all_label(self) -> Text:
        t = Text(" + ", style="bold cyan")
        t.append("커밋 전체 불러오기", style="bold")
        return t

    def _load_all_range_sessions(self) -> list[dict]:
        """Scan all saved sessions and return multi-commit range sessions with index info."""
        session_dir = get_session_repo_dir(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            local_repo_root=self._local_repo_root,
        )
        if not session_dir.exists():
            return []

        # Build lookup maps
        sha_to_index: dict[str, int] = {}
        sha7_to_full: dict[str, str] = {}
        for i, c in enumerate(self._commits):
            sha = c.get("sha", "")
            if sha:
                sha_to_index[sha] = i
                sha7_to_full[sha[:7]] = sha

        sessions = []
        for session_path in sorted(session_dir.glob("*/session.json")):
            session_id = session_path.parent.name
            parts = session_id.split("-")
            if len(parts) != 2:
                continue
            oldest_sha7, newest_sha7 = parts
            # Validate 7-char hex
            if len(oldest_sha7) != 7 or len(newest_sha7) != 7:
                continue
            # Skip single-commit sessions (already shown via individual marker)
            if oldest_sha7 == newest_sha7:
                continue

            oldest_sha = sha7_to_full.get(oldest_sha7, "")
            newest_sha = sha7_to_full.get(newest_sha7, "")
            if not oldest_sha or not newest_sha:
                continue

            oldest_idx = sha_to_index.get(oldest_sha)
            newest_idx = sha_to_index.get(newest_sha)
            if oldest_idx is None or newest_idx is None:
                continue

            start = min(oldest_idx, newest_idx)
            end = max(oldest_idx, newest_idx)

            try:
                payload = json.loads(session_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue

            grades = payload.get("grades", [])
            answers = payload.get("answers", {})
            questions = payload.get("questions", [])
            if grades:
                marker: tuple[str, str] = ("★", "yellow")
            elif answers:
                marker = ("●", "green")
            elif questions:
                marker = ("○", "cyan")
            else:
                marker = ("·", "grey62")

            sessions.append({
                "session_id": session_id,
                "start": start,
                "end": end,
                "indices": set(range(start, end + 1)),
                "marker": marker,
            })

        sessions.sort(key=lambda r: r["start"])
        return sessions

    def _load_session_marker(self, sha: str) -> tuple[str, str] | None:
        """Load session file and return marker for single-commit session."""
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
        answers = payload.get("answers", {})
        questions = payload.get("questions", [])
        if grades:
            return ("★", "yellow")
        if answers:
            return ("●", "green")
        if questions:
            return ("○", "cyan")
        return ("·", "grey62")

    def _session_marker(self, sha: str) -> tuple[str, str] | None:
        """Return cached session marker for this commit SHA."""
        return self._session_cache.get(sha)

    def _update_range_session_banner(self) -> None:
        """Show a banner if a session exists for the current S..E range selection."""
        status = self.query_one("#status-bar", Static)
        base = self._build_status()

        s = self._selection.start_index
        e = self._selection.end_index
        if s is None or e is None or s == e:
            status.update(base)
            return

        indices = sorted([s, e])
        newest_sha = self._commits[indices[0]].get("sha", "") if indices[0] < len(self._commits) else ""
        oldest_sha = self._commits[indices[1]].get("sha", "") if indices[1] < len(self._commits) else ""
        if not oldest_sha or not newest_sha:
            status.update(base)
            return

        target_id = f"{oldest_sha[:7]}-{newest_sha[:7]}"
        for r in self._range_sessions:
            if r["session_id"] == target_id:
                marker_text, _ = r["marker"]
                if marker_text == "★":
                    status.update(f"{base}  ★ 이 범위의 세션이 있습니다 (채점완료)")
                elif marker_text == "●":
                    status.update(f"{base}  ● 이 범위의 세션이 있습니다 (진행중)")
                elif marker_text == "○":
                    status.update(f"{base}  ○ 이 범위의 세션이 있습니다 (퀴즈있음)")
                return

        status.update(base)

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
        lines.append("SHA:     ", style="dim")
        lines.append(f"{sha[:40]}\n", style="cyan")
        lines.append("Subject: ", style="dim")
        lines.append(f"{subject}\n")
        lines.append("Author:  ", style="dim")
        lines.append(f"{author}\n")
        lines.append("Date:    ", style="dim")
        lines.append(f"{date}\n")

        # Range sessions that include this commit
        commit_ranges = [
            (r_idx, r)
            for r_idx, r in enumerate(self._range_sessions)
            if index in r["indices"]
        ]
        if commit_ranges:
            lines.append("\n범위 세션:", style="dim")
            for r_idx, r in commit_ranges:
                marker_text, marker_style = r["marker"]
                if marker_text == "★":
                    status_label = "채점 완료"
                elif marker_text == "●":
                    status_label = "진행 중"
                elif marker_text == "○":
                    status_label = "퀴즈 있음"
                else:
                    status_label = "세션 있음"
                lines.append(f"\n  [{r_idx + 1}] ", style="dim")
                lines.append(r["session_id"], style="cyan")
                lines.append(f"  {marker_text} {status_label}", style=marker_style)

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

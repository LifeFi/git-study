"""RepoPickerScreen — modal for switching repositories."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static

from ...domain.repo_cache import (
    get_remote_repo_cache_dir,
    list_remote_repo_caches,
    remove_remote_repo_cache,
)
from ...runtime_paths import get_global_runtime_dir


def _get_recent_repos_path() -> Path:
    return get_global_runtime_dir() / "recent_repos.json"


def load_recent_local_repos() -> list[dict]:
    path = _get_recent_repos_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_recent_local_repo(path_str: str) -> None:
    """Add or update a local repo path in the recent list (max 10)."""
    resolved = str(Path(path_str).resolve())
    repos = load_recent_local_repos()
    repos = [r for r in repos if r.get("path") != resolved]
    repos.insert(0, {"path": resolved, "last_used": datetime.now().timestamp()})
    repos = repos[:10]
    runtime_dir = get_global_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _get_recent_repos_path().write_text(
        json.dumps(repos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _format_time_ago(ts: float) -> str:
    delta = datetime.now().timestamp() - ts
    if delta < 60:
        return "방금 전"
    if delta < 3600:
        return f"{int(delta // 60)}분 전"
    if delta < 86400:
        return f"{int(delta // 3600)}시간 전"
    return f"{int(delta // 86400)}일 전"


class RepoPickerScreen(ModalScreen[tuple[str, str] | None]):
    """Modal for switching to another repository.

    Returns ``("github", url)`` | ``("local", path)`` | ``None`` on cancel.
    Press ``d`` to delete a cached GitHub repo.
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("d", "delete_cache", "캐시 삭제"),
    ]

    CSS = """
    RepoPickerScreen {
        align: center middle;
    }

    #repo-container {
        width: 85%;
        height: auto;
        max-height: 36;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }

    #repo-title {
        text-style: bold;
        padding: 1 0 0 0;
    }

    #repo-help {
        padding-bottom: 1;
    }

    #repo-cache-path {
        color: $text-muted;
        padding-bottom: 1;
    }

    #repo-list {
        height: auto;
        max-height: 14;
    }

    #repo-input-row {
        height: 1;
        padding-top: 1;
    }

    #repo-prompt {
        width: auto;
        height: 1;
        color: $text-muted;
        padding: 0 1 0 0;
    }

    #repo-input {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }

    #repo-status {
        height: 1;
        color: $text-muted;
        padding: 1 0;
    }
    """

    def __init__(
        self,
        current_local_root: Path | None = None,
        current_repo_source: str = "local",
        current_github_url: str | None = None,
    ) -> None:
        super().__init__()
        self._current_local_root = current_local_root
        self._current_repo_source = current_repo_source
        self._current_github_url = current_github_url
        self._entries: list[dict] = []

    def compose(self) -> ComposeResult:
        cache_path = str(get_remote_repo_cache_dir())
        with Vertical(id="repo-container"):
            yield Static("저장소 선택", id="repo-title")
            yield Static("", id="repo-help")
            yield Static(f"GitHub 캐시: {cache_path}", id="repo-cache-path")
            with ListView(id="repo-list"):
                pass
            with Horizontal(id="repo-input-row"):
                yield Static("> ", id="repo-prompt")
                yield Input(
                    placeholder="새 URL 또는 경로 직접 입력...",
                    id="repo-input",
                )
            yield Static("", id="repo-status")

    def on_mount(self) -> None:
        self._build_entries()
        self._populate_list()
        lv = self.query_one("#repo-list", ListView)
        if self._entries:
            lv.focus()
        else:
            self.query_one("#repo-input", Input).focus()
        self._update_help_text(0)

    def _update_help_text(self, idx: int | None) -> None:
        """현재 포커스된 항목이 github인지 여부에 따라 도움말 텍스트 업데이트."""
        is_github = (
            idx is not None
            and idx < len(self._entries)
            and self._entries[idx]["source"] == "github"
            and self._entries[idx].get("slug")
        )
        t = Text()
        t.append("Enter", style="bold")
        t.append(": 선택  |  ", style="dim")
        if is_github:
            t.append("d", style="bold")
            t.append(": 캐시 삭제  |  ", style="dim")
        else:
            t.append("d: 캐시 삭제  |  ", style="dim grey50")
        t.append("Esc", style="bold")
        t.append(": 취소", style="dim")
        try:
            self.query_one("#repo-help", Static).update(t)
        except Exception:
            pass

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = self.query_one("#repo-list", ListView)
        self._update_help_text(lv.index)

    # ------------------------------------------------------------------
    # Entry loading
    # ------------------------------------------------------------------

    def _is_current(self, source: str, value: str) -> bool:
        """True if this entry matches the currently active repo."""
        if source != self._current_repo_source:
            return False
        if source == "local":
            return self._current_local_root is not None and (
                str(self._current_local_root) == value
                or str(self._current_local_root.resolve()) == str(Path(value).resolve())
            )
        if source == "github":
            return bool(self._current_github_url) and (
                self._current_github_url == value
                or self._current_github_url.rstrip("/") == value.rstrip("/")
            )
        return False

    def _build_entries(self) -> None:
        entries: list[dict] = []
        seen_local: set[str] = set()

        # 현재 로컬 repo를 맨 위에 항상 추가
        if self._current_local_root:
            current_path = str(self._current_local_root)
            seen_local.add(current_path)
            entries.append({
                "source": "local",
                "display": current_path,
                "value": current_path,
                "slug": None,
                "time_ago": "",
            })

        # 최근 사용한 로컬 repo (중복 제외)
        for r in load_recent_local_repos():
            path_str = r.get("path", "")
            if path_str and path_str not in seen_local:
                seen_local.add(path_str)
                ts = r.get("last_used", 0.0)
                entries.append({
                    "source": "local",
                    "display": path_str,
                    "value": path_str,
                    "slug": None,
                    "time_ago": _format_time_ago(float(ts)) if ts else "",
                })

        # 로컬 항목이 전혀 없으면 플레이스홀더 추가
        if not seen_local:
            entries.append({
                "source": "local",
                "display": "(로컬 경로 없음 — 아래 입력란에 경로 입력)",
                "value": "",
                "slug": None,
                "time_ago": "",
            })

        # 캐시된 GitHub repo
        for cache in list_remote_repo_caches():
            url = cache.get("repo_url", "")
            if url:
                entries.append({
                    "source": "github",
                    "display": url.replace("https://", "").replace(".git", ""),
                    "value": url,
                    "slug": cache.get("slug", ""),
                    "time_ago": cache.get("last_used_label", ""),
                })

        self._entries = entries

    def _populate_list(self) -> None:
        lv = self.query_one("#repo-list", ListView)
        for entry in self._entries:
            is_cur = self._is_current(entry["source"], entry["value"])
            t = Text()
            if is_cur:
                t.append("★ ", style="bold yellow")
            else:
                t.append("  ")
            if entry["source"] == "local":
                t.append("[local]  ", style="dim")
            else:
                t.append("[github] ", style="cyan")
            t.append(entry["display"])
            if entry["time_ago"]:
                t.append(f"  {entry['time_ago']}", style="dim")
            lv.mount(ListItem(Label(t)))

    # ------------------------------------------------------------------
    # Actions & events
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        if not entry["value"]:
            # 플레이스홀더 항목: 입력란으로 포커스
            self.query_one("#repo-input", Input).focus()
            return
        self.dismiss((entry["source"], entry["value"]))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "repo-input":
            self._resolve_and_dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_delete_cache(self) -> None:
        lv = self.query_one("#repo-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        if entry["source"] != "github" or not entry.get("slug"):
            self.query_one("#repo-status", Static).update(
                "로컬 저장소는 캐시를 삭제할 수 없습니다."
            )
            return
        slug = entry["slug"]
        removed = remove_remote_repo_cache(slug)
        if removed:
            self._entries.pop(idx)
            for child in list(lv.children):
                child.remove()
            self._populate_list()
            self.query_one("#repo-status", Static).update(
                f"캐시 삭제됨: {entry['display']}"
            )
        else:
            self.query_one("#repo-status", Static).update("삭제 실패.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_and_dismiss(self, value: str) -> None:
        if not value:
            self.query_one("#repo-status", Static).update(
                "URL 또는 경로를 입력하세요."
            )
            return
        if value.startswith("http") or value.startswith("github.com"):
            self.dismiss(("github", value))
        else:
            self.dismiss(("local", value))

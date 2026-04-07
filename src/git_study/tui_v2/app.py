"""GitStudyAppV2: inline-quiz-first TUI with command bar."""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path

from textual import work
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from ..domain.code_context import (
    get_commit_parent_sha,
    build_full_file_map,
    build_range_full_file_map,
)
from ..domain.inline_anchor import parse_file_context_blocks
from ..domain.repo_cache import normalize_github_repo_url
from ..domain.repo_context import (
    DEFAULT_COMMIT_LIST_LIMIT,
    build_commit_context,
    build_multi_commit_context,
    get_commit_list_snapshot,
    get_repo,
)
from ..secrets import (
    delete_openai_api_key,
    get_openai_api_key,
    get_secrets_path,
    load_file_openai_api_key,
    save_openai_api_key,
)
from ..services.inline_grade_service import stream_inline_grade_progress
from ..services.inline_quiz_service import stream_inline_quiz_progress
from ..domain.repo_map import build_tree_summary, get_commit_counts, get_file_tree
from ..services.map_service import stream_full_map_progress, stream_map_progress
from ..services.read_service import stream_read_progress
from ..tui.commit_selection import CommitSelection, selected_commit_indices
from ..tui.state import (
    append_thread_event,
    find_local_repo_root,
    list_learning_sessions,
    load_app_state,
    load_chat_threads,
    delete_learning_session_file,
    load_learning_session_file,
    load_map_cache,
    load_thread_log,
    save_app_state,
    save_chat_threads,
    save_learning_session_file,
    save_map_cache,
)
from ..services.chat_service import stream_chat
from ..types import InlineQuizGrade, InlineQuizQuestion
from ..update_checker import format_as_box, format_as_toast, get_update_messages

from .commands import parse_command
from .screens import CommitPickerScreen, QuizListScreen, RepoPickerScreen, ThreadPickerScreen
from .screens.repo_picker import save_recent_local_repo
from .widgets.app_status_bar import AppStatusBar
from .widgets.command_bar import CommandBar
from .widgets.history_view import FullLogoAnimated, HistoryView, LoadingRow
from .widgets.inline_code_view import InlineCodeView, InlineQuizBlock



class GitStudyAppV2(App):
    """Inline-quiz TUI for git-study."""

    TITLE = "git-study v2"

    CSS = """
    Screen {
        layout: vertical;
        layers: base overlay;
    }

    #scroll-wrapper {
        layer: base;
        width: 1fr;
        height: 1fr;
    }

    #scroll-hint {
        layer: overlay;
        dock: bottom;
        height: 1;
        content-align: center middle;
        background: #e5b800;
        color: #1a1a00;
        text-style: bold;
        display: none;
    }

    #scroll-hint:hover {
        background: #ffd000;
        color: #000000;
    }

    #history-view {
        width: 1fr;
        height: auto;
    }

    #code-container {
        display: none;
        width: 1fr;
        height: 1fr;
    }


    /* ── Code mode ── */
    Screen.-code-active #history-view {
        display: none;
    }

    Screen.-code-active #code-container {
        display: block;
    }

    #cb-ac-list {
        height: auto;
        padding: 0 0;
    }

    
    /* ── cmd-bar: height auto — AppStatusBar 숨김 시 자동 수축 ── */
    #cmd-bar {
        height: auto;
    }


    #mode-bar {
        height: 3;
        padding: 1 1 1 1;
        color: $text-muted;
    }

    /* ── content spacer: autocomplete(7) - mode-bar(3) - app-status(2) = 2 ── */
    #content-spacer {
        height: 2;
    }


    /* ── Autocomplete panel: appears below cmd-bar ── */
    #cb-autocomplete {
        height: 8;
        display: none;
        background: transparent;
    }

    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        Binding("tab", "global_tab", priority=True),
        Binding("shift+tab", "toggle_view", "Chat/Code", priority=True),
        Binding("shift+up", "prev_question", priority=True),
        Binding("shift+down", "next_question", priority=True),
        Binding("f1", "quiz_hint", priority=True),
        Binding("pageup", "chat_scroll_page_up", priority=True),
        Binding("pagedown", "chat_scroll_page_down", priority=True),
        Binding("escape", "escape_to_cmdbar"),  # 위젯 ESC보다 낮은 우선순위
    ]

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    _commits: reactive[list[dict]] = reactive(list, init=False)
    _repo_source: str = "local"
    _github_repo_url: str | None = None
    _local_repo_root: Path | None = None
    _original_local_root: Path | None = (
        None  # startup시 결정된 로컬 경로 (github 전환 후에도 보존)
    )

    def __init__(
        self, repo_path: Path | None = None, auto_quiz_arg: str | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._repo_path: Path | None = repo_path  # explicit path override
        self._auto_quiz_arg: str | None = auto_quiz_arg
        self._questions: list[InlineQuizQuestion] = []
        self._answers: dict[str, str] = {}
        self._grades: list[InlineQuizGrade] = []
        self._grading_summary: dict = {}
        self._known_files: dict[str, str] = {}
        self._current_q_index: int = 0
        self._quiz_retried: bool = False
        self._graded_this_session: bool = False
        self._map_or_review_done: bool = False
        self._mode: str = (
            "idle"  # idle | quiz_loading | quiz_answering | grading | reviewing | chatting
        )
        self._oldest_sha: str = ""
        self._newest_sha: str = ""
        # CommitSelection tracks which indices are selected in the picker
        self._commit_selection: CommitSelection = CommitSelection()
        # 커밋 목록 로드 한도 (picker에서 더 불러올 때마다 갱신됨)
        self._commit_list_limit: int = DEFAULT_COMMIT_LIST_LIMIT
        # 마지막으로 로드한 가장 오래된 커밋 SHA (재오픈 시 해당 커밋까지 포함되도록 보장)
        self._commit_list_oldest_sha: str = ""
        # Current history block (Vertical) for attaching results to last command
        self._current_log_block = None
        # Per-op progress rows: op_id → LoadingRow
        self._progress_rows: dict[str, LoadingRow] = {}
        self._progress_elapsed: dict[str, int] = {}
        # Chat mode state
        self._current_thread_id: str = ""
        # 채팅용 커밋 컨텍스트 캐시 (SHA 범위가 같으면 재사용)
        self._chat_ctx_cache_key: tuple[str, str] = ("", "")
        self._chat_ctx_cache_str: str = ""
        # 액션 큐
        self._action_queue: list = []  # list of ParsedCommand
        self._action_queue_block_id: str | None = None
        self._update_messages: list[tuple[str, str]] = []
        self._stop_event: threading.Event = threading.Event()
        self._exit_printed: threading.Event = threading.Event()

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll-wrapper"):
            yield HistoryView(id="history-view")
            with Horizontal(id="code-container"):
                yield InlineCodeView(id="code-view")
            yield CommandBar(id="cmd-bar")
            with VerticalScroll(id="cb-autocomplete"):
                yield Static("", id="cb-ac-list")
            yield Static(self._mode_bar_text(), id="mode-bar")
            yield Static("", id="content-spacer")
        yield Static("▼  Tab → cmd bar  ▼", id="scroll-hint")

    def on_mount(self) -> None:
        # Prevent scroll containers from stealing focus
        self.query_one("#scroll-wrapper").can_focus = False
        self.query_one("#cb-autocomplete").can_focus = False

        # virtual_size 변화 시 scroll_end 호출.
        self.watch(
            self.query_one("#scroll-wrapper"),
            "virtual_size",
            self._on_scroll_wrapper_virtual_size_change,
        )
        # scroll_y 변화 시 scroll-hint 표시/숨김.
        self.watch(
            self.query_one("#scroll-wrapper"),
            "scroll_y",
            self._on_scroll_wrapper_scroll_y_change,
        )

        # 로드 전에 미리 채울 수 있는 정보 표시
        try:
            status_bar = self.query_one("#app-status", AppStatusBar)
            early_name = self._repo_path.name if self._repo_path else Path.cwd().name
            status_bar.set_repo(early_name)
        except Exception:
            pass
        self._load_local_repo()
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        cmd_bar.focus_input()
        api_key, _ = get_openai_api_key()
        if not api_key:
            cmd_bar.set_warning_alert("API키 미설정", "/apikey set KEY 로 등록")
        self._sync_logo_animation()
        self._check_for_updates()

    @work(thread=True)
    def _check_for_updates(self) -> None:
        """백그라운드에서 업데이트 버전 체크 후 토스트 알림 표시 및 종료 메시지에 추가."""
        updates = get_update_messages()
        if updates:
            self._update_messages = updates
            for title, command in updates:
                self.call_from_thread(
                    self.notify, format_as_toast(title, command), severity="warning", timeout=15
                )

    def _build_exit_message(self) -> str:
        """종료 메시지 + 업데이트 안내를 합쳐 반환."""
        if self._quiz_retried:
            phrase = "이해가 단단해졌습니다."
        elif self._graded_this_session and self._map_or_review_done:
            phrase = "이해가 넓고 깊어졌습니다."
        elif self._graded_this_session:
            phrase = "이해가 깊어졌습니다."
        else:
            phrase = "FOMO가 완화되었습니다."
        msg = f"\n[brain/main] HEAD~1 보다 {phrase}\n"
        if self._update_messages:
            boxed = "\n".join(
                format_as_box(title, command) for title, command in self._update_messages
            )
            msg += "\n" + boxed + "\n"
        return msg

    def _on_scroll_wrapper_virtual_size_change(self) -> None:
        """콘텐츠(HistoryView)가 커질 때 scroll-wrapper를 맨 아래로 스크롤."""
        try:
            self.query_one("#scroll-wrapper").scroll_end(animate=False)
        except Exception:
            pass

    def _on_scroll_wrapper_scroll_y_change(self, scroll_y: int) -> None:
        """scroll_y 변화 시 cmd-bar 미노출이면 scroll-hint를 표시한다."""
        try:
            sw = self.query_one("#scroll-wrapper")
            hint = self.query_one("#scroll-hint")
            hint.display = scroll_y < sw.max_scroll_y - 5
        except Exception:
            pass

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "scroll-hint":
            self.handle_tab_no_autocomplete()

    def on_focus(self, event) -> None:
        """Redirect focus to command bar if it lands outside cmd-bar inputs."""
        focused = self.focused
        if focused is None:
            return
        if focused.id not in (
            "cb-input",
            "code-scroll",
            "file-tree",
        ) and not focused.has_class("iqb-input"):
            try:
                self.query_one("#cmd-bar", CommandBar).focus_input()
            except Exception:
                pass
            return  # on_focus fires again when cb-input gets focus
        self._update_context_hint()

    def _update_context_hint(self) -> None:
        """현재 포커스 위치와 퀴즈 상태에 따라 CommandBar 컨텍스트 힌트를 갱신한다."""
        focused = self.focused
        quiz_count = len(self._questions)
        answered_count = len(self._answers)
        is_code_view = self._is_code_view_active()

        if focused is None:
            zone = "focus_lost"
        elif focused.has_class("iqb-input") and self._mode == "quiz_answering":
            # 퀴즈 답변창: 기존 _update_answer_status()가 더 상세한 힌트를 제공
            self._update_answer_status()
            self._sync_quiz_alert()
            return
        elif getattr(focused, "id", None) == "file-tree":
            zone = "left_panel"
        elif getattr(focused, "id", None) == "code-scroll":
            zone = "right_panel"
        elif getattr(focused, "id", None) == "cb-input":
            zone = "command_bar_code" if is_code_view else "command_bar_chat"
        else:
            zone = "focus_lost"

        try:
            self.query_one("#cmd-bar", CommandBar).update_context_hint(
                zone=zone,
                quiz_count=quiz_count,
                answered_count=answered_count,
                graded=bool(self._grades),
            )
        except Exception:
            pass
        self._sync_quiz_alert()

    # ------------------------------------------------------------------
    # Initial load
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_local_repo(self) -> None:
        root = find_local_repo_root(start=self._repo_path)
        if root is None:
            self.call_from_thread(
                self._set_status_timed, "Git 저장소를 찾을 수 없습니다.", 5.0
            )
            self.call_from_thread(self._log, "Git 저장소를 찾을 수 없습니다.", "error")
            return
        self._local_repo_root = root
        self._original_local_root = root  # startup 경로 고정 (github 전환 후에도 유지)

        # root 확정 직후 repo 이름 + hook 상태 즉시 표시 (커밋 로드 전)
        hook_path = root / ".git" / "hooks" / "post-commit"
        early_hook = hook_path.exists() and _has_hook(hook_path.read_text())
        _root_name = root.name

        def _early_status_update() -> None:
            try:
                sb = self.query_one("#app-status", AppStatusBar)
                sb.set_repo(_root_name)
                sb.set_hook(early_hook)
            except Exception:
                pass

        self.call_from_thread(_early_status_update)

        # CLI 인자 없을 때: 이전 github 세션이 있으면 복원
        if self._repo_path is None:
            try:
                global_state = load_app_state(repo_source="github")
                if global_state.get("repo_source") == "github" and global_state.get(
                    "github_repo_url"
                ):
                    github_url = global_state["github_repo_url"]
                    self._repo_source = "github"
                    self._github_repo_url = github_url
                    self._local_repo_root = None
                    self.call_from_thread(
                        self._set_status, f"이전 GitHub 세션 복원 중: {github_url}"
                    )
                    try:
                        snapshot = get_commit_list_snapshot(
                            repo_source="github",
                            github_repo_url=github_url,
                            refresh_remote=False,
                        )
                    except Exception as exc:
                        self.call_from_thread(
                            self._set_status,
                            f"GitHub 복원 실패 ({exc}), 로컬로 전환합니다.",
                        )
                        self._repo_source = "local"
                        self._github_repo_url = None
                        self._local_repo_root = root
                    else:
                        commits = snapshot.get("commits", [])
                        self.call_from_thread(
                            self._apply_commits, commits, global_state
                        )
                        return
            except Exception:
                pass

        try:
            snapshot = get_commit_list_snapshot(
                repo_source="local",
                refresh_remote=False,
                local_repo_root=root,
            )
        except Exception as exc:
            self.call_from_thread(self._set_status_timed, f"커밋 로드 실패: {exc}", 5.0)
            self.call_from_thread(
                self._append_result, f"커밋 로드 실패: {exc}", "error"
            )
            return

        commits = snapshot.get("commits", [])

        # Phase 3: load persisted state for SHA range restore
        try:
            saved_state = load_app_state(
                repo_source="local",
                local_repo_root=root,
            )
        except Exception:
            saved_state = {}

        self.call_from_thread(self._apply_commits, commits, saved_state)

    def _apply_commits(self, commits: list[dict], saved_state: dict) -> None:
        self._commits = commits
        if not commits:
            self._set_status_timed("커밋이 없습니다.", 5.0)
            self._log("커밋이 없습니다.", "error")
            return
        prev_oldest = self._oldest_sha
        prev_newest = self._newest_sha

        # Phase 3: restore saved SHA range, fall back to HEAD
        saved_oldest = saved_state.get("selected_range_start_sha", "")
        saved_newest = saved_state.get("selected_range_end_sha", "")

        sha_index = {c.get("sha", ""): i for i, c in enumerate(commits)}
        has_saved_range = (
            saved_oldest
            and saved_newest
            and saved_oldest in sha_index
            and saved_newest in sha_index
        )
        if has_saved_range:
            self._oldest_sha = saved_oldest
            self._newest_sha = saved_newest
            newest_idx = sha_index[saved_newest]
            oldest_idx = sha_index[saved_oldest]
            if newest_idx == oldest_idx:
                self._commit_selection = CommitSelection(start_index=newest_idx)
            else:
                self._commit_selection = CommitSelection(
                    start_index=newest_idx, end_index=oldest_idx
                )
        else:
            self._oldest_sha = commits[0]["sha"]
            self._newest_sha = commits[0]["sha"]

        # Load code view in background (ready for Shift+Tab toggle)
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=self._oldest_sha,
            newest_commit_sha=self._newest_sha,
            local_repo_root=self._local_repo_root,
        )

        # Update AppStatusBar repo name and initial range
        try:
            status_bar = self.query_one("#app-status", AppStatusBar)
            status_bar.set_repo(self._repo_display_name())
            if self._oldest_sha and self._newest_sha:
                sha_index = {c.get("sha", ""): i for i, c in enumerate(commits)}
                o_idx = sha_index.get(self._oldest_sha, 0)
                n_idx = sha_index.get(self._newest_sha, 0)
                count = abs(o_idx - n_idx) + 1
                status_bar.set_range(self._oldest_sha, self._newest_sha, count)
        except Exception:
            pass

        # 커밋 범위가 바뀐 경우 구분선 삽입 (챗은 유지)
        if prev_oldest and (
            prev_oldest != self._oldest_sha or prev_newest != self._newest_sha
        ):
            try:
                hv = self.query_one("#history-view", HistoryView)
                label = f"{self._oldest_sha[:7]}..{self._newest_sha[:7]}"
                hv.append_separator(f"─── 커밋 범위 변경: {label} ───")
            except Exception:
                pass

        # 현재 repo_source + URL/경로를 state에 저장 (재시작 시 복원용)
        self._save_app_state()

        # Phase 3.5: --auto-quiz이면 Phase 4 전에 HEAD SHA로 범위 override
        # _resolve_range()는 UI 스레드에서 git I/O를 수행하므로 호출하지 않고
        # 이미 로드된 commits 리스트에서 직접 SHA를 추출한다
        if self._auto_quiz_arg is not None and commits:
            arg = self._auto_quiz_arg.strip().upper()
            sha_index_tmp = {c.get("sha", ""): i for i, c in enumerate(commits)}
            if arg == "HEAD":
                auto_newest = auto_oldest = commits[0]["sha"]
            elif arg.startswith("HEAD~"):
                try:
                    n = int(self._auto_quiz_arg.strip()[5:])
                    auto_newest = commits[0]["sha"]
                    auto_oldest = commits[min(n, len(commits) - 1)]["sha"]
                except (ValueError, IndexError):
                    auto_newest = auto_oldest = commits[0]["sha"]
            else:
                auto_newest = auto_oldest = (
                    None  # SHA 직접 지정 등은 _start_quiz에서 처리
                )

            if auto_oldest and auto_newest:
                self._oldest_sha = auto_oldest
                self._newest_sha = auto_newest
                code_view.show_range(
                    repo_source=self._repo_source,
                    github_repo_url=self._github_repo_url,
                    oldest_commit_sha=auto_oldest,
                    newest_commit_sha=auto_newest,
                    local_repo_root=self._local_repo_root,
                )
                o_idx = sha_index_tmp.get(auto_oldest, 0)
                n_idx = sha_index_tmp.get(auto_newest, 0)
                count = abs(o_idx - n_idx) + 1
                try:
                    status_bar = self.query_one("#app-status", AppStatusBar)
                    status_bar.set_range(auto_oldest, auto_newest, count)
                except Exception:
                    pass
                self._sync_commit_selection(auto_oldest, auto_newest)

        # Phase 4: restore quiz session if exists for this range
        session_restored = self._try_restore_session()
        self._update_hook_status()
        if not session_restored:
            hook_installed = (
                self._local_repo_root is not None
                and (self._local_repo_root / ".git" / "hooks" / "post-commit").exists()
                and _has_hook(
                    (
                        self._local_repo_root / ".git" / "hooks" / "post-commit"
                    ).read_text()
                )
            )
            hook_hint = (
                "  ⚓ hook 등록됨."
                if hook_installed
                else "  /hook on 으로 커밋 후 자동 퀴즈 설정."
            )
            self._set_status(f"저장소 로드 완료 ({len(commits)} commits).{hook_hint}")
            # show_range → _populate_tree가 위젯 마운트로 포커스를 steal했을 수 있으므로 복구
            try:
                self.call_after_refresh(
                    self.query_one("#cmd-bar", CommandBar).focus_input
                )
            except Exception:
                pass

        # Phase 5: auto-quiz이면 HEAD 세션이 없을 때만 새 quiz 생성
        if self._auto_quiz_arg is not None and not session_restored:
            _range = self._auto_quiz_arg

            def _auto_quiz() -> None:
                self._log_command(f"/quiz {_range}")
                log_block_id = (
                    self._current_log_block.id if self._current_log_block else None
                )
                self._start_quiz(_range, log_block_id=log_block_id)

            self.call_after_refresh(_auto_quiz)

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    def on_command_bar_command_submitted(
        self, event: CommandBar.CommandSubmitted
    ) -> None:
        cmd = parse_command(event.text)
        # chat 메시지는 command 스타일로 로깅하지 않음 (append_user_message로 처리)
        if cmd.kind != "chat":
            self._log_command(event.text)
        if cmd.model_override and not self._validate_model_override(cmd.model_override):
            return

        match cmd.kind:
            case "quiz":
                if self._mode == "quiz_loading":
                    self._set_status_timed("퀴즈 생성이 이미 진행 중입니다.", 3.0)
                    self._append_result("퀴즈 생성이 이미 진행 중입니다.", "error")
                    return
                if self._mode == "grading":
                    self._set_status_timed("채점이 진행 중입니다. 완료 후 시도하세요.", 3.0)
                    self._append_result("채점이 진행 중입니다. 완료 후 시도하세요.", "error")
                    return
                log_block_id = (
                    self._current_log_block.id if self._current_log_block else None
                )
                # 범위가 명시된 경우 메인 스레드에서 즉시 SHA 업데이트 (race condition 방지)
                if cmd.range_arg:
                    self._pre_apply_range(cmd.range_arg)
                self._start_quiz(
                    cmd.range_arg,
                    log_block_id=log_block_id,
                    count=cmd.quiz_count,
                    author_context=cmd.author_context,
                    model_override=cmd.model_override,
                )
            case "review":
                log_block_id = (
                    self._current_log_block.id if self._current_log_block else None
                )
                # 범위가 명시된 경우 메인 스레드에서 즉시 SHA 업데이트 (race condition 방지)
                if cmd.range_arg:
                    self._pre_apply_range(cmd.range_arg)
                self._start_review(
                    cmd.range_arg,
                    log_block_id=log_block_id,
                    model_override=cmd.model_override,
                )
            case "map":
                log_block_id = (
                    self._current_log_block.id if self._current_log_block else None
                )
                self._start_map(
                    cmd.range_arg,
                    refresh=cmd.refresh,
                    log_block_id=log_block_id,
                    model_override=cmd.model_override,
                )
            case "quiz_list":
                self._open_quiz_list()
            case "quiz_clear":
                self._handle_quiz_clear()
            case "quiz_retry":
                self._handle_quiz_retry()
            case "grade":
                if self._mode == "grading":
                    self._set_status_timed("채점이 이미 진행 중입니다.", 3.0)
                    self._append_result("채점이 이미 진행 중입니다.", "error")
                    return
                if self._mode == "quiz_loading":
                    self._set_status_timed("퀴즈 생성이 진행 중입니다. 완료 후 시도하세요.", 3.0)
                    self._append_result("퀴즈 생성이 진행 중입니다. 완료 후 시도하세요.", "error")
                    return
                if self._mode == "quiz_answering":
                    self._set_mode("idle")
                    try:
                        self.query_one(
                            "#code-view", InlineCodeView
                        )._refresh_quiz_blocks_state()
                    except Exception:
                        pass
                    try:
                        self.query_one("#cmd-bar", CommandBar).focus_input()
                    except Exception:
                        pass
                self._set_mode("grading")
                log_block_id = (
                    self._current_log_block.id if self._current_log_block else None
                )
                self._start_grading(
                    log_block_id=log_block_id, model_override=cmd.model_override
                )
            case "help":
                self._show_help()
            case "commits":
                self._open_commit_picker()
            case "answer":
                self._resume_answer_mode()
            case "repo":
                self._handle_repo_command(cmd.range_arg)
            case "apikey":
                self._handle_apikey_command(cmd.range_arg)
            case "model":
                self._handle_model_command(cmd.range_arg)
            case "exit":
                self._stop_event.set()
                self._schedule_force_exit()
                self.exit(self._build_exit_message())
            case "chat":
                self._start_chat(cmd.range_arg, mentioned_files=cmd.mentioned_files, mentioned_quizzes=cmd.mentioned_quizzes)
            case "clear":
                self._handle_clear()
            case "resume":
                self._handle_resume()
            case "hook":
                if cmd.range_arg == "off":
                    self._handle_uninstall_hook()
                else:
                    self._handle_install_hook(
                        cmd.range_arg if cmd.range_arg != "on" else ""
                    )
            case _:
                self._set_status_dismissable(f"알 수 없는 명령: {cmd.raw}  [dim]ESC 닫기[/dim]")
                self._append_result(f"알 수 없는 명령: {cmd.raw}", "error")

    def on_inline_quiz_block_answer_submitted(
        self, event: InlineQuizBlock.AnswerSubmitted
    ) -> None:
        if self._mode != "quiz_answering":
            return
        if self._current_q_index >= len(self._questions):
            return

        qid = self._questions[self._current_q_index].get("id", "")
        self._answers[qid] = event.answer

        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.update_answer(self._current_q_index, event.answer)
        self._save_session()

        if self._current_q_index < len(self._questions) - 1:
            self._current_q_index += 1
            code_view.activate_question(self._current_q_index)
            self._update_answer_status()
            try:
                self.query_one("#app-status", AppStatusBar).set_quiz_progress(
                    self._current_q_index + 1, len(self._questions)
                )
            except Exception:
                pass
        else:
            self._set_status_timed("모든 답변 완료. /grade 로 채점하세요.", 3.0)
            self._append_result("모든 답변 완료. /grade 로 채점하세요.", "info")
            self._set_mode("idle")
            try:
                self.query_one("#app-status", AppStatusBar).set_quiz_progress(0, 0)
            except Exception:
                pass
            self._sync_quiz_alert()

    def on_inline_quiz_block_answer_escaped(
        self, event: InlineQuizBlock.AnswerEscaped
    ) -> None:
        self._set_mode("idle")
        try:
            self.query_one("#code-view", InlineCodeView)._refresh_quiz_blocks_state()
        except Exception:
            pass
        if self._questions:
            total = len(self._questions)
            answered = len(self._answers)
            self._set_status(
                f"퀴즈 진행 중 ({answered}/{total} 답변) — 블록 클릭 또는 /answer 로 재진입"
            )
        if event.via_shift_tab:
            # Shift+Tab 경로: 뷰를 Chat으로 전환 + 명령창 포커스
            self._show_chat_view()
            try:
                self.query_one("#cmd-bar", CommandBar).focus_input()
            except Exception:
                pass
            return
        # ESC 경로: cmd-bar로 포커스 귀환
        try:
            self.query_one("#cmd-bar", CommandBar).focus_input()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Quiz block click
    # ------------------------------------------------------------------

    def on_inline_code_view_question_activated(
        self,
        event: InlineCodeView.QuestionActivated,
    ) -> None:
        if not self._questions:
            return
        self._set_mode("quiz_answering")
        self._current_q_index = event.index
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.activate_question(event.index)
        self._update_answer_status()
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(
                event.index + 1, len(self._questions)
            )
        except Exception:
            pass

    def on_inline_code_view_line_range_selected(
        self,
        event: InlineCodeView.LineRangeSelected,
    ) -> None:
        """코드뷰 p 키 → CommandBar에 @-mention 삽입 후 채팅뷰로 전환."""
        # 채팅뷰 전환을 먼저 → 레이아웃 안정 후 mention 삽입 (순서 중요)
        self._show_chat_view()
        file_path = event.file_path
        start_line = event.start_line
        end_line = event.end_line
        cmd_bar = self.query_one("#cmd-bar", CommandBar)

        def _do_insert():
            cmd_bar.insert_mention(file_path, start_line, end_line)
            cmd_bar.focus_input()

            # focus() 후 Textual이 커서를 리셋할 수 있으므로 한 번 더 고정
            def _fix_cursor():
                try:
                    from .widgets.command_bar import CommandInput

                    inp = cmd_bar.query_one("#cb-input", CommandInput)
                    inp.cursor_position = len(inp.value)
                except Exception:
                    pass

            self.call_after_refresh(_fix_cursor)

        self.call_after_refresh(_do_insert)

    def _schedule_force_exit(self) -> None:
        """asyncio executor 대기(최대 60s)를 우회하기 위해 데몬 스레드에서 강제 종료 예약.

        - 정상 종료(스트리밍 없음): app.run()이 0.8s 내 반환 → run_v2()에서 print → 프로세스 종료
          → _exit_printed가 set되므로 데몬 스레드는 print 없이 exit만 수행 (이미 죽어있음)
        - 강제 종료(스트리밍 블로킹): asyncio가 shutdown_default_executor에서 60s 대기
          → 0.8s 후 데몬 스레드가 exit message를 출력하고 os._exit(0)으로 즉시 종료
        """
        exit_msg = self._build_exit_message()

        def _force():
            time.sleep(0.8)
            if not self._exit_printed.is_set():
                if exit_msg:
                    print(exit_msg)
            os._exit(0)

        threading.Thread(target=_force, daemon=True).start()

    def action_quit(self) -> None:
        self._stop_event.set()
        self._schedule_force_exit()
        self.exit(self._build_exit_message())

    def action_escape_to_cmdbar(self) -> None:
        """ESC: 어디서든 cmd-bar로 포커스 귀환.
        iqb-input(퀴즈 답변창)과 모달은 위젯 자체 ESC가 먼저 처리하므로 여기엔 도달 안 함."""
        if len(self.screen_stack) > 1:
            return  # 모달 열려있으면 패스
        if self._mode == "quiz_answering":
            # 채점 완료 블록(code-scroll 포커스)에서 ESC → 모드 해제 + 블록 비활성화
            self._set_mode("idle")
            try:
                self.query_one(
                    "#code-view", InlineCodeView
                )._refresh_quiz_blocks_state()
            except Exception:
                pass
        try:
            self.query_one("#cmd-bar", CommandBar).focus_input()
        except Exception:
            pass

    def action_prev_question(self) -> None:
        if not self._questions:
            return
        focused = self.focused
        in_quiz = (
            focused is not None and focused.has_class("iqb-input")
        ) or self._mode == "quiz_answering"
        if in_quiz:
            self._current_q_index = (self._current_q_index - 1) % len(self._questions)
        self._resume_answer_mode()

    def action_next_question(self) -> None:
        if not self._questions:
            return
        focused = self.focused
        in_quiz = (
            focused is not None and focused.has_class("iqb-input")
        ) or self._mode == "quiz_answering"
        if in_quiz:
            self._current_q_index = (self._current_q_index + 1) % len(self._questions)
        self._resume_answer_mode()

    def action_quiz_hint(self) -> None:
        """F1: 현재 활성 퀴즈 번호를 @n 형태로 명령창에 채우고 포커스 이동."""
        if not self._questions:
            return
        n = self._current_q_index + 1
        try:
            cb = self.query_one("#cmd-bar", CommandBar)
            inp = cb.query_one("#cb-input")
            inp.value = f"@{n} "
            inp.cursor_position = len(inp.value)
            cb.focus_input()
        except Exception:
            pass

    def on_command_bar_prev_question(self, event: CommandBar.PrevQuestion) -> None:
        self.action_prev_question()

    def on_command_bar_next_question(self, event: CommandBar.NextQuestion) -> None:
        self.action_next_question()

    def _resume_answer_mode(self) -> None:
        if not self._questions:
            self._set_status_timed(
                "진행 중인 퀴즈가 없습니다. /quiz 로 퀴즈를 생성하세요.", 5.0
            )
            self._append_result(
                "진행 중인 퀴즈가 없습니다. /quiz 로 퀴즈를 생성하세요.", "error"
            )
            return
        self._set_mode("quiz_answering")
        self._show_code_view()
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(
                self._current_q_index + 1, len(self._questions)
            )
        except Exception:
            pass
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.activate_question(self._current_q_index)
        q = self._questions[self._current_q_index]
        qid = q.get("id", "")
        grade_map = {g["id"]: g for g in self._grades}
        if qid in grade_map:
            self._set_status(
                "[bold cyan]Shift+↑↓[/bold cyan] 이동  [dim]Esc 종료[/dim]"
            )
        else:
            self._update_answer_status()

    # ------------------------------------------------------------------
    # Commit picker (Phase 1 & 2)
    # ------------------------------------------------------------------

    def _open_commit_picker(self) -> None:
        log_block_id = self._current_log_block.id if self._current_log_block else None
        op_id = uuid.uuid4().hex[:8]
        self._begin_progress("커밋 목록 갱신 중...", log_block_id, op_id)
        self._refresh_and_open_picker(log_block_id, op_id)

    @work(thread=True)
    def _refresh_and_open_picker(
        self, log_block_id: str | None = None, op_id: str = ""
    ) -> None:
        try:
            snapshot = get_commit_list_snapshot(
                limit=self._commit_list_limit,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                refresh_remote=False,
                local_repo_root=self._local_repo_root,
            )
            # 이전에 로드했던 가장 오래된 커밋이 결과에 없으면 (새 커밋이 밀어낸 경우)
            # 해당 SHA가 포함될 때까지 limit을 늘려 재fetch
            oldest_sha = self._commit_list_oldest_sha
            if oldest_sha and snapshot.get("has_more_commits"):
                loaded_shas = {c["sha"] for c in snapshot.get("commits", [])}
                if oldest_sha not in loaded_shas:
                    total = snapshot.get("total_commit_count", self._commit_list_limit)
                    extended = self._commit_list_limit + DEFAULT_COMMIT_LIST_LIMIT
                    while extended <= total:
                        ext_snapshot = get_commit_list_snapshot(
                            limit=extended,
                            repo_source=self._repo_source,
                            github_repo_url=self._github_repo_url,
                            refresh_remote=False,
                            local_repo_root=self._local_repo_root,
                        )
                        snapshot = ext_snapshot
                        loaded_shas = {c["sha"] for c in snapshot.get("commits", [])}
                        if oldest_sha in loaded_shas:
                            self._commit_list_limit = extended
                            break
                        extended += DEFAULT_COMMIT_LIST_LIMIT
            fresh_commits = snapshot.get("commits", [])
        except Exception as exc:
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"커밋 갱신 실패: {exc}", 5.0)
            self.call_from_thread(
                self._append_result, f"커밋 갱신 실패: {exc}", "error"
            )
            fresh_commits = []
            snapshot = {}

        self.call_from_thread(self._end_progress, op_id)
        self.call_from_thread(self._do_open_picker, fresh_commits, snapshot)

    def _do_open_picker(self, fresh_commits: list[dict], snapshot: dict) -> None:
        if fresh_commits:
            self._commits = fresh_commits  # 메인 스레드에서 reactive 업데이트
        if not self._commits:
            self._set_status_timed("커밋 목록이 없습니다.", 5.0)
            self._append_result("커밋 목록이 없습니다.", "error")
            return
        # 커밋 목록이 갱신되었을 수 있으므로 SHA 기준으로 인덱스 재계산
        self._sync_commit_selection(self._oldest_sha, self._newest_sha)
        self.push_screen(
            CommitPickerScreen(
                self._commits,
                self._commit_selection,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
                has_more=snapshot.get("has_more_commits", False),
                total_count=snapshot.get("total_commit_count", len(self._commits)),
            ),
            callback=self._on_commit_picker_result,
        )

    def _on_commit_picker_result(
        self, result: tuple[CommitSelection, list[dict]] | str | None
    ) -> None:
        if result == "switch":
            self._open_quiz_list()
            return
        if result is None:
            if self._mode not in ("quiz_loading", "grading"):
                self._set_status(
                    f"커밋 범위: {self._oldest_sha[:7]}..{self._newest_sha[:7]}  |  "
                    "/quiz 로 퀴즈 생성, /commits 로 커밋 선택."
                    if self._oldest_sha and self._newest_sha
                    else "명령어를 입력하세요: /quiz, /grade, /help"
                )
            self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)
            return
        selection, updated_commits = result
        # picker에서 커밋을 더 불러왔을 수 있으므로 앱 커밋 목록 및 limit 동기화
        if len(updated_commits) > len(self._commits):
            self._commits = updated_commits
        self._commit_list_limit = max(self._commit_list_limit, len(updated_commits))
        # 마지막으로 로드한 가장 오래된 커밋 SHA 저장 (다음 오픈 시 해당 커밋까지 보장)
        if updated_commits:
            self._commit_list_oldest_sha = updated_commits[-1]["sha"]
        result = selection
        if result.start_index is None:
            self._set_status_timed("커밋이 선택되지 않았습니다.", 5.0)
            self._append_result("커밋이 선택되지 않았습니다.", "error")
            return

        self._commit_selection = result
        indices = sorted(selected_commit_indices(result))
        if not indices:
            return

        # Map indices to SHAs (commits list is newest-first)
        newest_sha = self._commits[indices[0]]["sha"]
        oldest_sha = self._commits[indices[-1]]["sha"]

        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha

        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=oldest_sha,
            newest_commit_sha=newest_sha,
            local_repo_root=self._local_repo_root,
        )

        # Phase 2: persist the selected range
        self._save_app_state()

        count = len(indices)

        # Update AppStatusBar range
        try:
            self.query_one("#app-status", AppStatusBar).set_range(
                oldest_sha, newest_sha, count
            )
        except Exception:
            pass

        self._log(
            f"커밋 범위 선택: {oldest_sha[:7]}..{newest_sha[:7]} ({count} commits)",
            "success",
        )

        # 퀴즈 상태 초기화 후 새 범위의 세션 복원 시도 (mid-session이므로 log 재생 불필요)
        self._reset_quiz_state()
        session_restored = self._try_restore_session(replay_log=False)
        if not session_restored:
            self._set_status(
                f"커밋 범위 선택됨: {oldest_sha[:7]}..{newest_sha[:7]} ({count} commits). "
                "/quiz 로 퀴즈를 생성하세요."
            )
        self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)

    # ------------------------------------------------------------------
    # Quiz generation
    # ------------------------------------------------------------------

    def _validate_model_override(self, model: str) -> bool:
        """모델명 유효성 검사. 실패 시 빨간 에러 메시지 표시 후 False 반환."""
        from ..settings import SUGGESTED_MODELS

        if model not in SUGGESTED_MODELS:
            suggestions = "\n".join(f"  {m}" for m in SUGGESTED_MODELS)
            msg = RichText()
            msg.append(f"알 수 없는 모델: {model}", style="red")
            msg.append(f"\n\n사용 가능한 모델:\n{suggestions}", style="white")
            self._append_result(msg, "error")
        return model in SUGGESTED_MODELS

    @work(thread=True)
    def _start_quiz(
        self,
        range_arg: str,
        log_block_id: str | None = None,
        count: int = 4,
        author_context: str = "self",
        model_override: str = "",
    ) -> None:
        if self._mode in ("quiz_loading", "grading"):
            self.call_from_thread(
                self._set_status_timed, "이미 작업이 진행 중입니다.", 5.0
            )
            self.call_from_thread(
                self._append_result, "이미 작업이 진행 중입니다.", "error"
            )
            return
        op_id = uuid.uuid4().hex[:8]
        self.call_from_thread(self._set_mode, "quiz_loading")
        self.call_from_thread(
            self._begin_progress, "퀴즈 생성 준비 중...", log_block_id, op_id
        )

        try:
            oldest_sha, newest_sha = self._resolve_range(range_arg)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"범위 해석 실패: {exc}", 5.0)
            self.call_from_thread(
                self._log_to_block,
                f"범위 해석 실패: {exc}",
                "error",
                log_block_id,
                op_id,
            )
            return

        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha

        # Show the range in code view + sync commit selection
        self.call_from_thread(
            self._show_range_in_view,
            oldest_sha,
            newest_sha,
        )
        self.call_from_thread(self._sync_commit_selection, oldest_sha, newest_sha)

        # Build commit context
        try:
            repo = get_repo(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                refresh_remote=False,
                local_repo_root=self._local_repo_root,
            )
            commit_shas = self._collect_shas_in_range(repo, oldest_sha, newest_sha)
            commits = [repo.commit(sha) for sha in commit_shas]
            if len(commits) == 1:
                context = build_commit_context(commits[0], "selected", repo)
                full_file_map = build_full_file_map(commits[0], repo)
            else:
                context = build_multi_commit_context(commits, "range_selected", repo)
                from git import NULL_TREE

                oldest_commit = commits[-1]
                base_commit = (
                    oldest_commit.parents[0] if oldest_commit.parents else NULL_TREE
                )
                full_file_map = build_range_full_file_map(base_commit, commits[0], repo)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(
                self._set_status_timed, f"커밋 컨텍스트 생성 실패: {exc}", 5.0
            )
            self.call_from_thread(
                self._log_to_block,
                f"커밋 컨텍스트 생성 실패: {exc}",
                "error",
                log_block_id,
                op_id,
            )
            return

        # Stream quiz generation
        self.call_from_thread(self._update_progress, "퀴즈 생성 중...", op_id)
        questions: list[InlineQuizQuestion] = []
        known_files: dict[str, str] = {}

        try:
            for event in stream_inline_quiz_progress(
                context,
                count=count,
                full_file_map=full_file_map,
                author_context=author_context,
                model_override=model_override,
            ):
                if self._stop_event.is_set():
                    return
                if event.get("type") == "node":
                    label = event.get("label", event.get("node", ""))
                    self.call_from_thread(
                        self._update_progress, f"퀴즈 생성 중... {label}", op_id
                    )
                elif event.get("type") == "usage":
                    inp = event.get("input_tokens", 0)
                    out = event.get("output_tokens", 0)
                    mdl = event.get("model_name") or None
                    if inp or out:
                        self.call_from_thread(
                            self._append_token_usage, inp, out, log_block_id, mdl
                        )
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    questions = result.get("inline_questions", [])
                    # Parse file context for known_files
                    file_context = context.get("file_context_text", "")
                    if file_context:
                        known_files = parse_file_context_blocks(file_context)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"퀴즈 생성 실패: {exc}", 5.0)
            self.call_from_thread(
                self._log_to_block,
                f"퀴즈 생성 실패: {exc}",
                "error",
                log_block_id,
                op_id,
            )
            return

        if not questions:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(
                self._set_status_timed, "퀴즈 질문이 생성되지 않았습니다.", 5.0
            )
            self.call_from_thread(
                self._log_to_block,
                "퀴즈 질문이 생성되지 않았습니다.",
                "error",
                log_block_id,
                op_id,
            )
            return

        # git에서 전체 파일 내용을 가져와 known_files 보강/덮어쓰기
        # (parse_file_context_blocks는 3000자 잘림 콘텐츠를 반환할 수 있음)
        try:
            from ..domain.code_context import get_file_content_at_commit_or_empty

            seen: set[str] = set()
            for q in questions:
                fpath = q.get("file_path", "")
                if fpath and fpath not in seen:
                    seen.add(fpath)
                    content = get_file_content_at_commit_or_empty(
                        repo, newest_sha, fpath
                    )
                    if content:
                        known_files[fpath] = content
        except Exception:
            pass

        self._questions = questions
        self._answers = {}
        self._grades = []
        self._grading_summary = {}
        self._known_files = known_files
        self._current_q_index = 0
        self.call_from_thread(
            lambda: self.query_one("#cmd-bar", CommandBar).set_quiz_questions(
                [q.get("question", "") for q in questions]
            )
        )
        # mode will be set to quiz_answering in _apply_quiz_to_view (called from main thread)

        # Phase 2: persist updated SHA range (set via /quiz range_arg)
        self.call_from_thread(self._save_app_state)
        # Phase 4: save initial session
        self.call_from_thread(self._save_session)

        self.call_from_thread(self._end_progress, op_id)
        self.call_from_thread(
            self._log_to_block,
            f"퀴즈 생성 완료! {len(questions)}문제",
            "success",
            log_block_id,
            op_id,
        )
        self.call_from_thread(self._apply_quiz_to_view)

    def _apply_quiz_to_view(self) -> None:
        self._set_mode("idle")
        total = len(self._questions)
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(0, 0)
        except Exception:
            pass
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.load_inline_quiz(
            questions=self._questions,
            answers=self._answers,
            grades=self._grades,
            known_files=self._known_files,
            current_index=0,
            focus_answer=False,
        )
        self._sync_quiz_alert()
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        self.call_after_refresh(lambda: self.call_after_refresh(cmd_bar.focus_input))
        self.set_timer(0.3, cmd_bar.focus_input)  # mount 비동기 체인 완료 후 보장

    def _show_range_in_view(self, oldest_sha: str, newest_sha: str) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=oldest_sha,
            newest_commit_sha=newest_sha,
            local_repo_root=self._local_repo_root,
        )
        # @-mention 자동완성용 파일 목록 CommandBar에 주입
        try:
            cmd_bar = self.query_one("#cmd-bar", CommandBar)
            cmd_bar.set_mention_files(list(code_view.file_paths))
            cmd_bar.set_mention_changed_files(set(code_view.changed_paths))
        except Exception:
            pass
        # Update AppStatusBar range (count unknown here; use commits list for approximation)
        try:
            sha_index = {c.get("sha", ""): i for i, c in enumerate(self._commits)}
            o_idx = sha_index.get(oldest_sha, 0)
            n_idx = sha_index.get(newest_sha, 0)
            count = abs(o_idx - n_idx) + 1
            self.query_one("#app-status", AppStatusBar).set_range(
                oldest_sha, newest_sha, count
            )
        except Exception:
            pass
        # 코드뷰 렌더링이 code-pane에 포커스를 주므로 cmd-bar로 복구
        try:
            self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Review (commit reading material)
    # ------------------------------------------------------------------

    @work(thread=True)
    def _start_review(
        self, range_arg: str, log_block_id: str | None = None, model_override: str = ""
    ) -> None:
        if self._mode in ("grading", "reviewing"):
            self.call_from_thread(
                self._set_status_timed, "이미 작업이 진행 중입니다.", 5.0
            )
            self.call_from_thread(
                self._append_result, "이미 작업이 진행 중입니다.", "error"
            )
            return
        op_id = uuid.uuid4().hex[:8]
        self.call_from_thread(self._set_mode, "reviewing")
        self.call_from_thread(self._show_chat_view)
        self.call_from_thread(
            self._begin_progress, "리뷰 준비 중...", log_block_id, op_id
        )

        try:
            oldest_sha, newest_sha = self._resolve_range(range_arg)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"범위 해석 실패: {exc}", 5.0)
            self.call_from_thread(self._log, f"범위 해석 실패: {exc}", "error")
            return

        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha
        self.call_from_thread(self._show_range_in_view, oldest_sha, newest_sha)
        self.call_from_thread(self._sync_commit_selection, oldest_sha, newest_sha)

        try:
            repo = get_repo(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                refresh_remote=False,
                local_repo_root=self._local_repo_root,
            )
            commit_shas = self._collect_shas_in_range(repo, oldest_sha, newest_sha)
            commits = [repo.commit(sha) for sha in commit_shas]
            if len(commits) == 1:
                context = build_commit_context(commits[0], "selected", repo)
            else:
                context = build_multi_commit_context(commits, "range_selected", repo)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(
                self._set_status_timed, f"커밋 컨텍스트 생성 실패: {exc}", 5.0
            )
            self.call_from_thread(self._log, f"컨텍스트 생성 실패: {exc}", "error")
            return

        self.call_from_thread(self._update_progress, "리뷰 생성 중...", op_id)
        final_output = ""
        try:
            for event in stream_read_progress(context, model_override=model_override):
                if self._stop_event.is_set():
                    return
                if event.get("type") == "node":
                    label = event.get("label", event.get("node", ""))
                    self.call_from_thread(
                        self._update_progress, f"리뷰 생성 중... {label}", op_id
                    )
                elif event.get("type") == "usage":
                    inp = event.get("input_tokens", 0)
                    out = event.get("output_tokens", 0)
                    mdl = event.get("model_name") or None
                    if inp or out:
                        self.call_from_thread(
                            self._append_token_usage, inp, out, log_block_id, mdl
                        )
                elif event.get("type") == "result":
                    final_output = event.get("result", {}).get("final_output", "")
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"리뷰 생성 실패: {exc}", 5.0)
            self.call_from_thread(self._log, f"리뷰 생성 실패: {exc}", "error")
            return

        self.call_from_thread(self._set_mode, "idle")
        self.call_from_thread(self._end_progress, op_id)
        if final_output:
            self._map_or_review_done = True
            self.call_from_thread(self._set_status_timed, "리뷰 완료.", 3.0)
            self.call_from_thread(
                self._log_to_block, "리뷰 완료.", "info", log_block_id, op_id
            )
            self.call_from_thread(self._render_review, final_output, log_block_id)
        else:
            self.call_from_thread(
                self._log, "리뷰 내용이 생성되지 않았습니다.", "error"
            )
            self.call_from_thread(self._set_status_timed, "리뷰 생성 실패.", 5.0)
            self.call_from_thread(
                self._log_to_block, "리뷰 생성 실패.", "error", log_block_id, op_id
            )

    def _render_review(self, md_text: str, log_block_id: str | None = None) -> None:
        try:
            hv = self.query_one("#history-view", HistoryView)
            # CSS ID로 블록 조회 — 위젯 참조보다 신뢰할 수 있음
            block = None
            if log_block_id:
                try:
                    block = hv.query_one(f"#{log_block_id}")
                except Exception:
                    pass
            hv.append_markdown(md_text, block=block)
        except Exception:
            pass
        self._log_chat("app_markdown", md_text, block_id=log_block_id or "")

    # ------------------------------------------------------------------
    # Map
    # ------------------------------------------------------------------

    @work(thread=True)
    def _start_map(
        self,
        range_arg: str,
        refresh: bool = False,
        log_block_id: str | None = None,
        model_override: str = "",
    ) -> None:
        op_id = uuid.uuid4().hex[:8]
        self.call_from_thread(self._show_chat_view)
        self.call_from_thread(
            self._begin_progress, "맵 준비 중...", log_block_id, op_id
        )

        try:
            oldest_sha, newest_sha = self._resolve_range(range_arg)
        except Exception as exc:
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"범위 해석 실패: {exc}", 5.0)
            self.call_from_thread(self._log, f"범위 해석 실패: {exc}", "error")
            return

        session_id = f"{oldest_sha[:7]}-{newest_sha[:7]}"

        # 캐시 확인: full=True 캐시만 히트
        if not refresh:
            cached = load_map_cache(
                session_id,
                repo_source=self._repo_source,
                local_repo_root=self._local_repo_root,
            )
            if cached and cached.get("full", False):
                self.call_from_thread(self._end_progress, op_id)
                self.call_from_thread(
                    self._log_to_block, "맵 완료 (캐시).", "info", log_block_id, op_id
                )
                self.call_from_thread(self._render_map, cached, log_block_id, True)
                self.call_from_thread(self._set_status_timed, "맵 완료 (캐시).", 3.0)
                return

        try:
            repo = get_repo(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                refresh_remote=False,
                local_repo_root=self._local_repo_root,
            )
            commit_shas = self._collect_shas_in_range(repo, oldest_sha, newest_sha)
            raw_commits = [repo.commit(sha) for sha in commit_shas]
        except Exception as exc:
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(
                self._set_status_timed, f"컨텍스트 생성 실패: {exc}", 5.0
            )
            self.call_from_thread(self._log, f"컨텍스트 생성 실패: {exc}", "error")
            return

        # 커밋 목록 정보
        commits_info = []
        for c in raw_commits:
            try:
                commits_info.append(
                    {
                        "sha": c.hexsha[:7],
                        "title": c.message.splitlines()[0][:70],
                        "date": c.committed_datetime.strftime("%Y-%m-%d %H:%M"),
                    }
                )
            except Exception:
                pass

        # 커밋 컨텍스트 (diff map용)
        self.call_from_thread(self._update_progress, "변경 파일 분석 중...", op_id)
        try:
            if len(raw_commits) == 1:
                context = build_commit_context(raw_commits[0], "selected", repo)
            else:
                context = build_multi_commit_context(
                    raw_commits, "range_selected", repo
                )
        except Exception as exc:
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(
                self._set_status_timed, f"컨텍스트 생성 실패: {exc}", 5.0
            )
            self.call_from_thread(self._log, f"컨텍스트 생성 실패: {exc}", "error")
            return

        # numstat
        numstat_text = ""
        try:
            if oldest_sha == newest_sha:
                numstat_text = repo.git.diff(
                    "--numstat", "--no-color", f"{oldest_sha}^", oldest_sha
                )
            else:
                numstat_text = repo.git.diff(
                    "--numstat", "--no-color", f"{oldest_sha}^", newest_sha
                )
        except Exception:
            pass

        # 커밋 맵 (diff 파일 역할 요약)
        self.call_from_thread(self._update_progress, "파일 역할 분석 중...", op_id)
        summaries: dict[str, str] = {}
        for event in stream_map_progress(context, model_override=model_override):
            if self._stop_event.is_set():
                return
            if event.get("type") == "usage":
                inp = event.get("input_tokens", 0)
                out = event.get("output_tokens", 0)
                mdl = event.get("model_name") or None
                if inp or out:
                    self.call_from_thread(
                        self._append_token_usage, inp, out, log_block_id, mdl
                    )
            elif event.get("type") == "result":
                summaries = event.get("summaries", {})

        cache_data: dict = {
            "session_id": session_id,
            "oldest_sha": oldest_sha,
            "newest_sha": newest_sha,
            "commits": commits_info,
            "summaries": summaries,
            "numstat": numstat_text,
            "full": False,
        }

        # 프로젝트 맵 (로컬 저장소인 경우)
        if self._local_repo_root:
            self.call_from_thread(
                self._update_progress, "프로젝트 구조 분석 중...", op_id
            )
            try:
                file_paths = get_file_tree(self._local_repo_root)
                commit_counts = get_commit_counts(repo)
                tree_summary = build_tree_summary(file_paths)
                file_path_set = set(file_paths)
                hot_files = sorted(
                    [(p, c) for p, c in commit_counts.items() if p in file_path_set],
                    key=lambda x: x[1],
                    reverse=True,
                )
                directories: dict[str, str] = {}
                key_files: dict[str, str] = {}
                for event in stream_full_map_progress(
                    tree_summary,
                    hot_files,
                    model_override=model_override,
                    file_paths=file_paths,
                ):
                    if self._stop_event.is_set():
                        return
                    if event.get("type") == "usage":
                        inp = event.get("input_tokens", 0)
                        out = event.get("output_tokens", 0)
                        mdl = event.get("model_name") or None
                        if inp or out:
                            self.call_from_thread(
                                self._append_token_usage, inp, out, log_block_id, mdl
                            )
                    elif event.get("type") == "result":
                        directories = event.get("directories", {})
                        key_files = event.get("key_files", {})
                cache_data.update(
                    {
                        "full": True,
                        "tree_summary": tree_summary,
                        "hot_files": hot_files[:20],
                        "directories": directories,
                        "key_files": key_files,
                    }
                )
            except Exception:
                pass  # 프로젝트 맵 실패해도 커밋 맵은 표시

        try:
            save_map_cache(
                session_id,
                cache_data,
                repo_source=self._repo_source,
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass

        self._map_or_review_done = True
        self.call_from_thread(self._end_progress, op_id)
        self.call_from_thread(
            self._log_to_block, "맵 완료.", "info", log_block_id, op_id
        )
        self.call_from_thread(self._render_map, cache_data, log_block_id, False)
        self.call_from_thread(self._set_status_timed, "맵 완료.", 3.0)

    def _render_map(
        self,
        data: dict,
        log_block_id: str | None = None,
        from_cache: bool = False,
        *,
        _replay: bool = False,
    ) -> None:
        oldest_sha = data.get("oldest_sha", "")[:7]
        newest_sha = data.get("newest_sha", "")[:7]
        commits_info: list[dict] = data.get("commits", [])
        summaries: dict[str, str] = data.get("summaries", {})
        numstat_text: str = data.get("numstat", "")
        is_full: bool = data.get("full", False)
        cache_label = " (캐시)" if from_cache else ""

        # numstat 파싱
        stat_entries: list[tuple[str, int, int]] = []
        for line in numstat_text.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                try:
                    add = int(parts[0]) if parts[0] != "-" else 0
                    delete = int(parts[1]) if parts[1] != "-" else 0
                    stat_entries.append((parts[2], add, delete))
                except ValueError:
                    pass

        import unicodedata as _ud

        def _dw(s: str) -> int:
            """문자열의 터미널 표시 너비 (CJK 2칸, 그 외 1칸)."""
            return sum(2 if _ud.east_asian_width(c) in ("W", "F") else 1 for c in s)

        def _pad(s: str, width: int) -> str:
            return s + " " * max(0, width - _dw(s))

        _SEP = "  " + "─" * 42 + "\n"

        t = RichText()

        # ── 커밋 정보 ───────────────────────────────
        range_str = (
            oldest_sha if oldest_sha == newest_sha else f"{oldest_sha}..{newest_sha}"
        )
        t.append("  커밋 정보", style="bold white")
        t.append(f"  {range_str}{cache_label}\n", style="dim cyan")
        t.append(_SEP, style="dim")
        for c in commits_info:
            t.append(f"    {c['date']}  ", style="dim")
            t.append(f"[{c['sha']}]", style="cyan")
            t.append(f"  {c['title']}\n", style="")
        t.append("\n")

        # ── 프로젝트 맵 ────────────────────────────
        if is_full:
            directories: dict[str, str] = data.get("directories", {})
            key_files: dict[str, str] = data.get("key_files", {})
            t.append("  프로젝트 맵\n", style="bold white")
            t.append(_SEP, style="dim")
            dir_path_set = set(directories.keys())

            def _find_best_parent_dir(kpath: str) -> str:
                """key_file 경로에서 directories에 있는 가장 가까운 부모를 반환.
                앞 1컴포넌트(src/ 등) prefix 불일치만 보정한다."""
                kparts = kpath.split("/")
                # 직접 조상 (깊은 것부터)
                for depth in range(len(kparts) - 1, 0, -1):
                    candidate = "/".join(kparts[:depth])
                    if candidate in dir_path_set:
                        return candidate
                # 앞 1컴포넌트만 제거해 재시도 (src/ 등 prefix 불일치 보정)
                if len(kparts) > 1:
                    for depth in range(len(kparts) - 2, 0, -1):
                        candidate = "/".join(kparts[1 : depth + 1])
                        if candidate in dir_path_set:
                            return candidate
                return ""

            keys_by_dir: dict[str, list[tuple[str, str]]] = {}
            for kpath, ksummary in key_files.items():
                parent = _find_best_parent_dir(kpath)
                keys_by_dir.setdefault(parent, []).append(
                    (kpath.rsplit("/", 1)[-1], ksummary)
                )
            # 최소 depth 기준으로 정규화 (첫 항목이 4칸에서 시작)
            min_depth = (
                min((d.count("/") for d in directories), default=0)
                if directories
                else 0
            )
            # 좌측 컬럼 최대 너비 계산 (정렬 기준)
            left_col_width = 4
            for dir_path in directories:
                depth = dir_path.count("/") - min_depth
                indent_w = 4 + 2 * depth
                name_w = _dw(dir_path.rsplit("/", 1)[-1] + "/")
                left_col_width = max(left_col_width, indent_w + name_w)
                for fname, _ in keys_by_dir.get(dir_path, []):
                    left_col_width = max(left_col_width, indent_w + 2 + 2 + _dw(fname))
            left_col_width += 2  # 여백
            for dir_path, dir_summary in sorted(directories.items()):
                depth = dir_path.count("/") - min_depth
                indent = "    " + "  " * depth
                name = dir_path.rsplit("/", 1)[-1] + "/"
                left = indent + name
                t.append(_pad(left, left_col_width), style="bold")
                t.append(f"{dir_summary}\n", style="dim")
                for fname, fsummary in keys_by_dir.get(dir_path, []):
                    kindent = "    " + "  " * (depth + 1)
                    left_k = kindent + "★ " + fname
                    t.append(_pad(left_k, left_col_width), style="bold cyan")
                    t.append(f"{fsummary}\n", style="dim")
            # directories에 매칭되지 않은 key_files 폴백 렌더링
            orphaned = [
                (kpath, ksummary)
                for kpath, ksummary in sorted(key_files.items())
                if _find_best_parent_dir(kpath) not in dir_path_set
            ]
            for kpath, ksummary in orphaned:
                left_k = "    ★ " + kpath
                t.append(_pad(left_k, left_col_width), style="bold cyan")
                t.append(f"{ksummary}\n", style="dim")
            t.append("\n")

        # ── 커밋 맵 ─────────────────────────────────
        t.append("  커밋 맵\n", style="bold white")
        t.append(_SEP, style="dim")
        if summaries:
            stat_lookup: dict[str, tuple[int, int]] = {
                p: (a, d) for p, a, d in stat_entries
            }
            # 변경 규모 큰 순 정렬
            sorted_paths = sorted(
                summaries.keys(),
                key=lambda p: sum(stat_lookup.get(p, (0, 0))),
                reverse=True,
            )
            for path in sorted_paths:
                fname = path.rsplit("/", 1)[-1]
                add, delete = stat_lookup.get(path, (0, 0))
                t.append(f"    {_pad(fname, 26)}", style="bold")
                if add or delete:
                    add_str = f"+{add}"
                    del_str = f"-{delete}"
                    stat_field = add_str + "/" + del_str
                    padding = " " * max(0, 12 - _dw(stat_field))
                    t.append(add_str, style="green")
                    t.append("/", style="dim")
                    t.append(del_str, style="dim red")
                    t.append(padding)
                else:
                    t.append(" " * 12)
                t.append(f"  {summaries[path]}\n", style="white")
        else:
            t.append("    (파일 요약 없음)\n", style="dim")
        t.append("\n")

        try:
            hv = self.query_one("#history-view", HistoryView)
            block = None
            if log_block_id:
                try:
                    block = hv.query_one(f"#{log_block_id}")
                except Exception:
                    pass
            hv.append_rich(t, block=block)
        except Exception:
            pass
        if not _replay:
            session_id = data.get("session_id", "")
            if session_id:
                self._log_chat("app_map", session_id, block_id=log_block_id or "")

    # ------------------------------------------------------------------
    # Grading
    # ------------------------------------------------------------------

    @work(thread=True)
    def _start_grading(
        self, log_block_id: str | None = None, model_override: str = ""
    ) -> None:
        if not self._questions:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(
                self._set_status_timed,
                "채점할 퀴즈가 없습니다. /quiz 먼저 실행하세요.",
                5.0,
            )
            self.call_from_thread(
                self._append_result,
                "채점할 퀴즈가 없습니다. /quiz 먼저 실행하세요.",
                "error",
            )
            return
        unanswered = [
            q for q in self._questions if q.get("id", "") not in self._answers
        ]
        if unanswered:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(
                self._set_status_timed,
                f"아직 {len(unanswered)}개 질문에 답변하지 않았습니다.",
                5.0,
            )
            self.call_from_thread(
                self._append_result,
                f"아직 {len(unanswered)}개 질문에 답변하지 않았습니다.",
                "error",
            )
            return

        op_id = uuid.uuid4().hex[:8]
        self.call_from_thread(self._begin_progress, "채점 중...", log_block_id, op_id)

        grades: list[InlineQuizGrade] = []
        grading_summary: dict = {}
        try:
            for event in stream_inline_grade_progress(
                self._questions, self._answers, model_override=model_override
            ):
                if self._stop_event.is_set():
                    return
                if event.get("type") == "node":
                    label = event.get("label", event.get("node", ""))
                    self.call_from_thread(
                        self._update_progress, f"채점 중... {label}", op_id
                    )
                elif event.get("type") == "usage":
                    inp = event.get("input_tokens", 0)
                    out = event.get("output_tokens", 0)
                    mdl = event.get("model_name") or None
                    if inp or out:
                        self.call_from_thread(
                            self._append_token_usage, inp, out, log_block_id, mdl
                        )
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    grades = result.get("final_grades", [])
                    grading_summary = result.get("grading_summary", {})
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._end_progress, op_id)
            self.call_from_thread(self._set_status_timed, f"채점 실패: {exc}", 5.0)
            self.call_from_thread(
                self._log_to_block, f"채점 실패: {exc}", "error", log_block_id, op_id
            )
            return

        self._grades = grades
        self._grading_summary = grading_summary
        self._graded_this_session = True
        self.call_from_thread(self._set_mode, "idle")
        self.call_from_thread(self._end_progress, op_id)
        self.call_from_thread(self._sync_quiz_alert)

        # Phase 4: persist session with grades
        self.call_from_thread(self._save_session)
        self.call_from_thread(self._apply_grades_to_view, grades, log_block_id, op_id)

    def _apply_grades_to_view(
        self,
        grades: list[InlineQuizGrade],
        log_block_id: str | None = None,
        op_id: str | None = None,
    ) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.update_grades(grades)
        # Compute average score
        scores = [g.get("score", 0) for g in grades]
        avg = sum(scores) / len(scores) if scores else 0
        msg = f"채점 완료! 평균 {avg:.1f}/100  ({len(grades)}문제)"
        self._set_status_timed(msg, 3.0)
        self._log_to_block(msg, "success", log_block_id, op_id)

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        from rich.text import Text as RichText

        hook_installed = (
            self._repo_source != "github"
            and bool(self._local_repo_root)
            and (self._local_repo_root / ".git" / "hooks" / "post-commit").exists()
            and _has_hook(
                (self._local_repo_root / ".git" / "hooks" / "post-commit").read_text()
            )
        )
        hook_desc = RichText()
        if hook_installed:
            hook_desc.append("● ", style="green")
            hook_desc.append("설치됨", style="dim green")
            hook_desc.append("  /hook off 으로 제거", style="dim")
        else:
            hook_desc.append("○ ", style="dim")
            hook_desc.append("post-commit hook 설치 (커밋 후 자동 퀴즈)", style="dim")

        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        cmd_bar.show_help_panel(
            [
                ("/commits", "커밋 범위 선택"),
                (
                    "/quiz [범위] [--ai|--others] [개수]",
                    "퀴즈 생성 · 범위: HEAD~3, A..B 등 · 개수: 기본 3 · --ai AI 코드 / --others 타인 코드 모드",
                ),
                ("/grade", "채점"),
                ("/answer", "답변 재진입"),
                ("/review [범위]", "커밋 해설 보기"),
                ("/clear", "대화 초기화 (이전 대화는 /resume 으로 복원)"),
                ("/resume", "이전 대화 불러오기"),
                ("/repo [URL/경로]", "저장소 전환 (인자 없으면 목록 모달)"),
                ("/apikey", "API key 상태 표시"),
                ("/apikey set <key>", "API key 설정"),
                ("/apikey unset", "저장된 API key 삭제"),
                (
                    "/model [이름]",
                    "모델 변경 (예: /model gpt-4o) · 인자 없으면 목록 표시",
                ),
                ("/hook on", hook_desc),
                ("/help", "도움말"),
                ("Shift+Tab", "채팅 뷰 ↔ 코드 뷰 전환"),
                ("Ctrl+Q", "종료"),
            ]
        )

    def _handle_install_hook(self, terminal: str = "") -> None:
        import stat

        if self._repo_source == "github":
            self._set_status_timed(
                "GitHub 저장소에는 post-commit hook을 설치할 수 없습니다.", 5.0
            )
            self._append_result(
                "GitHub 저장소에는 post-commit hook을 설치할 수 없습니다.", "error"
            )
            return

        repo_root = self._local_repo_root
        if not repo_root:
            self._set_status_timed("[red]저장소가 로드되지 않았습니다.[/red]", 5.0)
            self._append_result("저장소가 로드되지 않았습니다.", "error")
            return

        hook_path = repo_root / ".git" / "hooks" / "post-commit"
        block = _build_hook_block(repo_root, terminal or "auto")

        if hook_path.exists():
            content = hook_path.read_text()
            if _has_hook(content):
                # 기존 블록 제거 후 새 블록으로 교체
                stripped = _strip_hook(content)
                base = (
                    stripped
                    if stripped.strip() not in ("", "#!/bin/sh")
                    else "#!/bin/sh"
                )
                hook_path.write_text(base.rstrip("\n") + "\n" + block)
                hook_path.chmod(
                    hook_path.stat().st_mode
                    | stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )
                self._set_status_timed(f"✓ hook 업데이트 완료: {hook_path}", 3.0)
                self._append_result(f"✓ hook 업데이트 완료: {hook_path}", "info")
                self._update_hook_status()
                return
            with hook_path.open("a") as f:
                f.write(f"\n{block}")
        else:
            hook_path.parent.mkdir(parents=True, exist_ok=True)
            hook_path.write_text(f"#!/bin/sh\n{block}")

        hook_path.chmod(
            hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self._set_status_timed(f"✓ hook 설치 완료: {hook_path}", 3.0)
        self._append_result(f"✓ hook 설치 완료: {hook_path}", "info")
        self._update_hook_status()

    def _handle_uninstall_hook(self) -> None:
        if self._repo_source == "github":
            self._set_status_timed(
                "GitHub 저장소에는 post-commit hook이 없습니다.", 5.0
            )
            self._append_result(
                "GitHub 저장소에는 post-commit hook이 없습니다.", "error"
            )
            return

        repo_root = self._local_repo_root
        if not repo_root:
            self._set_status_timed("[red]저장소가 로드되지 않았습니다.[/red]", 5.0)
            self._append_result("저장소가 로드되지 않았습니다.", "error")
            return

        hook_path = repo_root / ".git" / "hooks" / "post-commit"
        if not hook_path.exists():
            self._set_status_timed("post-commit hook 파일이 없습니다.", 5.0)
            self._append_result("post-commit hook 파일이 없습니다.", "error")
            return

        content = hook_path.read_text()
        if not _has_hook(content):
            self._set_status_timed("git-study hook 이 설치되어 있지 않습니다.", 5.0)
            self._append_result("git-study hook 이 설치되어 있지 않습니다.", "error")
            return

        new_content = _strip_hook(content)
        if new_content.strip() in ("", "#!/bin/sh"):
            hook_path.unlink()
            self._set_status_timed(f"✓ hook 제거 완료 (파일 삭제): {hook_path}", 3.0)
            self._append_result(f"✓ hook 제거 완료 (파일 삭제): {hook_path}", "info")
        else:
            hook_path.write_text(new_content + "\n")
            self._set_status_timed(
                f"✓ hook 제거 완료 (기존 내용 보존): {hook_path}", 3.0
            )
            self._append_result(
                f"✓ hook 제거 완료 (기존 내용 보존): {hook_path}", "info"
            )
        self._update_hook_status()

    # ------------------------------------------------------------------
    # Repo switching
    # ------------------------------------------------------------------

    def _handle_repo_command(self, arg: str) -> None:
        arg = arg.strip()
        if arg:
            self._switch_repo(arg)
        else:
            self._open_repo_picker()

    def _open_repo_picker(self) -> None:
        # _original_local_root: github 전환 후에도 startup 경로를 보존
        local_root = self._original_local_root or self._local_repo_root
        self.push_screen(
            RepoPickerScreen(
                current_local_root=local_root,
                current_repo_source=self._repo_source,
                current_github_url=self._github_repo_url,
            ),
            callback=self._on_repo_picker_result,
        )

    def _on_repo_picker_result(self, result: tuple[str, str] | None) -> None:
        if result is None:
            self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)
            return
        repo_source, url_or_path = result
        self._switch_repo_impl(repo_source, url_or_path)
        self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)

    def _switch_repo(self, arg: str) -> None:
        """Parse argument and dispatch to _switch_repo_impl."""
        if arg.startswith("http") or arg.startswith("github.com"):
            try:
                url = normalize_github_repo_url(arg)
                self._switch_repo_impl("github", url)
            except ValueError as exc:
                self._set_status_timed(f"잘못된 URL: {exc}", 5.0)
                self._append_result(f"잘못된 URL: {exc}", "error")
        else:
            path = Path(arg).expanduser().resolve()
            self._switch_repo_impl("local", str(path))

    @work(thread=True)
    def _switch_repo_impl(self, repo_source: str, url_or_path: str) -> None:
        """Switch to a new repo (background thread)."""
        self.call_from_thread(self._set_status, f"저장소 전환 중... {url_or_path}")
        self.call_from_thread(self._reset_quiz_state)
        # repo 변경: 챗 상태 + 히스토리 뷰 초기화
        self._current_thread_id = ""
        self._oldest_sha = ""
        self._newest_sha = ""
        self.call_from_thread(self._clear_history_view)

        if repo_source == "github":
            self._repo_source = "github"
            self._github_repo_url = url_or_path
            self._local_repo_root = None
        else:
            root = find_local_repo_root(start=Path(url_or_path))
            if root is None:
                self.call_from_thread(
                    self._set_status_timed,
                    f"Git 저장소를 찾을 수 없습니다: {url_or_path}",
                    5.0,
                )
                self.call_from_thread(
                    self._append_result,
                    f"Git 저장소를 찾을 수 없습니다: {url_or_path}",
                    "error",
                )
                return
            self._repo_source = "local"
            self._local_repo_root = root
            self._github_repo_url = None

        try:
            snapshot = get_commit_list_snapshot(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                refresh_remote=(self._repo_source == "github"),
                local_repo_root=self._local_repo_root,
            )
        except Exception as exc:
            self.call_from_thread(self._set_status_timed, f"커밋 로드 실패: {exc}", 5.0)
            self.call_from_thread(
                self._append_result, f"커밋 로드 실패: {exc}", "error"
            )
            return

        commits = snapshot.get("commits", [])

        # Persist the recently used local repo
        if self._repo_source == "local" and self._local_repo_root:
            try:
                save_recent_local_repo(str(self._local_repo_root))
            except Exception:
                pass

        try:
            saved_state = load_app_state(
                repo_source=self._repo_source,
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            saved_state = {}

        self.call_from_thread(self._apply_commits, commits, saved_state)

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------

    def _handle_apikey_command(self, arg: str) -> None:
        arg = arg.strip()
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else ""

        if sub == "set":
            key = parts[1].strip() if len(parts) > 1 else ""
            if key:
                self._set_api_key(key)
            else:
                self._set_status_timed("/apikey set 뒤에 key를 입력하세요.", 4.0)
        elif sub == "unset":
            self._unset_api_key()
        else:
            self._show_apikey_status()

    def _show_apikey_status(self) -> None:
        active_key, source = get_openai_api_key()
        file_key = load_file_openai_api_key()

        def _mask(k: str | None) -> str:
            if not k:
                return "없음"
            return f"{k[:8]}..." if len(k) > 8 else "(설정됨)"

        active_masked = _mask(active_key)
        file_masked = _mask(file_key)
        secrets_path = get_secrets_path()

        source_label = {
            "env": "환경변수 (OPENAI_API_KEY)",
            "file": "secrets.json",
        }.get(source, "없음")

        file_note = (
            "  ← 현재 미사용 (env 우선)" if (source == "env" and file_key) else ""
        )
        file_line = f"  secrets.json  : {file_masked}{file_note}"
        detail = "\n".join(
            [
                "API Key 상태",
                f"  활성 (사용 중) : {active_masked}  [{source_label}]",
                file_line,
                f"  파일 경로     : {secrets_path}",
            ]
        )

        file_summary = "저장됨" if file_key else "없음"
        unused = " (미사용)" if source == "env" and file_key else ""

        if source == "missing":
            self._set_status_timed(
                "API key 없음. /apikey set <key> 로 설정하세요.", 5.0
            )
            msg = RichText("API Key 상태\n")
            msg.append(f"  활성 (사용 중) : {active_masked}  [{source_label}]\n", style="bold red")
            msg.append(f"  secrets.json  : {file_masked}\n", style="bold red")
            msg.append(f"  파일 경로     : {secrets_path}")
            msg.append("\n\n  다음 명령어로 API key를 설정하세요:\n", style="dim")
            msg.append("  /apikey set [OPENAI_API_KEY]", style="bold color(214)")
            self._append_result(msg, "info")
        else:
            self._set_status_dismissable(
                f"활성: {source}  ·  file: {file_summary}{unused}  [dim]  ESC 닫기[/dim]"
            )
            self._append_result(detail, "info")

    def _sync_logo_animation(self) -> None:
        """API key 활성화 여부에 따라 로고 애니메이션 재개/정지."""
        api_key, _ = get_openai_api_key()
        try:
            logo = self.query_one(FullLogoAnimated)
            if api_key:
                logo.resume_animation()
            else:
                logo.pause_animation()
        except Exception:
            pass

    def _set_api_key(self, key: str) -> None:
        try:
            save_openai_api_key(key)
            masked = f"{key[:8]}..." if len(key) > 8 else "(설정됨)"
            self._set_status_timed(
                f"API key 저장됨: {masked}  (~/.git-study/secrets.json)", 3.0
            )
            self._append_result(
                f"API key 저장됨: {masked}  (~/.git-study/secrets.json)", "info"
            )
            self._log("OpenAI API key 저장 완료.", "success")
            self.query_one("#cmd-bar", CommandBar).clear_warning_alert()
            self._sync_logo_animation()
        except Exception as exc:
            self._set_status_timed(f"API key 저장 실패: {exc}", 5.0)
            self._append_result(f"API key 저장 실패: {exc}", "error")

    def _unset_api_key(self) -> None:
        try:
            delete_openai_api_key()
            self._set_status_timed("API key 삭제됨  (~/.git-study/secrets.json)", 3.0)
            self._append_result("API key 삭제됨  (~/.git-study/secrets.json)", "info")
            self._sync_logo_animation()
        except Exception as exc:
            self._set_status_timed(f"API key 삭제 실패: {exc}", 5.0)
            self._append_result(f"API key 삭제 실패: {exc}", "error")

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def _handle_model_command(self, arg: str) -> None:
        from ..settings import (
            DEFAULT_MODEL,
            SUGGESTED_MODELS,
            load_settings,
            save_settings,
        )

        arg = arg.strip()
        if arg:
            if arg not in SUGGESTED_MODELS:
                self._set_status_timed(f"알 수 없는 모델: {arg}", 5.0)
                self._append_result(
                    f"알 수 없는 모델: {arg}\n\n사용 가능한 모델:\n"
                    + "\n".join(f"  {m}" for m in SUGGESTED_MODELS),
                    "error",
                )
                return
            try:
                save_settings(model=arg)
                self._set_status_timed(f"모델 변경됨: {arg}", 3.0)
                self._append_result(f"모델 변경됨: {arg}", "info")
                self._log(f"모델이 {arg} 으로 변경되었습니다.", "success")
            except Exception as exc:
                self._set_status_timed(f"모델 저장 실패: {exc}", 5.0)
                self._append_result(f"모델 저장 실패: {exc}", "error")
        else:
            current = load_settings().get("model", DEFAULT_MODEL)
            self._set_status_dismissable(
                f"현재 모델: {current}  ·  /model <이름> 으로 변경  [dim]ESC 닫기[/dim]"
            )
            self._log(
                f"현재 모델: {current}\n\n제안 모델:\n"
                + "\n".join(f"  {m}" for m in SUGGESTED_MODELS),
                "info",
            )

    # ------------------------------------------------------------------
    # Range resolution
    # ------------------------------------------------------------------

    def _resolve_range(self, range_arg: str) -> tuple[str, str]:
        """Resolve a range argument to (oldest_sha, newest_sha)."""
        repo = get_repo(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            refresh_remote=False,
            local_repo_root=self._local_repo_root,
        )

        if not range_arg:
            # Default: use current _oldest_sha/_newest_sha if set, else HEAD~1..HEAD
            if self._oldest_sha and self._newest_sha:
                return self._oldest_sha, self._newest_sha
            head = repo.head.commit
            if head.parents:
                return head.parents[0].hexsha, head.hexsha
            return head.hexsha, head.hexsha

        # Support "SHA1..SHA2" format (order doesn't matter) — ".." 체크를 먼저
        # "HEAD~1..HEAD~4" 같은 입력이 HEAD~ 분기에 잘못 걸리지 않도록
        if ".." in range_arg:
            parts = range_arg.split("..", 1)
            c1 = repo.commit(parts[0].strip())
            c2 = repo.commit(parts[1].strip())
            try:
                # exits 0 if c1 is ancestor of c2 (= c1 is older)
                repo.git.merge_base("--is-ancestor", c1.hexsha, c2.hexsha)
                return c1.hexsha, c2.hexsha
            except Exception:
                return c2.hexsha, c1.hexsha

        # Support "HEAD~N" format -> range from HEAD~N to HEAD
        if range_arg.startswith("HEAD~"):
            try:
                n = int(range_arg[5:])
            except ValueError:
                n = 1
            head = repo.head.commit
            oldest = head
            for _ in range(n):
                if oldest.parents:
                    oldest = oldest.parents[0]
                else:
                    break
            return oldest.hexsha, head.hexsha

        # Single SHA
        commit = repo.commit(range_arg.strip())
        return commit.hexsha, commit.hexsha

    def _collect_shas_in_range(
        self, repo, oldest_sha: str, newest_sha: str
    ) -> list[str]:
        """Collect commit SHAs from newest to oldest (inclusive)."""
        if oldest_sha == newest_sha:
            return [newest_sha]
        shas: list[str] = []
        current = repo.commit(newest_sha)
        for _ in range(50):  # safety limit
            shas.append(current.hexsha)
            if current.hexsha == oldest_sha:
                break
            if not current.parents:
                break
            current = current.parents[0]
        return shas

    def _pre_apply_range(self, range_arg: str) -> None:
        """메인 스레드에서 즉시 SHA 범위를 설정한다 (race condition 방지).

        백그라운드 워커가 _resolve_range를 호출하기 전에 커밋 피커가 열려도
        올바른 S/E 마커가 표시되도록 미리 상태를 업데이트한다.
        코드뷰 갱신(_show_range_in_view)은 git I/O가 무거우므로 워커에 위임하고,
        여기서는 SHA + 선택 동기화만 수행한다.
        """
        try:
            oldest_sha, newest_sha = self._resolve_range(range_arg)
        except Exception:
            return  # 실패 시 워커가 다시 시도하며 오류를 표시함
        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha
        self._sync_commit_selection(oldest_sha, newest_sha)

    # ------------------------------------------------------------------
    # Phase 2: Persist app state (selected SHA range)
    # ------------------------------------------------------------------

    def _sync_commit_selection(self, oldest_sha: str, newest_sha: str) -> None:
        """SHA 범위에서 CommitSelection 인덱스를 역산해 동기화 (메인 스레드)."""
        sha_index = {c.get("sha", ""): i for i, c in enumerate(self._commits)}
        newest_idx = sha_index.get(newest_sha)
        oldest_idx = sha_index.get(oldest_sha)
        if newest_idx is None or oldest_idx is None:
            return
        if newest_idx == oldest_idx:
            self._commit_selection = CommitSelection(start_index=newest_idx)
        else:
            self._commit_selection = CommitSelection(
                start_index=newest_idx, end_index=oldest_idx
            )

    def _save_app_state(self) -> None:
        if self._repo_source == "local" and self._local_repo_root is None:
            return
        try:
            save_app_state(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                commit_mode="selected",
                difficulty="medium",
                quiz_style="mixed",
                request_text="",
                read_request_text="",
                basic_request_text="",
                inline_request_text="",
                grading_request_text="",
                selected_range_start_sha=self._oldest_sha,
                selected_range_end_sha=self._newest_sha,
                local_repo_root=self._local_repo_root,
            )
            # local 모드로 전환된 경우 global state 파일에도 repo_source="local" 기록
            # (그렇지 않으면 재시작 시 이전 github URL로 잘못 복원됨)
            if self._repo_source == "local":
                from ..tui.state import get_app_state_path
                import json as _json

                global_state_path = get_app_state_path(repo_source="github")
                try:
                    existing = _json.loads(
                        global_state_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    existing = {}
                existing["repo_source"] = "local"
                existing["github_repo_url"] = ""
                global_state_path.parent.mkdir(parents=True, exist_ok=True)
                global_state_path.write_text(
                    _json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Phase 4: Quiz session save / restore
    # ------------------------------------------------------------------

    def _reset_quiz_state(self) -> None:
        """퀴즈/답변/채점 상태를 초기화하고 코드뷰 퀴즈 블록을 제거한다."""
        self._questions = []
        self._answers = {}
        self._grades = []
        self._grading_summary = {}
        try:
            self.query_one("#cmd-bar", CommandBar).set_quiz_questions([])
        except Exception:
            pass
        self._known_files = {}
        self._current_q_index = 0
        self._set_mode("idle")
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(0, 0)
        except Exception:
            pass
        try:
            self.query_one("#code-view", InlineCodeView).clear_quiz()
        except Exception:
            pass
        self._sync_quiz_alert()

    def _session_id(self) -> str | None:
        if not self._oldest_sha or not self._newest_sha:
            return None
        return f"{self._oldest_sha[:7]}-{self._newest_sha[:7]}"

    def _save_session(self) -> None:
        sid = self._session_id()
        if not sid or not self._questions:
            return
        payload = {
            "session_id": sid,
            "oldest_sha": self._oldest_sha,
            "newest_sha": self._newest_sha,
            "questions": self._questions,
            "answers": self._answers,
            "grades": self._grades,
            "grading_summary": self._grading_summary,
            "session_meta": {
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        }
        try:
            save_learning_session_file(
                sid,
                payload,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass

    def _try_restore_session(self, replay_log: bool = True) -> bool:
        """Restore a saved quiz session for current SHA range. Returns True if restored."""
        sid = self._session_id()
        if not sid:
            return False

        # 챗 스레드 복원은 퀴즈 세션 여부와 무관하게 항상 시도
        self._restore_chat_thread(sid, replay_log=replay_log)

        try:
            payload = load_learning_session_file(
                sid,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            return False
        if not payload:
            return False

        questions = payload.get("questions", [])
        if not questions:
            return False

        # show_range를 questions 세팅 전에 먼저 호출.
        # questions가 빈 상태에서 _populate_tree → _render_code_only 경로를 타므로
        # _render_code_with_quiz → _focus_active_answer_or_scroll 가 실행되지 않음.
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=self._oldest_sha,
            newest_commit_sha=self._newest_sha,
            local_repo_root=self._local_repo_root,
        )

        self._questions = questions
        self._answers = payload.get("answers", {})
        self._grades = payload.get("grades", [])
        self._grading_summary = payload.get("grading_summary", {})
        self._current_q_index = 0
        try:
            self.query_one("#cmd-bar", CommandBar).set_quiz_questions(
                [q.get("question", "") for q in questions]
            )
        except Exception:
            pass

        # Restore known_files from questions if needed (not persisted — will be empty)
        self._known_files = {}

        # 코드뷰는 백그라운드에서 로드만 해두고 자동 전환하지 않음.
        # 퀴즈 미완료 시 Chat View에서 넛지 메시지로 안내한다.

        # 앱 이벤트 로그 + 채팅 스레드 복원은 _restore_chat_thread()에서 이미 처리됨

        if self._grades:
            self._set_mode("idle")
            self._restore_graded_view()
        elif self._answers:
            answered_count = len(self._answers)
            total = len(self._questions)
            if answered_count >= total:
                self._set_mode("idle")
                self._restore_answered_view()
            else:
                self._set_mode("idle")
                self._restore_answering_view()
        else:
            self._set_mode("idle")
            self._restore_answering_view()
        self._sync_quiz_alert()

        # 세션 복원 후 포커스를 명령창으로 복구
        # (load_inline_quiz가 TextArea 위젯을 마운트하며 포커스를 가져갈 수 있음)
        try:
            cmd_bar = self.query_one("#cmd-bar", CommandBar)
            self.call_after_refresh(
                lambda: self.call_after_refresh(cmd_bar.focus_input)
            )
            self.set_timer(0.3, cmd_bar.focus_input)
        except Exception:
            pass

        return True

    def _restore_chat_thread(self, sid: str, *, replay_log: bool = True) -> None:
        """재시작 시 thread_id 복원(없으면 신규 생성) + thread_log 재생."""
        try:
            threads_data = load_chat_threads(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
            current_tid = threads_data.get("current", "")
            if not current_tid:
                current_tid = self._create_new_thread(threads_data)
            self._current_thread_id = current_tid
        except Exception:
            pass

        # thread_log 재생 — 마지막 "cleared" 이후 이벤트만
        if not replay_log:
            return
        try:
            if not self._current_thread_id:
                return
            thread_log = load_thread_log(
                self._current_thread_id,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
            if thread_log:
                last_clear = max(
                    (i for i, e in enumerate(thread_log) if e.get("kind") == "cleared"),
                    default=-1,
                )
                hv = self.query_one("#history-view", HistoryView)
                self._replay_thread_log(hv, thread_log[last_clear + 1 :])

                # 복원 완료 후 명령창이 보이도록 스크롤 + 포커스
                def _scroll_to_cmdbar():
                    try:
                        sw = self.query_one("#scroll-wrapper")
                        sw.scroll_end(animate=False)
                    except Exception:
                        pass
                    try:
                        self.query_one("#cmd-bar", CommandBar).focus_input()
                    except Exception:
                        pass

                self.call_after_refresh(
                    lambda: self.call_after_refresh(_scroll_to_cmdbar)
                )
                self.set_timer(1.0, _scroll_to_cmdbar)
        except Exception:
            pass

    def _replay_thread_log(self, hv: HistoryView, events: list[dict]) -> None:
        """thread_log 이벤트를 HistoryView에 재생한다 (채팅 + UI 명령 이벤트 통합)."""
        block = None
        # 원본 block_id → 새로 생성된 블록 위젯 매핑 (비동기 완료 결과 복원용)
        block_id_map: dict[str, object] = {}
        for event in events:
            kind = event.get("kind", "")
            content = event.get("content", "")
            style = event.get("style", "info")
            orig_block_id = event.get("block_id", "")
            match kind:
                case "user_message":
                    block = hv.append_user_message(content)
                    # chat에서 실행된 action 결과들이 orig_block_id로 이 블록을 참조하므로 매핑
                    if orig_block_id:
                        block_id_map[orig_block_id] = block
                case "action_message":
                    # action_responder 확인 메시지 — user_message 블록에 append
                    target_block = (
                        block_id_map.get(orig_block_id, block)
                        if orig_block_id
                        else block
                    )
                    if content.strip():
                        hv.append_result(content, "assistant", block=target_block)
                case "assistant_message":
                    if content.strip():
                        hv.append_markdown(content, block=block)
                case "tool_call":
                    name = event.get("data", {}).get("name", content)
                    hv.append_tool_call(name, block=block)
                case "app_command" | "command":
                    block = hv.append_command(content)
                    # 원본 block_id가 있으면 새 블록과 매핑해둠
                    if orig_block_id:
                        block_id_map[orig_block_id] = block
                case "app_result" | "result":
                    target_block = (
                        block_id_map.get(orig_block_id, block)
                        if orig_block_id
                        else block
                    )
                    hv.append_result(content, style, block=target_block)
                case "app_markdown" | "markdown":
                    # block_id가 저장돼 있으면 매핑에서 원본 블록을 찾아 사용
                    target_block = (
                        block_id_map.get(orig_block_id, block)
                        if orig_block_id
                        else block
                    )
                    hv.append_markdown(content, block=target_block)
                case "app_map":
                    try:
                        cached = load_map_cache(
                            content,
                            repo_source=self._repo_source,
                            local_repo_root=self._local_repo_root,
                        )
                        if cached:
                            replay_block = (
                                block_id_map.get(orig_block_id)
                                if orig_block_id
                                else None
                            )
                            replay_block_id = (
                                replay_block.id if replay_block is not None else None
                            )
                            self._render_map(cached, replay_block_id, _replay=True)
                    except Exception:
                        pass
                case "app_token_usage":
                    try:
                        parts = content.split(",", 2)
                        inp, out = parts[0], parts[1]
                        mdl = parts[2] if len(parts) > 2 else None
                        target_block = (
                            block_id_map.get(orig_block_id, block)
                            if orig_block_id
                            else block
                        )
                        hv.append_token_usage(
                            target_block, int(inp), int(out), model_name=mdl
                        )
                    except Exception:
                        pass
                case "separator":
                    hv.append_separator(content)
                case "cleared":
                    pass  # 슬라이싱으로 처리되므로 여기에 도달하지 않음

    # ------------------------------------------------------------------
    # Chat mode
    # ------------------------------------------------------------------

    def _create_new_thread(self, threads_data: dict) -> str:
        """신규 thread_id를 생성해 threads_data에 추가하고 저장 후 반환."""
        tid = time.strftime("%Y%m%d%H%M%S")
        count = len(threads_data.get("threads", [])) + 1
        threads_data["current"] = tid
        threads_data.setdefault("threads", []).append(
            {
                "id": tid,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "label": f"대화 {count}",
            }
        )
        try:
            save_chat_threads(
                threads_data,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass
        return tid

    def _ensure_thread_id(self) -> str | None:
        """활성 thread_id를 반환. 없으면 새로 생성."""
        if self._current_thread_id:
            return self._current_thread_id
        threads_data = load_chat_threads(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            local_repo_root=self._local_repo_root,
        )
        tid = self._create_new_thread(threads_data)
        self._current_thread_id = tid
        return tid

    def _build_commit_context(self) -> str:
        """LLM 시스템 프롬프트용 커밋 컨텍스트 문자열 생성."""
        if not self._oldest_sha:
            return ""
        lines = [
            "당신은 Git 커밋을 분석하는 코드 학습 도우미입니다.",
            "사용자의 질문에 한국어로 답변하세요.",
            "필요하면 get_file_content 도구를 사용해 파일 내용을 확인하세요.",
            "",
        ]
        ctx_str = self._get_commit_context_str()
        if ctx_str:
            lines.append(ctx_str)
        return "\n".join(lines)

    def _get_commit_context_str(self) -> str:
        """커밋 diff + 파일 내용 문자열 반환. SHA가 같으면 캐시 재사용. 워커 스레드에서 호출."""
        if not self._oldest_sha:
            return ""
        newest = self._newest_sha or self._oldest_sha
        cache_key = (self._oldest_sha, newest)
        if self._chat_ctx_cache_key == cache_key and self._chat_ctx_cache_str:
            return self._chat_ctx_cache_str
        try:
            repo = get_repo(
                repo_source=self._repo_source,
                local_repo_root=self._local_repo_root,
                github_repo_url=self._github_repo_url,
                refresh_remote=False,
            )
            commits_list = list(self._commits)
            sha_to_idx = {c["sha"]: i for i, c in enumerate(commits_list)}
            oldest_idx = sha_to_idx.get(self._oldest_sha)
            newest_idx = sha_to_idx.get(newest)
            if oldest_idx is not None and newest_idx is not None:
                if newest_idx > oldest_idx:
                    newest_idx, oldest_idx = oldest_idx, newest_idx
                range_shas = [
                    c["sha"] for c in commits_list[newest_idx : oldest_idx + 1]
                ]
                range_commits = [repo.commit(sha) for sha in range_shas]
            else:
                range_commits = [repo.commit(newest)]
            if len(range_commits) == 1:
                ctx = build_commit_context(range_commits[0], "selected_commit", repo)
            else:
                ctx = build_multi_commit_context(range_commits, "selected_range", repo)
            lines = [
                f"커밋: {ctx.get('commit_sha', '')}",
                f"제목: {ctx.get('commit_subject', '')}",
                f"작성자: {ctx.get('commit_author', '')}",
                f"날짜: {ctx.get('commit_date', '')}",
                "",
                "── 변경 파일 ──",
                ctx.get("changed_files_summary", "(없음)"),
                "",
                "── Diff ──",
                ctx.get("diff_text", "(없음)"),
                "",
                "── 파일 컨텍스트 ──",
                ctx.get("file_context_text", "(없음)"),
            ]
            result = "\n".join(lines)
            self._chat_ctx_cache_key = cache_key
            self._chat_ctx_cache_str = result
            return result
        except Exception:
            return f"커밋 범위: {self._oldest_sha[:7]}..{newest[:7]}"

    def _build_commit_diff_context(self) -> str:
        """코드 리뷰어 에이전트용 커밋 범위 컨텍스트 문자열 생성."""
        return self._get_commit_context_str()

    def _build_quiz_context(self) -> str:
        """퀴즈 튜터 에이전트용 퀴즈 문항 컨텍스트 문자열 생성."""
        if not self._questions:
            return ""
        grade_map = {g["id"]: g for g in self._grades}
        lines = []
        for i, q in enumerate(self._questions, 1):
            file_info = f"{q.get('file_path', '')}:{q.get('anchor_line', '')}"
            lines.append(f"Q{i}. [{file_info}] {q.get('question', '')}")
            lines.append(f"    예상 답변: {q.get('expected_answer', '')}")
            grade = grade_map.get(q.get("id", ""))
            if grade:
                lines.append(f"    채점: {grade.get('score')}/10 — {grade.get('feedback', '')}")
        return "\n".join(lines)

    def _build_grade_context(self) -> dict | None:
        """학습 어드바이저용 채점 결과 컨텍스트."""
        if not self._grading_summary:
            return None
        return dict(self._grading_summary)

    @work(thread=True)
    def _start_chat(self, user_text: str, mentioned_files: tuple = (), mentioned_quizzes: tuple = ()) -> None:
        """채팅 메시지를 LLM으로 전송하고 스트리밍 응답을 표시."""
        if mentioned_quizzes:
            clean = re.sub(r'@\d+\s*', '', user_text).strip()
            quiz_parts = []
            for n in mentioned_quizzes:
                idx = n - 1
                if 0 <= idx < len(self._questions):
                    q = self._questions[idx]
                    file_path = q.get("file_path", "")
                    anchor_line = q.get("anchor_line", "")
                    loc = f"{file_path}:{anchor_line}" if anchor_line else file_path
                    # 채점 결과는 quiz_context(system)에 포함 — user_text에 중복 금지 (supervisor 오분류 방지)
                    quiz_parts.append(f"퀴즈 #{n} [{loc}]: {q.get('question', '')}")
            if quiz_parts:
                nums = ", ".join(f"#{n}" for n in mentioned_quizzes)
                quiz_ref = "\n".join(quiz_parts)
                if clean:
                    user_text = f"{clean}\n\n[참조 퀴즈 — {nums}에 대해서만 답해줘]\n{quiz_ref}"
                else:
                    user_text = f"퀴즈 {nums} 힌트 요청 — 해당 퀴즈에 대해서만 답해줘. 답을 직접 알려주지 말고 사고 방향만 안내해줘.\n{quiz_ref}"
        if not user_text.strip():
            return

        tid = self._ensure_thread_id()
        if not tid:
            return

        self.call_from_thread(self._set_mode, "chatting")
        self.call_from_thread(self._show_chat_view)

        # HistoryView에 유저 메시지 표시
        # streaming 위젯은 route 이벤트 후에 생성 (에이전트 라벨이 위에 오도록)
        # 블록 ID를 미리 생성 — action 이벤트 핸들러에서 DOM 조회 없이 바로 사용
        chat_block_id = f"hvchat-{uuid.uuid4().hex[:8]}"
        block_holder: list = []

        def _mount_user_msg():
            hv = self.query_one("#history-view", HistoryView)
            block = hv.append_user_message(user_text, block_id=chat_block_id)
            block_holder.append((hv, block, None))  # streaming은 나중에 설정

        self.call_from_thread(_mount_user_msg)

        # 유저 메시지 thread log에 저장
        try:
            append_thread_event(
                tid,
                {
                    "kind": "user_message",
                    "content": user_text,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "block_id": chat_block_id,  # 복원 시 block_id_map 복원용
                },
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass

        commit_context = self._build_commit_context()
        quiz_context = self._build_quiz_context()
        commit_diff_context = self._build_commit_diff_context()

        # @-mention 파일 내용 추출
        mentioned_snippets: dict[str, str] = {}
        # GitHub repo의 경우 _local_repo_root가 None → get_repo()로 실제 캐시 경로 확보
        effective_local_root: Path | None = self._local_repo_root
        if mentioned_files:
            try:
                from ..domain.code_context import get_file_content_at_commit_or_empty

                target_sha = self._newest_sha or self._oldest_sha
                repo = get_repo(
                    repo_source=self._repo_source,
                    github_repo_url=self._github_repo_url,
                    refresh_remote=False,
                    local_repo_root=self._local_repo_root,
                )
                if effective_local_root is None and repo is not None:
                    effective_local_root = Path(repo.working_dir)
                for file_path, start, end in mentioned_files:
                    if target_sha and repo is not None:
                        content = get_file_content_at_commit_or_empty(
                            repo, target_sha, file_path
                        )
                    elif effective_local_root is not None:
                        full_path = effective_local_root / file_path
                        content = (
                            full_path.read_text(errors="replace")
                            if full_path.exists()
                            else ""
                        )
                    else:
                        content = ""
                    if content:
                        if start > 0 and end > 0:
                            lines = content.splitlines()
                            snippet = "\n".join(lines[start - 1 : end])
                            key = f"{file_path}[{start}-{end}]"
                        else:
                            snippet = content
                            key = file_path
                        mentioned_snippets[key] = snippet
            except Exception:
                pass

        accumulated = ""

        def _ensure_streaming():
            """streaming 위젯이 없으면 생성. 메인 스레드에서 호출."""
            if not block_holder:
                return
            hv, block, streaming = block_holder[0]
            if streaming is None:
                sw = hv.begin_streaming(block)
                block_holder[0] = (hv, block, sw)

        for event in stream_chat(
            thread_id=tid,
            user_text=user_text,
            commit_context=commit_context,
            quiz_context=quiz_context,
            commit_diff_context=commit_diff_context,
            oldest_sha=self._oldest_sha,
            newest_sha=self._newest_sha,
            local_repo_root=effective_local_root,
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            mentioned_snippets=mentioned_snippets,
            grade_context=self._build_grade_context(),
        ):
            if self._stop_event.is_set():
                return
            if not block_holder:
                continue

            etype = event.get("type", "")
            if etype == "action":
                actions = event.get("actions", [])
                actions_args = event.get("actions_args", [])
                # chat_block_id는 _start_chat 시작 시 미리 생성 — 클로저 변수 재사용
                cmds = []
                for action, args in zip(actions, actions_args):
                    if action == "none":
                        continue
                    raw_cmd = f"/{action} {args}".strip()
                    cmds.append(parse_command(raw_cmd))
                if cmds:

                    def _do_enqueue(cs=cmds, bid=chat_block_id):
                        self._enqueue_actions(cs, bid)

                    self.call_from_thread(_do_enqueue)
            elif etype == "route":
                label = event.get("label", "")

                def _add_route_then_streaming(lbl=label):
                    if not block_holder:
                        return
                    hv, block, _ = block_holder[0]
                    if lbl:
                        hv.append_result(f"→ {lbl}", "info", block)
                    sw = hv.begin_streaming(block)
                    block_holder[0] = (hv, block, sw)

                self.call_from_thread(_add_route_then_streaming)
            elif etype == "action_message":
                # action_responder 확인 메시지 — 별도 블록으로 표시, streaming 위젯 리셋
                msg = event.get("content", "")

                def _show_action_msg(m=msg):
                    if not block_holder:
                        return
                    hv, block, streaming = block_holder[0]
                    # 기존 streaming 위젯이 있으면 종료 (빈 내용)
                    if streaming is not None:
                        hv.end_streaming(block, streaming, "")
                    hv.append_result(m, "assistant", block)
                    # streaming 리셋 → 이후 chat agent 토큰은 새 위젯에 시작
                    block_holder[0] = (hv, block, None)

                self.call_from_thread(_show_action_msg)
                accumulated = ""
                try:
                    append_thread_event(
                        tid,
                        {
                            "kind": "action_message",
                            "content": msg,
                            "block_id": chat_block_id,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                        repo_source=self._repo_source,
                        github_repo_url=self._github_repo_url or "",
                        local_repo_root=self._local_repo_root,
                    )
                except Exception:
                    pass
            elif etype == "token":
                accumulated += event.get("content", "")
                acc = accumulated

                def _update_token(a=acc):
                    _ensure_streaming()
                    if not block_holder:
                        return
                    hv, block, streaming = block_holder[0]
                    if streaming is not None:
                        hv.update_streaming(streaming, a)

                self.call_from_thread(_update_token)
            elif etype == "tool_call":
                name = event.get("name", "")

                def _add_tool(n=name):
                    if not block_holder:
                        return
                    hv, block, _ = block_holder[0]
                    hv.append_tool_call(n, block)

                self.call_from_thread(_add_tool)
                try:
                    append_thread_event(
                        tid,
                        {
                            "kind": "tool_call",
                            "content": name,
                            "data": {"name": name, "args": event.get("args", {})},
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                        repo_source=self._repo_source,
                        github_repo_url=self._github_repo_url or "",
                        local_repo_root=self._local_repo_root,
                    )
                except Exception:
                    pass
            elif etype == "done":
                full = event.get("full_content", accumulated)
                inp = event.get("input_tokens", 0)
                out = event.get("output_tokens", 0)

                mdl = event.get("model_name") or None

                def _finish(f=full, i=inp, o=out, m=mdl):
                    _ensure_streaming()
                    if not block_holder:
                        return
                    hv, block, streaming = block_holder[0]
                    if i or o:
                        # streaming 위젯 앞에 삽입 → 명령어 바로 아래 표시
                        streaming_widgets = list(block.query(".hv-assistant-streaming"))
                        first_streaming = (
                            streaming_widgets[0] if streaming_widgets else None
                        )
                        hv.append_token_usage(
                            block, i, o, insert_before=first_streaming, model_name=m
                        )
                        model_part = f",{m}" if m else ""
                        self._log_chat(
                            "app_token_usage",
                            f"{i},{o}{model_part}",
                            block_id=block.id if block else "",
                        )
                    if streaming is not None:
                        hv.end_streaming(block, streaming, f)

                self.call_from_thread(_finish)
                try:
                    append_thread_event(
                        tid,
                        {
                            "kind": "assistant_message",
                            "content": full,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                        repo_source=self._repo_source,
                        github_repo_url=self._github_repo_url or "",
                        local_repo_root=self._local_repo_root,
                    )
                except Exception:
                    pass
                self.call_from_thread(self._set_mode, "idle")
                self.call_from_thread(self._set_status_timed, "답변 완료.", 3.0)
                self.call_from_thread(self._append_result, "답변 완료.", "info")
                return
            elif etype == "error":
                err = event.get("content", "오류 발생")

                def _on_error():
                    if block_holder:
                        hv, block, streaming = block_holder[0]
                        if streaming is not None:
                            hv.end_streaming(block, streaming, "")

                self.call_from_thread(_on_error)
                self.call_from_thread(self._log, err, "error")
                self.call_from_thread(self._set_mode, "idle")
                return

    def _open_quiz_list(self) -> None:
        """/quiz list: 세션별 퀴즈 목록 모달 열기."""
        sessions = list_learning_sessions(
            repo_source=self._repo_source,
            local_repo_root=self._local_repo_root,
        )
        if not sessions:
            self._set_status_timed("저장된 퀴즈 세션이 없습니다.", 3.0)
            return

        current_sid = self._session_id() or ""

        def on_dismiss(result: str | None) -> None:
            if result == "switch":
                self._open_commit_picker()
                return
            if result and result != current_sid:
                self._load_session_by_id(result)

        self.push_screen(
            QuizListScreen(
                sessions,
                current_sid,
                repo_source=self._repo_source,
                local_repo_root=self._local_repo_root,
            ),
            on_dismiss,
        )

    def _load_session_by_id(self, session_id: str) -> None:
        """세션 ID로 퀴즈 세션 전환. oldest/newest_sha를 세션 파일에서 읽어 복원한다."""
        try:
            payload = load_learning_session_file(
                session_id,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            payload = None

        if not payload:
            self._set_status_timed(f"세션을 불러올 수 없습니다: {session_id}", 4.0)
            return

        oldest_sha = payload.get("oldest_sha", "")
        newest_sha = payload.get("newest_sha", "")
        if not oldest_sha or not newest_sha:
            self._set_status_timed("세션 SHA 정보가 없습니다.", 4.0)
            return

        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha

        # AppStatusBar 커밋 범위 갱신 (count 미보관이므로 0 전달 → 숫자 숨김)
        try:
            self.query_one("#app-status", AppStatusBar).set_range(oldest_sha, newest_sha, 0)
        except Exception:
            pass

        # 선택된 범위를 state 파일에 영구 저장
        self._save_app_state()

        self._try_restore_session(replay_log=False)
        self._set_status_timed(f"세션 복원: {session_id}", 3.0)

    def _handle_quiz_clear(self) -> None:
        """/quiz clear: 현재 범위 퀴즈 세션 파일 삭제 + 인메모리 상태 초기화."""
        sid = self._session_id()
        if not sid:
            self._set_status_timed("커밋 범위가 선택되지 않았습니다.", 4.0)
            return
        if self._mode in ("quiz_loading", "grading", "chatting"):
            self._set_status_timed("진행 중인 작업이 있어 삭제할 수 없습니다.", 4.0)
            return
        deleted = delete_learning_session_file(
            sid,
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            local_repo_root=self._local_repo_root,
        )
        # 인메모리 상태 초기화
        self._questions = []
        self._answers = {}
        self._grades = []
        self._grading_summary = {}
        self._current_q_index = 0
        try:
            self.query_one("#cmd-bar", CommandBar).set_quiz_questions([])
        except Exception:
            pass
        if self._mode == "quiz_answering":
            self._set_mode("idle")
        # InlineCodeView 퀴즈 블록 제거
        try:
            self.query_one("#code-view", InlineCodeView).clear_quiz()
        except Exception:
            pass
        self._sync_quiz_alert()
        if deleted:
            self._set_status_dismissable(
                f"퀴즈 세션이 삭제됐습니다. ({sid})  [dim]ESC 닫기[/dim]"
            )
        else:
            self._set_status_timed("삭제할 퀴즈 세션이 없습니다.", 4.0)

    def _handle_quiz_retry(self) -> None:
        """/quiz retry: 답변·채점 초기화하고 Q1부터 다시 답변 모드 진입. 문제는 유지."""
        if not self._questions:
            self._set_status_timed("퀴즈가 없습니다. /quiz 로 먼저 생성하세요.", 4.0)
            return
        if self._mode in ("quiz_loading", "grading", "chatting"):
            self._set_status_timed("진행 중인 작업이 있어 재시작할 수 없습니다.", 4.0)
            return

        self._answers = {}
        self._grades = []
        self._grading_summary = {}
        self._current_q_index = 0
        self._quiz_retried = True

        try:
            code_view = self.query_one("#code-view", InlineCodeView)
            code_view.load_inline_quiz(
                questions=self._questions,
                answers=self._answers,
                grades=self._grades,
                known_files=self._known_files,
                current_index=0,
            )
        except Exception:
            pass

        self._save_session()
        self._set_mode("quiz_answering")
        self._sync_quiz_alert()
        self._set_status_timed("답변·채점 초기화 완료 — Q1부터 다시 시작합니다.", 3.0)

    def _handle_clear(self) -> None:
        """/clear: 새 thread_id 생성 + 화면 초기화. 이전 대화는 /resume 으로 복원 가능."""
        try:
            threads_data = load_chat_threads(
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
            new_tid = self._create_new_thread(threads_data)
            self._current_thread_id = new_tid
        except Exception:
            self._current_thread_id = time.strftime("%Y%m%d%H%M%S")
        self._clear_history_view()
        self._set_status_dismissable(
            "대화가 초기화됐습니다. /resume 으로 이전 대화를 불러올 수 있습니다.  [dim]ESC 닫기[/dim]"
        )

    def _handle_resume(self) -> None:
        """/resume: 이전 대화 목록 모달 표시 후 선택한 thread로 전환."""
        threads_data = load_chat_threads(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            local_repo_root=self._local_repo_root,
        )
        threads = threads_data.get("threads", [])
        if not threads:
            self._set_status_timed("이전 대화가 없습니다.", 5.0)
            self._append_result("이전 대화가 없습니다.", "error")
            return

        # msg_count + 미리보기 추가
        for t in threads:
            msg_count, previews = self._get_thread_summary(t["id"])
            t["msg_count"] = msg_count
            t["previews"] = previews

        self.push_screen(
            ThreadPickerScreen(threads, self._current_thread_id),
            callback=self._on_thread_picker_result,
        )

    def _on_thread_picker_result(self, result: dict | None) -> None:
        if result is None:
            self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)
            return
        threads_data = load_chat_threads(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            local_repo_root=self._local_repo_root,
        )
        self._current_thread_id = result["id"]
        threads_data["current"] = result["id"]
        try:
            save_chat_threads(
                threads_data,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass

        thread_log = load_thread_log(
            result["id"],
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            local_repo_root=self._local_repo_root,
        )
        self._clear_history_view()
        try:
            hv = self.query_one("#history-view", HistoryView)
            self._replay_thread_log(hv, thread_log)
            hv.append_separator(f"─── {result['label']} 재개 ───")
        except Exception:
            pass
        self._set_status_timed(f"{result['label']} 재개됨.", 3.0)
        self._append_result(f"{result['label']} 재개됨.", "info")
        self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)

    def _get_thread_summary(self, thread_id: str) -> tuple[int, list[str]]:
        """thread log에서 (user_message 수, 최근 2개 미리보기) 반환."""
        try:
            log = load_thread_log(
                thread_id,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
            user_msgs = [e for e in log if e.get("kind") == "user_message"]
            count = len(user_msgs)
            previews = []
            for e in user_msgs[:2]:
                raw = e.get("content", "").strip().replace("\n", " ")
                previews.append(raw[:60] + ("…" if len(raw) > 60 else ""))
            return count, previews
        except Exception:
            return 0, []

    def _restore_graded_view(self) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.load_inline_quiz(
            questions=self._questions,
            answers=self._answers,
            grades=self._grades,
            known_files=self._known_files,
            current_index=0,
        )
        scores = [g.get("score", 0) for g in self._grades]
        avg = sum(scores) / len(scores) if scores else 0
        self._set_status(
            f"이전 세션 복원됨. 채점 완료 — 평균 {avg:.1f}/100 ({len(self._grades)}문제). "
            "/quiz 로 새 퀴즈 생성."
        )

    def _restore_answered_view(self) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.load_inline_quiz(
            questions=self._questions,
            answers=self._answers,
            grades=self._grades,
            known_files=self._known_files,
            current_index=0,
        )

    def _restore_answering_view(self) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.load_inline_quiz(
            questions=self._questions,
            answers=self._answers,
            grades=self._grades,
            known_files=self._known_files,
            current_index=self._current_q_index,
            focus_answer=False,  # 복원 시 Chat View 유지 — 명령창 포커스
        )
        # 복원 시에는 답변 힌트 대신 context hint 유지 (_update_answer_status 호출 안 함)
        # quiz_progress는 실제 답변 진입(AnswerEntered) 시에만 설정

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _repo_display_name(self) -> str:
        if self._repo_source == "github" and self._github_repo_url:
            return (
                self._github_repo_url.split("github.com/")[-1]
                .rstrip("/")
                .removesuffix(".git")
            )
        if self._local_repo_root:
            return self._local_repo_root.name
        return "unknown"

    def _mode_bar_text(self, code_active: bool = False) -> RichText:
        t = RichText()
        if code_active:
            t.append("Chat", style="dim")
            t.append(" ◀ ", style="bold white")
            t.append("[ Code/Quiz ● ]", style="bold color(99)")
        else:
            t.append("[ Chat ● ]", style="bold green")
            t.append(" ▶ ", style="bold white")
            t.append("Code/Quiz", style="dim")
        t.append("    Shift+Tab", style="bold white")
        t.append(" to switch", style="dim")
        return t

    def _update_mode_bar(self) -> None:
        code_active = self._is_code_view_active()
        try:
            self.query_one("#mode-bar", Static).update(self._mode_bar_text(code_active))
        except Exception:
            pass

    def _show_code_view(self) -> None:
        """Switch to code view."""
        try:
            self.screen.add_class("-code-active")
        except Exception:
            pass
        self._update_mode_bar()

    def _clear_history_view(self) -> None:
        try:
            self.query_one("#history-view", HistoryView).clear()
        except Exception:
            pass

    def _show_chat_view(self) -> None:
        """Switch to chat mode."""
        try:
            self.screen.remove_class("-code-active")
        except Exception:
            pass
        self._update_mode_bar()

    def _is_code_view_active(self) -> bool:
        try:
            return self.screen.has_class("-code-active")
        except Exception:
            return False

    def action_global_tab(self) -> None:
        """App-level Tab: code view → panel cycle (file-tree→code-scroll→cmd-bar), chat mode → scroll+focus cmd-bar."""
        # CommitPickerScreen이 열려 있으면 선택 해제에 Tab을 위임
        if isinstance(self.screen, CommitPickerScreen):
            self.screen.action_clear_selection()
            return
        # QuizListScreen이 열려 있으면 패널 전환에 Tab을 위임
        if isinstance(self.screen, QuizListScreen):
            self.screen.action_switch_panel()
            return
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        if cmd_bar._ac_candidates:
            cmd_bar.action_tab_pressed()
            return
        if self._is_code_view_active():
            focused = self.focused
            focused_id = getattr(focused, "id", None) if focused else None
            if focused is not None and focused.has_class("iqb-input"):
                # 퀴즈 답변창 → 명령창 (quiz textarea는 로테이션에서 제외)
                try:
                    self.query_one("#cmd-bar", CommandBar).focus_input()
                except Exception:
                    pass
                return
            if focused_id == "code-scroll":
                # 우측 패널 → 명령창 (iqb-input 건너뜀)
                try:
                    self.query_one("#cmd-bar", CommandBar).focus_input()
                except Exception:
                    pass
                return
            if focused_id == "cb-input":
                # 명령창 → 좌측 패널
                try:
                    self.query_one("#file-tree").focus()
                except Exception:
                    self.action_focus_next()
                return
            # 좌측 패널 → 우측 패널 (기본 focus_next)
            self.action_focus_next()
            return
        # 챗 모드: 스크롤 끝 + 명령창 포커스
        self.handle_tab_no_autocomplete()

    def handle_tab_no_autocomplete(self) -> None:
        """챗 모드 Tab: 히스토리 최하단 스크롤 + cmd-bar 포커스."""
        # 스크롤을 먼저 — focus_input()이 레이아웃 갱신을 유발해 스크롤을 덮어쓰는 것을 방지
        try:
            self.query_one("#scroll-wrapper").scroll_end(animate=False)
        except Exception:
            pass
        # 이미 포커스가 있으면 재포커스 생략 (레이아웃 갱신 최소화)
        focused = self.focused
        if focused is None or getattr(focused, "id", None) != "cb-input":
            self.query_one("#cmd-bar", CommandBar).focus_input()

    def _chat_scroll(self, method: str, **kwargs) -> None:
        """챗 모드일 때 #scroll-wrapper를 스크롤."""
        if self._is_code_view_active():
            return
        try:
            sw = self.query_one("#scroll-wrapper")
            getattr(sw, method)(animate=False, **kwargs)
        except Exception:
            pass

    def check_action(self, action: str, _parameters: tuple) -> bool | None:
        """코드 패널 포커스 시 chat_scroll_page_* 바인딩을 비활성화해 pageup/pagedown이 코드뷰로 전달되도록."""
        if action in ("chat_scroll_page_up", "chat_scroll_page_down"):
            from .widgets.inline_code_view import CodePane

            try:
                if self.focused is self.query_one("#code-pane", CodePane):
                    return False
            except Exception:
                pass
        return True

    def action_chat_scroll_page_up(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self._chat_scroll("scroll_page_up")

    def action_chat_scroll_page_down(self) -> None:
        if len(self.screen_stack) > 1:
            return
        self._chat_scroll("scroll_page_down")

    def _has_code_content(self) -> bool:
        """Return True if commits have been selected and code view has content."""
        return bool(self._oldest_sha and self._newest_sha)

    def action_toggle_view(self) -> None:
        """Shift+Tab: toggle between Chat mode and Code View."""
        # 모달 피커 간 전환
        if isinstance(self.screen, CommitPickerScreen):
            self.screen.action_switch_screen()
            return
        if isinstance(self.screen, QuizListScreen):
            self.screen.action_switch_screen()
            return
        focused = self.focused
        if focused is not None and focused.has_class("iqb-input"):
            # Shift+Tab: 답변 모드 해제 + Chat View로 전환.
            try:
                focused.action_escape_answer(via_shift_tab=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            return
        if self._is_code_view_active():
            if self._mode == "quiz_answering":
                self._set_mode("idle")
                try:
                    self.query_one(
                        "#code-view", InlineCodeView
                    )._refresh_quiz_blocks_state()
                except Exception:
                    pass
            self._show_chat_view()
        else:
            if self._has_code_content():
                self._show_code_view()
            else:
                self._set_status_timed(
                    "선택된 커밋이 없습니다. /commits 로 커밋을 선택하세요.", 5.0
                )
                self._append_result(
                    "선택된 커밋이 없습니다. /commits 로 커밋을 선택하세요.", "error"
                )
        self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)

    def _log_chat(
        self, kind: str, content: str, style: str = "", block_id: str = ""
    ) -> None:
        """thread_log.jsonl에 UI 이벤트를 기록한다 (thread_id 미확정 시 무시)."""
        tid = self._current_thread_id
        if not tid:
            return
        event: dict = {
            "kind": kind,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if style:
            event["style"] = style
        if block_id:
            event["block_id"] = block_id
        try:
            append_thread_event(
                tid,
                event,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass

    def _log_command(self, cmd: str) -> None:
        """Append a command row to history and set as current block for results."""
        try:
            hv = self.query_one("#history-view", HistoryView)
            self._current_log_block = hv.append_command(cmd)
        except Exception:
            pass
        block_id = self._current_log_block.id if self._current_log_block else ""
        self._log_chat("app_command", cmd, block_id=block_id)

    def _log(self, text: str, style: str = "info") -> None:
        """Append a result row. If there's a current command block, attaches there; otherwise standalone."""
        try:
            hv = self.query_one("#history-view", HistoryView)
            if self._current_log_block is not None:
                hv.append_result(text, style, block=self._current_log_block)
            else:
                hv.append(text, style)
        except Exception:
            pass
        self._log_chat("app_result", text, style)

    def _sync_quiz_alert(self) -> None:
        """퀴즈 미완료 상태에 따라 CommandBar 왼쪽 알림 영역을 갱신한다."""
        try:
            cb = self.query_one("#cmd-bar", CommandBar)
            focused = self.focused
            in_answer_input = focused is not None and focused.has_class("iqb-input")
            if (
                self._questions
                and len(self._answers) >= len(self._questions)
                and not self._grades
            ):
                cb.set_quiz_alert("답변 완료!", "/grade 로 채점하세요")
            elif (
                not in_answer_input
                and self._mode != "quiz_answering"
                and self._questions
                and len(self._answers) < len(self._questions)
                and not self._grades
            ):
                cb.set_quiz_alert("퀴즈▶▶", "/answer, Shift+↑↓")
            elif self._grades:
                cb.set_quiz_alert("채점 완료!", "결과 보기   Shift+↑↓")
            else:
                cb.clear_quiz_alert()
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            cmd_bar = self.query_one("#cmd-bar", CommandBar)
            cmd_bar.status_text = text
            cmd_bar._context_hint = text  # 타이머 만료 후 이 힌트로 복원
        except Exception:
            pass

    def _set_status_timed(self, text: str, timeout: float = 4.0) -> None:
        try:
            self.query_one("#cmd-bar", CommandBar).set_status_timed(text, timeout)
        except Exception:
            pass

    def _set_status_dismissable(self, text: str) -> None:
        try:
            self.query_one("#cmd-bar", CommandBar).set_status_dismissable(text)
        except Exception:
            pass

    def _append_result(
        self, text: "str | RichText", style: str = "info", op_id: str | None = None
    ) -> None:
        tag = self._take_elapsed_tag(op_id)
        display = (text + tag) if isinstance(text, str) else text
        try:
            self.query_one("#history-view", HistoryView).append_result(
                display, style=style
            )
        except Exception:
            pass
        if isinstance(text, str):
            self._log_chat("app_result", text + tag, style)

    def _update_hook_status(self) -> None:
        """AppStatusBar의 hook 설치 여부 표시 갱신."""
        try:
            if self._repo_source == "github":
                installed: bool | None = False  # GitHub 모드: hook 설치 불가
            elif self._local_repo_root:
                hook_path = self._local_repo_root / ".git" / "hooks" / "post-commit"
                installed = hook_path.exists() and _has_hook(hook_path.read_text())
            else:
                installed = None
            self.query_one("#app-status", AppStatusBar).set_hook(installed)
        except Exception:
            pass

    def _begin_progress(
        self, text: str, block_id: str | None = None, op_id: str = ""
    ) -> None:
        """커맨드 블록 아래에 스피너 로딩 위젯 표시. block_id 지정 시 해당 블록에 붙임."""
        try:
            hv = self.query_one("#history-view", HistoryView)
            block = None
            if block_id:
                try:
                    block = hv.query_one(f"#{block_id}")
                except Exception:
                    pass
            if block is None:
                block = self._current_log_block
            row = hv.begin_progress(text, block=block)
            if op_id:
                self._progress_rows[op_id] = row
        except Exception:
            pass
        try:
            self.query_one("#cmd-bar", CommandBar).set_progress(op_id, text)
        except Exception:
            pass

    def _append_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        block_id: str | None = None,
        model_name: str | None = None,
    ) -> None:
        """토큰 사용량을 히스토리뷰에 표시 (메인 스레드에서 호출)."""
        try:
            hv = self.query_one("#history-view", HistoryView)
            block = None
            if block_id:
                try:
                    block = hv.query_one(f"#{block_id}")
                except Exception:
                    pass
            hv.append_token_usage(
                block, input_tokens, output_tokens, model_name=model_name
            )
            model_part = f",{model_name}" if model_name else ""
            self._log_chat(
                "app_token_usage",
                f"{input_tokens},{output_tokens}{model_part}",
                block_id=block_id or "",
            )
        except Exception:
            pass

    def _log_to_block(
        self,
        text: str,
        style: str,
        block_id: str | None = None,
        op_id: str | None = None,
    ) -> None:
        """block_id 블록에 결과 row 추가. 없으면 _log() 폴백."""
        text = text + self._take_elapsed_tag(op_id)
        if block_id:
            try:
                hv = self.query_one("#history-view", HistoryView)
                block = hv.query_one(f"#{block_id}")
                hv.append_result(text, style, block=block)
                self._log_chat("app_result", text, style, block_id=block_id or "")
                return
            except Exception:
                pass
        self._log(text, style)

    def _update_progress(self, text: str, op_id: str = "") -> None:
        """로딩 위젯 텍스트 업데이트."""
        try:
            row = self._progress_rows.get(op_id)
            if row is not None:
                row.set_text(text)
        except Exception:
            pass
        try:
            self.query_one("#cmd-bar", CommandBar).set_progress(op_id, text)
        except Exception:
            pass

    def _end_progress(self, op_id: str = "") -> None:
        """로딩 위젯 제거. 경과 시간을 _progress_elapsed에 저장."""
        try:
            hv = self.query_one("#history-view", HistoryView)
            row = self._progress_rows.pop(op_id, None)
            if row is not None:
                self._progress_elapsed[op_id] = row.elapsed_seconds
                hv.end_progress(row)
        except Exception:
            pass
        try:
            self.query_one("#cmd-bar", CommandBar).set_progress(op_id, None)
        except Exception:
            pass

    def _take_elapsed_tag(self, op_id: str | None = None) -> str:
        """저장된 경과 시간을 '(43s)' 형태 문자열로 반환하고 초기화."""
        if op_id is None:
            return ""
        elapsed = self._progress_elapsed.pop(op_id, None)
        if elapsed is None:
            return ""
        m, s = divmod(elapsed, 60)
        if m > 0:
            return f" ({m}m {s:02d}s)"
        return f" ({elapsed}s)"

    def _set_mode(self, mode: str) -> None:
        """Set internal mode and sync AppStatusBar."""
        self._mode = mode
        try:
            self.query_one("#app-status", AppStatusBar).set_mode(mode)
        except Exception:
            pass
        self._update_context_hint()
        # quiz_answering 진입 시는 on_focus(iqb-input)가 처리 — 이중 호출 방지
        if mode != "quiz_answering":
            self.call_after_refresh(self._sync_quiz_alert)
        # 큐에 대기 중인 액션이 있으면 idle 진입 시 처리
        if mode == "idle" and self._action_queue:
            self.call_after_refresh(self._process_action_queue)

    def _update_queue_status(self) -> None:
        """AppStatusBar 큐 상태 갱신. 메인 스레드에서 호출."""
        try:
            sb = self.query_one("#app-status", AppStatusBar)
            if self._action_queue:
                labels = " → ".join(f"/{c.kind}" for c in self._action_queue)
                sb.set_queue(f"[큐: {labels}]")
            else:
                sb.set_queue("")
        except Exception:
            pass

    def _process_action_queue(self) -> None:
        """큐에서 다음 액션 꺼내 실행. 메인 스레드에서 호출."""
        if not self._action_queue:
            self._update_queue_status()
            return
        # mode가 바쁘면 대기 (idle 또는 chatting일 때만 실행)
        if self._mode not in ("idle", "chatting"):
            return
        cmd = self._action_queue.pop(0)
        self._update_queue_status()
        bid = self._action_queue_block_id
        # 실행 헤더 표시
        cmd_str = f"/{cmd.kind}"
        if cmd.range_arg:
            cmd_str += f" {cmd.range_arg}"
        try:
            hv = self.query_one("#history-view", HistoryView)
            block = hv.query_one(f"#{bid}") if bid else None
            hv.append_result(f"▶ {cmd_str}", "info", block=block)
        except Exception:
            pass
        # 실행
        match cmd.kind:
            case "quiz":
                self._start_quiz(
                    cmd.range_arg,
                    log_block_id=bid,
                    count=cmd.quiz_count,
                    author_context=cmd.author_context,
                )
            case "review":
                self._start_review(cmd.range_arg, log_block_id=bid)
            case "grade":
                self._start_grading(log_block_id=bid)
            case "map":
                self._start_map(
                    cmd.range_arg,
                    refresh=cmd.refresh,
                    log_block_id=bid,
                )

    def _enqueue_actions(self, cmds: list, block_id: str | None) -> None:
        """액션 목록을 큐에 추가. 메인 스레드에서 호출."""
        self._action_queue.extend(cmds)
        self._action_queue_block_id = block_id
        # 여러 개면 예약 행 표시
        if len(cmds) > 1:
            labels = " → ".join(
                ("/" + c.kind + (f" {c.range_arg}" if c.range_arg else ""))
                for c in cmds
            )
            try:
                hv = self.query_one("#history-view", HistoryView)
                block = hv.query_one(f"#{block_id}") if block_id else None
                hv.append_result(f"◆ 예약: {labels}", "info", block=block)
            except Exception:
                pass
        self._update_queue_status()
        self._process_action_queue()

    def _update_answer_status(self) -> None:
        hint = "[bold cyan]Enter[/bold cyan] 답변 입력  [bold cyan]Shift+Enter[/bold cyan] 줄바꿈  [bold cyan]Shift+↑↓[/bold cyan] 이동  [bold cyan]F1[/bold cyan] 힌트  [dim]Esc 종료[/dim]"
        self._set_status(hint)


_HOOK_BEGIN = "# BEGIN git-study-hook"
_HOOK_END = "# END git-study-hook"


_TERMINAL_ALIASES: dict[str, str] = {
    "terminal": "Terminal",
    "terminal.app": "Terminal",
    "iterm": "iTerm2",
    "iterm2": "iTerm2",
    "warp": "Warp",
}


def _build_osascript(app_name: str, cmd: str) -> str:
    """터미널 앱별 osascript 명령 생성."""
    if app_name == "iTerm2":
        # create window → 인터랙티브 세션에 명령 입력 (command 파라미터는 종료 시 창 닫힘)
        return (
            f'osascript -e \'tell application "iTerm2" to tell (create window with default profile)'
            f' to tell current session to write text "{cmd}"\''
        )
    # Terminal.app, Warp 등 do script 방식
    return f'osascript -e \'tell application "{app_name}" to do script "{cmd}"\''


def _build_hook_block(repo_path: Path, terminal: str = "auto") -> str:
    """post-commit hook 스크립트 블록 생성."""
    # AppleScript 문자열 내부용: " → \"
    as_path = str(repo_path).replace('"', '\\"')
    # shell 단일따옴표 문자열 내부용: ' → '\''
    as_path_shell = as_path.replace("'", "'\\''")
    shell_cmd = f'cd \\"{as_path_shell}\\" && git-study --auto-quiz HEAD'

    if terminal.lower() in ("", "auto"):
        # 실행 시점에 현재 떠 있는 터미널 자동 감지
        iterm_cmd = _build_osascript("iTerm2", shell_cmd)
        warp_cmd = _build_osascript("Warp", shell_cmd)
        term_cmd = _build_osascript("Terminal", shell_cmd)
        mac_block = (
            f'  if pgrep -x "iTerm2" > /dev/null 2>&1; then\n'
            f"    {iterm_cmd} &\n"
            f'  elif pgrep -x "Warp" > /dev/null 2>&1; then\n'
            f"    {warp_cmd} &\n"
            f"  else\n"
            f"    {term_cmd} &\n"
            f"  fi"
        )
    else:
        app_name = _TERMINAL_ALIASES.get(terminal.lower(), terminal)
        mac_block = f"  {_build_osascript(app_name, shell_cmd)} &"

    return (
        f"{_HOOK_BEGIN}\n"
        f"if command -v osascript > /dev/null 2>&1; then\n"
        f"{mac_block}\n"
        f"else\n"
        f'  [ -t 1 ] && git-study --auto-quiz HEAD "{as_path}"\n'
        f"fi\n"
        f"{_HOOK_END}\n"
    )


def _has_hook(content: str) -> bool:
    return _HOOK_BEGIN in content or "git-study-v2" in content


def _strip_hook(content: str) -> str:
    """BEGIN/END 마커 블록 및 구버전 단일 라인 형식 제거."""
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    inside = False
    for line in lines:
        if _HOOK_BEGIN in line:
            inside = True
            continue
        if _HOOK_END in line:
            inside = False
            continue
        if inside:
            continue
        # 구버전 단일 라인 형식 제거
        if "git-study-v2" in line or "git-study: auto quiz" in line:
            continue
        result.append(line)
    return "".join(result).rstrip("\n")


def _parse_terminal_arg(argv: list[str]) -> str:
    """--terminal NAME 파싱. 없으면 'auto' 반환."""
    for i, a in enumerate(argv):
        if a == "--terminal" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--terminal="):
            return a.split("=", 1)[1]
    return "auto"


def _install_hook_command(argv: list[str]) -> None:
    """git-study-v2 install-hook [path] [--terminal NAME] [--force]"""
    import stat

    force = "--force" in argv
    terminal = _parse_terminal_arg(argv)

    # --terminal NAME 값과 플래그를 제외한 위치 인자만 추출
    skip_next = False
    path_args = []
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a == "--terminal":
            skip_next = True
            continue
        if a.startswith("--"):
            continue
        path_args.append(a)

    repo_path = Path(path_args[0]).expanduser().resolve() if path_args else Path.cwd()

    git_dir = repo_path / ".git"
    if not git_dir.is_dir():
        print(f"오류: {repo_path} 는 git 저장소가 아닙니다.")
        raise SystemExit(1)

    hook_path = git_dir / "hooks" / "post-commit"
    block = _build_hook_block(repo_path, terminal)

    if hook_path.exists():
        content = hook_path.read_text()
        if _has_hook(content):
            # 기존 블록 제거 후 새 블록으로 교체
            stripped = _strip_hook(content)
            base = (
                stripped if stripped.strip() not in ("", "#!/bin/sh") else "#!/bin/sh"
            )
            hook_path.write_text(base.rstrip("\n") + "\n" + block)
            print(f"✓ git-study hook 업데이트 완료: {hook_path}")
        else:
            with hook_path.open("a") as f:
                f.write(f"\n{block}")
            print(f"✓ git-study hook 을 기존 post-commit 에 추가했습니다: {hook_path}")
    else:
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(f"#!/bin/sh\n{block}")
        print(f"✓ git-study hook 설치 완료: {hook_path}")

    hook_path.chmod(
        hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )


def _uninstall_hook_command(argv: list[str]) -> None:
    """git-study-v2 uninstall-hook [path]"""
    path_args = [a for a in argv if not a.startswith("--")]
    repo_path = Path(path_args[0]).expanduser().resolve() if path_args else Path.cwd()

    git_dir = repo_path / ".git"
    if not git_dir.is_dir():
        print(f"오류: {repo_path} 는 git 저장소가 아닙니다.")
        raise SystemExit(1)

    hook_path = git_dir / "hooks" / "post-commit"
    if not hook_path.exists():
        print("post-commit hook 파일이 없습니다.")
        return

    content = hook_path.read_text()
    if not _has_hook(content):
        print("git-study hook 이 설치되어 있지 않습니다.")
        return

    new_content = _strip_hook(content)
    if new_content.strip() in ("", "#!/bin/sh"):
        hook_path.unlink()
        print(f"✓ hook 제거 완료 (파일 삭제): {hook_path}")
    else:
        hook_path.write_text(new_content + "\n")
        print(f"✓ hook 제거 완료 (기존 내용 보존): {hook_path}")


def run_v2() -> None:
    """Entry point for git-study-v2."""
    import argparse
    import sys
    from dotenv import load_dotenv

    # install-hook / uninstall-hook 서브커맨드는 argparse 전에 처리
    if len(sys.argv) > 1 and sys.argv[1] == "install-hook":
        _install_hook_command(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall-hook":
        _uninstall_hook_command(sys.argv[2:])
        return

    load_dotenv()
    from importlib.metadata import version as _pkg_version, PackageNotFoundError

    try:
        _version = _pkg_version("git-study")
    except PackageNotFoundError:
        _version = "unknown"
    parser = argparse.ArgumentParser(
        prog="git-study-v2",
        description=f"git-study v{_version} — Git 커밋을 인터랙티브 퀴즈로 학습하는 TUI 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
서브커맨드:
  install-hook [path] [--terminal NAME] [--force]   현재(또는 지정) 저장소에 post-commit hook 설치
                                        (NAME: terminal, iterm2, warp. 기본값: terminal)
  uninstall-hook [path]           post-commit hook 제거

TUI 명령어 (앱 실행 후):
  /commits              커밋 범위 선택
  /quiz [범위]          퀴즈 생성  (예: /quiz HEAD~3)
  /grade                채점
  /review [범위]        커밋 해설 보기
  /answer               답변 재진입
  /hook on              현재 저장소에 post-commit hook 설치
  /hook off             post-commit hook 제거
  /repo [URL|경로]      저장소 전환
  /model [이름]         모델 변경
  /apikey [key]         OpenAI API key 설정
  /clear                대화 초기화
  /resume               이전 대화 불러오기
  /help                 도움말
  /exit                 종료
  
""",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Git 저장소 경로 (기본값: 현재 디렉토리)",
    )
    parser.add_argument(
        "--auto-quiz",
        nargs="?",
        const="HEAD",
        default=None,
        metavar="RANGE",
        help="앱 시작 시 자동으로 퀴즈 생성 (예: HEAD, HEAD~3, HEAD~2..HEAD). 값 없이 사용하면 HEAD 기본값.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_version}",
    )
    args = parser.parse_args()
    repo_path = Path(args.path).expanduser().resolve() if args.path else None
    app = GitStudyAppV2(repo_path=repo_path, auto_quiz_arg=args.auto_quiz)
    result = app.run()
    # 정상 반환 시 데몬 스레드의 중복 출력 방지
    app._exit_printed.set()
    if result:
        print(result)

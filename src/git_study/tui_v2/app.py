"""GitStudyAppV2: inline-quiz-first TUI with command bar."""

from __future__ import annotations

import time
from pathlib import Path

from textual import work
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from ..domain.code_context import get_commit_parent_sha
from ..domain.inline_anchor import parse_file_context_blocks
from ..domain.repo_cache import normalize_github_repo_url
from ..domain.repo_context import (
    DEFAULT_COMMIT_LIST_LIMIT,
    build_commit_context,
    build_multi_commit_context,
    get_commit_list_snapshot,
    get_repo,
)
from ..secrets import get_openai_api_key, get_secrets_path, save_openai_api_key
from ..services.inline_grade_service import stream_inline_grade_progress
from ..services.inline_quiz_service import stream_inline_quiz_progress
from ..services.read_service import stream_read_progress
from ..tui.commit_selection import CommitSelection, selected_commit_indices
from ..tui.state import (
    append_thread_event,
    find_local_repo_root,
    load_app_state,
    load_chat_threads,
    load_learning_session_file,
    load_thread_log,
    save_app_state,
    save_chat_threads,
    save_learning_session_file,
)
from ..services.chat_service import stream_chat
from ..types import InlineQuizGrade, InlineQuizQuestion

from .commands import parse_command
from .screens import CommitPickerScreen, RepoPickerScreen, ThreadPickerScreen
from .screens.repo_picker import save_recent_local_repo
from .widgets.app_status_bar import AppStatusBar
from .widgets.command_bar import CommandBar
from .widgets.history_view import HistoryView
from .widgets.inline_code_view import InlineCodeView


class GitStudyAppV2(App):
    """Inline-quiz TUI for git-study."""

    TITLE = "git-study v2"

    CSS = """
    Screen {
        layout: vertical;
    }

    #scroll-wrapper {
        width: 1fr;
        height: 1fr;
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

    /* ── Autocomplete panel: appears below cmd-bar, covers mode+pad area ── */
    #cb-autocomplete {
        height: 5;
        display: none;
        background: transparent;
    }

    #cb-ac-list {
        height: auto;
        padding: 0 0;
    }

    /* ── Fixed bottom section ── */
    #app-status {
        height: 2;
    }

    #cmd-bar {
        height: 3;
    }

    #mode-bar {
        height: 2;
        padding: 1 1 0 1;
        color: $text-muted;
    }

    #bottom-pad {
        height: 3;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        Binding("tab", "global_tab", priority=True),
        Binding("shift+tab", "toggle_view", "Chat/Code", priority=True),
        Binding("shift+up", "prev_question", priority=True),
        Binding("shift+down", "next_question", priority=True),
    ]

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    _commits: reactive[list[dict]] = reactive(list, init=False)
    _repo_source: str = "local"
    _github_repo_url: str | None = None
    _local_repo_root: Path | None = None
    _original_local_root: Path | None = None  # startup시 결정된 로컬 경로 (github 전환 후에도 보존)

    def __init__(self, repo_path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._repo_path: Path | None = repo_path  # explicit path override
        self._questions: list[InlineQuizQuestion] = []
        self._answers: dict[str, str] = {}
        self._grades: list[InlineQuizGrade] = []
        self._known_files: dict[str, str] = {}
        self._current_q_index: int = 0
        self._mode: str = "idle"  # idle | quiz_loading | quiz_answering | grading | reviewing | chatting
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
        # Chat mode state
        self._current_thread_id: str = ""

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll-wrapper"):
            yield HistoryView(id="history-view")
            with Horizontal(id="code-container"):
                yield InlineCodeView(id="code-view")
        yield AppStatusBar(id="app-status")
        yield CommandBar(id="cmd-bar")
        with VerticalScroll(id="cb-autocomplete"):
            yield Static("", id="cb-ac-list")
        yield Static(self._mode_bar_text(), id="mode-bar")
        yield Static("", id="bottom-pad")

    def on_mount(self) -> None:
        # Prevent scroll containers from stealing focus
        self.query_one("#scroll-wrapper").can_focus = False
        self.query_one("#cb-autocomplete").can_focus = False
        self._load_local_repo()
        self.query_one("#cmd-bar", CommandBar).focus_input()

    def on_focus(self, event) -> None:
        """Redirect focus to command bar if it lands outside cmd-bar inputs."""
        focused = self.focused
        if focused is None:
            return
        if focused.id not in ("cb-input", "cb-answer", "code-scroll", "file-tree"):
            try:
                self.query_one("#cmd-bar", CommandBar).focus_input()
            except Exception:
                pass


    # ------------------------------------------------------------------
    # Initial load
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_local_repo(self) -> None:
        root = find_local_repo_root(start=self._repo_path)
        if root is None:
            self.call_from_thread(self._set_status, "Git 저장소를 찾을 수 없습니다.")
            self.call_from_thread(self._log, "Git 저장소를 찾을 수 없습니다.", "error")
            return
        self._local_repo_root = root
        self._original_local_root = root  # startup 경로 고정 (github 전환 후에도 유지)

        # CLI 인자 없을 때: 이전 github 세션이 있으면 복원
        if self._repo_path is None:
            try:
                global_state = load_app_state(repo_source="github")
                if (
                    global_state.get("repo_source") == "github"
                    and global_state.get("github_repo_url")
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
                            self._set_status, f"GitHub 복원 실패 ({exc}), 로컬로 전환합니다."
                        )
                        self._repo_source = "local"
                        self._github_repo_url = None
                        self._local_repo_root = root
                    else:
                        commits = snapshot.get("commits", [])
                        self.call_from_thread(self._apply_commits, commits, global_state)
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
            self.call_from_thread(self._set_status, f"커밋 로드 실패: {exc}")
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
            self._set_status("커밋이 없습니다.")
            self._log("커밋이 없습니다.", "error")
            return
        prev_oldest = self._oldest_sha
        prev_newest = self._newest_sha

        # Phase 3: restore saved SHA range, fall back to HEAD
        saved_oldest = saved_state.get("selected_range_start_sha", "")
        saved_newest = saved_state.get("selected_range_end_sha", "")

        sha_index = {c.get("sha", ""): i for i, c in enumerate(commits)}
        has_saved_range = (
            saved_oldest and saved_newest
            and saved_oldest in sha_index and saved_newest in sha_index
        )
        if has_saved_range:
            self._oldest_sha = saved_oldest
            self._newest_sha = saved_newest
            newest_idx = sha_index[saved_newest]
            oldest_idx = sha_index[saved_oldest]
            if newest_idx == oldest_idx:
                self._commit_selection = CommitSelection(start_index=newest_idx)
            else:
                self._commit_selection = CommitSelection(start_index=newest_idx, end_index=oldest_idx)
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
        if prev_oldest and (prev_oldest != self._oldest_sha or prev_newest != self._newest_sha):
            try:
                hv = self.query_one("#history-view", HistoryView)
                label = f"{self._oldest_sha[:7]}..{self._newest_sha[:7]}"
                hv.append_separator(f"─── 커밋 범위 변경: {label} ───")
            except Exception:
                pass

        # 현재 repo_source + URL/경로를 state에 저장 (재시작 시 복원용)
        self._save_app_state()

        # Phase 4: restore quiz session if exists for this range
        session_restored = self._try_restore_session()
        if not session_restored:
            self._set_status(
                f"저장소 로드 완료 ({len(commits)} commits). "
                "/quiz 로 퀴즈 생성, /commits 로 커밋 선택."
            )

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    def on_command_bar_command_submitted(self, event: CommandBar.CommandSubmitted) -> None:
        cmd = parse_command(event.text)
        # chat 메시지는 command 스타일로 로깅하지 않음 (append_user_message로 처리)
        if cmd.kind != "chat":
            self._log_command(event.text)
        match cmd.kind:
            case "quiz":
                self._start_quiz(cmd.range_arg)
            case "review":
                # 비동기 완료 후에도 올바른 블록을 찾도록 ID 전달
                log_block_id = self._current_log_block.id if self._current_log_block else None
                self._start_review(cmd.range_arg, log_block_id=log_block_id)
            case "grade":
                self._start_grading()
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
            case "exit":
                self.exit()
            case "chat":
                self._start_chat(cmd.range_arg)
            case "clear":
                self._handle_clear()
            case "resume":
                self._handle_resume()
            case _:
                self._set_status(f"알 수 없는 명령: {cmd.raw}")

    def on_command_bar_answer_submitted(self, event: CommandBar.AnswerSubmitted) -> None:
        if self._mode != "quiz_answering":
            return
        if self._current_q_index >= len(self._questions):
            return

        # Save answer
        qid = self._questions[self._current_q_index].get("id", "")
        self._answers[qid] = event.answer

        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.update_answer(self._current_q_index, event.answer)

        # Phase 4: persist session after each answer
        self._save_session()

        # Advance to next question
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
            # All questions answered
            cmd_bar = self.query_one("#cmd-bar", CommandBar)
            cmd_bar.set_command_mode()
            cmd_bar.status_text = "모든 답변 완료. /grade 로 채점하세요."
            self._set_mode("idle")
            try:
                self.query_one("#app-status", AppStatusBar).set_quiz_progress(0, 0)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Quiz block click
    # ------------------------------------------------------------------

    def on_inline_code_view_question_activated(
        self, event: InlineCodeView.QuestionActivated,
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
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        cmd_bar.focus_input()

    def action_prev_question(self) -> None:
        if self._questions:
            self._current_q_index = (self._current_q_index - 1) % len(self._questions)
            self._resume_answer_mode()

    def action_next_question(self) -> None:
        if self._questions:
            self._current_q_index = (self._current_q_index + 1) % len(self._questions)
            self._resume_answer_mode()

    def on_command_bar_prev_question(self, event: CommandBar.PrevQuestion) -> None:
        self.action_prev_question()

    def on_command_bar_next_question(self, event: CommandBar.NextQuestion) -> None:
        self.action_next_question()

    def on_command_bar_answer_exited(self, event: CommandBar.AnswerExited) -> None:
        self._set_mode("idle")
        if self._questions:
            total = len(self._questions)
            answered = len(self._answers)
            self._set_status(
                f"퀴즈 진행 중 ({answered}/{total} 답변) — 블록 클릭 또는 /answer 로 재진입"
            )
        self.query_one("#cmd-bar", CommandBar).focus_input()

    def _resume_answer_mode(self) -> None:
        if not self._questions:
            self._set_status("진행 중인 퀴즈가 없습니다. /quiz 로 퀴즈를 생성하세요.")
            return
        self._set_mode("quiz_answering")
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(
                self._current_q_index + 1, len(self._questions)
            )
        except Exception:
            pass
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.activate_question(self._current_q_index)
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        self._update_answer_status()
        cmd_bar.set_answer_mode(cmd_bar.status_text)
        cmd_bar.focus_input()

    # ------------------------------------------------------------------
    # Commit picker (Phase 1 & 2)
    # ------------------------------------------------------------------

    def _open_commit_picker(self) -> None:
        self._set_status("커밋 목록 갱신 중...")
        self._refresh_and_open_picker()

    @work(thread=True)
    def _refresh_and_open_picker(self) -> None:
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
            self.call_from_thread(self._set_status, f"커밋 갱신 실패: {exc}")
            fresh_commits = []
            snapshot = {}

        self.call_from_thread(self._do_open_picker, fresh_commits, snapshot)

    def _do_open_picker(self, fresh_commits: list[dict], snapshot: dict) -> None:
        if fresh_commits:
            self._commits = fresh_commits  # 메인 스레드에서 reactive 업데이트
        if not self._commits:
            self._set_status("커밋 목록이 없습니다.")
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

    def _on_commit_picker_result(self, result: tuple[CommitSelection, list[dict]] | None) -> None:
        if result is None:
            self._set_status(
                f"커밋 범위: {self._oldest_sha[:7]}..{self._newest_sha[:7]}  |  "
                "/quiz 로 퀴즈 생성, /commits 로 커밋 선택."
                if self._oldest_sha and self._newest_sha
                else "명령어를 입력하세요: /quiz, /grade, /help"
            )
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
            self._set_status("커밋이 선택되지 않았습니다.")
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
        self._show_code_view()

        # Phase 2: persist the selected range
        self._save_app_state()

        count = len(indices)

        # Update AppStatusBar range
        try:
            self.query_one("#app-status", AppStatusBar).set_range(oldest_sha, newest_sha, count)
        except Exception:
            pass

        self._log(f"커밋 범위 선택: {oldest_sha[:7]}..{newest_sha[:7]} ({count} commits)", "success")

        # 퀴즈 상태 초기화 후 새 범위의 세션 복원 시도
        self._reset_quiz_state()
        session_restored = self._try_restore_session()
        if not session_restored:
            self._set_status(
                f"커밋 범위 선택됨: {oldest_sha[:7]}..{newest_sha[:7]} ({count} commits). "
                "/quiz 로 퀴즈를 생성하세요."
            )

    # ------------------------------------------------------------------
    # Quiz generation
    # ------------------------------------------------------------------

    @work(thread=True)
    def _start_quiz(self, range_arg: str) -> None:
        if self._mode in ("quiz_loading", "grading"):
            self.call_from_thread(self._set_status, "이미 작업이 진행 중입니다.")
            return
        self.call_from_thread(self._set_mode, "quiz_loading")
        self.call_from_thread(self._set_status, "퀴즈 생성 준비 중...")

        try:
            oldest_sha, newest_sha = self._resolve_range(range_arg)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, f"범위 해석 실패: {exc}")
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
            repo = get_repo(repo_source=self._repo_source, github_repo_url=self._github_repo_url, refresh_remote=False, local_repo_root=self._local_repo_root)
            commit_shas = self._collect_shas_in_range(repo, oldest_sha, newest_sha)
            commits = [repo.commit(sha) for sha in commit_shas]
            if len(commits) == 1:
                context = build_commit_context(commits[0], "selected", repo)
            else:
                context = build_multi_commit_context(commits, "range_selected", repo)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, f"커밋 컨텍스트 생성 실패: {exc}")
            return

        # Stream quiz generation
        self.call_from_thread(self._set_status, "퀴즈 생성 중...")
        questions: list[InlineQuizQuestion] = []
        known_files: dict[str, str] = {}

        try:
            for event in stream_inline_quiz_progress(context, count=4):
                if event.get("type") == "node":
                    label = event.get("label", event.get("node", ""))
                    self.call_from_thread(self._set_status, f"퀴즈 생성 중... {label}")
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    questions = result.get("inline_questions", [])
                    # Parse file context for known_files
                    file_context = context.get("file_context_text", "")
                    if file_context:
                        known_files = parse_file_context_blocks(file_context)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, f"퀴즈 생성 실패: {exc}")
            return

        if not questions:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, "퀴즈 질문이 생성되지 않았습니다.")
            self.call_from_thread(self._log, "퀴즈 질문이 생성되지 않았습니다.", "error")
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
                    content = get_file_content_at_commit_or_empty(repo, newest_sha, fpath)
                    if content:
                        known_files[fpath] = content
        except Exception:
            pass

        self._questions = questions
        self._answers = {}
        self._grades = []
        self._known_files = known_files
        self._current_q_index = 0
        # mode will be set to quiz_answering in _apply_quiz_to_view (called from main thread)

        # Phase 2: persist updated SHA range (set via /quiz range_arg)
        self.call_from_thread(self._save_app_state)
        # Phase 4: save initial session
        self.call_from_thread(self._save_session)

        self.call_from_thread(self._log, f"퀴즈 생성 완료! {len(questions)}문제", "success")
        self.call_from_thread(self._apply_quiz_to_view)

    def _apply_quiz_to_view(self) -> None:
        self._set_mode("quiz_answering")
        total = len(self._questions)
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(1, total)
        except Exception:
            pass
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.load_inline_quiz(
            questions=self._questions,
            answers=self._answers,
            grades=self._grades,
            known_files=self._known_files,
            current_index=0,
        )
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        self._update_answer_status()
        cmd_bar.set_answer_mode(cmd_bar.status_text)
        cmd_bar.focus_input()

    def _show_range_in_view(self, oldest_sha: str, newest_sha: str) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=oldest_sha,
            newest_commit_sha=newest_sha,
            local_repo_root=self._local_repo_root,
        )
        self._show_code_view()
        # Update AppStatusBar range (count unknown here; use commits list for approximation)
        try:
            sha_index = {c.get("sha", ""): i for i, c in enumerate(self._commits)}
            o_idx = sha_index.get(oldest_sha, 0)
            n_idx = sha_index.get(newest_sha, 0)
            count = abs(o_idx - n_idx) + 1
            self.query_one("#app-status", AppStatusBar).set_range(oldest_sha, newest_sha, count)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Review (commit reading material)
    # ------------------------------------------------------------------

    @work(thread=True)
    def _start_review(self, range_arg: str, log_block_id: str | None = None) -> None:
        if self._mode in ("quiz_loading", "grading", "reviewing"):
            self.call_from_thread(self._set_status, "이미 작업이 진행 중입니다.")
            return
        self.call_from_thread(self._set_mode, "reviewing")
        self.call_from_thread(self._show_chat_view)
        self.call_from_thread(self._set_status, "리뷰 준비 중...")

        try:
            oldest_sha, newest_sha = self._resolve_range(range_arg)
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, f"범위 해석 실패: {exc}")
            self.call_from_thread(self._log, f"범위 해석 실패: {exc}", "error")
            return

        self._oldest_sha = oldest_sha
        self._newest_sha = newest_sha
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
            self.call_from_thread(self._set_status, f"커밋 컨텍스트 생성 실패: {exc}")
            self.call_from_thread(self._log, f"컨텍스트 생성 실패: {exc}", "error")
            return

        self.call_from_thread(self._set_status, "리뷰 생성 중...")
        final_output = ""
        try:
            for event in stream_read_progress(context):
                if event.get("type") == "node":
                    label = event.get("label", event.get("node", ""))
                    self.call_from_thread(self._set_status, f"리뷰 생성 중... {label}")
                elif event.get("type") == "result":
                    final_output = event.get("result", {}).get("final_output", "")
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, f"리뷰 생성 실패: {exc}")
            self.call_from_thread(self._log, f"리뷰 생성 실패: {exc}", "error")
            return

        self.call_from_thread(self._set_mode, "idle")
        if final_output:
            self.call_from_thread(self._render_review, final_output, log_block_id)
            self.call_from_thread(self._set_status, "리뷰 완료.")
        else:
            self.call_from_thread(self._log, "리뷰 내용이 생성되지 않았습니다.", "error")
            self.call_from_thread(self._set_status, "리뷰 생성 실패.")

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
    # Grading
    # ------------------------------------------------------------------

    @work(thread=True)
    def _start_grading(self) -> None:
        if not self._questions:
            self.call_from_thread(self._set_status, "채점할 퀴즈가 없습니다. /quiz 먼저 실행하세요.")
            return
        unanswered = [
            q for q in self._questions
            if q.get("id", "") not in self._answers
        ]
        if unanswered:
            self.call_from_thread(
                self._set_status,
                f"아직 {len(unanswered)}개 질문에 답변하지 않았습니다.",
            )
            return

        self.call_from_thread(self._set_mode, "grading")
        self.call_from_thread(self._set_status, "채점 중...")

        grades: list[InlineQuizGrade] = []
        try:
            for event in stream_inline_grade_progress(self._questions, self._answers):
                if event.get("type") == "node":
                    label = event.get("label", event.get("node", ""))
                    self.call_from_thread(self._set_status, f"채점 중... {label}")
                elif event.get("type") == "result":
                    result = event.get("result", {})
                    grades = result.get("final_grades", [])
        except Exception as exc:
            self.call_from_thread(self._set_mode, "idle")
            self.call_from_thread(self._set_status, f"채점 실패: {exc}")
            return

        self._grades = grades
        self.call_from_thread(self._set_mode, "idle")

        # Phase 4: persist session with grades
        self.call_from_thread(self._save_session)
        self.call_from_thread(self._apply_grades_to_view, grades)

    def _apply_grades_to_view(self, grades: list[InlineQuizGrade]) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.update_grades(grades)
        # Compute average score
        scores = [g.get("score", 0) for g in grades]
        avg = sum(scores) / len(scores) if scores else 0
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        cmd_bar.set_command_mode()
        cmd_bar.status_text = f"채점 완료! 평균 {avg:.1f}/100  ({len(grades)}문제)"
        self._log(f"채점 완료! 평균 {avg:.1f}/100  ({len(grades)}문제)", "success")

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        cmd_bar.show_help_panel([
            ("/commits", "커밋 범위 선택"),
            ("/quiz [범위]", "퀴즈 생성 (예: /quiz HEAD~3)"),
            ("/grade", "채점"),
            ("/answer", "답변 재진입"),
            ("/review [범위]", "커밋 해설 보기"),
            ("/clear", "대화 초기화 (이전 대화는 /resume 으로 복원)"),
            ("/resume", "이전 대화 불러오기"),
            ("/repo [URL/경로]", "저장소 전환 (인자 없으면 목록 모달)"),
            ("/apikey [key]", "OpenAI API key 설정 (인자 없으면 상태 표시)"),
            ("/help", "도움말"),
            ("Ctrl+Q", "종료"),
        ])

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
            return
        repo_source, url_or_path = result
        self._switch_repo_impl(repo_source, url_or_path)

    def _switch_repo(self, arg: str) -> None:
        """Parse argument and dispatch to _switch_repo_impl."""
        if arg.startswith("http") or arg.startswith("github.com"):
            try:
                url = normalize_github_repo_url(arg)
                self._switch_repo_impl("github", url)
            except ValueError as exc:
                self._set_status(f"잘못된 URL: {exc}")
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
                    self._set_status,
                    f"Git 저장소를 찾을 수 없습니다: {url_or_path}",
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
            self.call_from_thread(self._set_status, f"커밋 로드 실패: {exc}")
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
        if arg:
            self._set_api_key(arg)
        else:
            key, source = get_openai_api_key()
            if source == "missing":
                self._set_status("API key 없음. /apikey <key> 로 설정하세요.")
            else:
                masked = f"{key[:8]}..." if key and len(key) > 8 else "(설정됨)"
                if source == "file":
                    self._set_status(f"현재 API key: {masked}  (출처: file — {get_secrets_path()})")
                else:
                    self._set_status(f"현재 API key: {masked}  (출처: {source})")

    def _set_api_key(self, key: str) -> None:
        try:
            save_openai_api_key(key)
            masked = f"{key[:8]}..." if len(key) > 8 else "(설정됨)"
            self._set_status(f"API key 저장됨: {masked}  (~/.git-study/secrets.json)")
            self._log("OpenAI API key 저장 완료.", "success")
        except Exception as exc:
            self._set_status(f"API key 저장 실패: {exc}")

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

        # Support "SHA1..SHA2" format (order doesn't matter)
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

        # Single SHA
        commit = repo.commit(range_arg.strip())
        return commit.hexsha, commit.hexsha

    def _collect_shas_in_range(self, repo, oldest_sha: str, newest_sha: str) -> list[str]:
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
            self._commit_selection = CommitSelection(start_index=newest_idx, end_index=oldest_idx)

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
                    existing = _json.loads(global_state_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
                existing["repo_source"] = "local"
                existing["github_repo_url"] = ""
                global_state_path.parent.mkdir(parents=True, exist_ok=True)
                global_state_path.write_text(
                    _json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
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
        self._known_files = {}
        self._current_q_index = 0
        self._set_mode("idle")
        try:
            self.query_one("#cmd-bar", CommandBar).set_command_mode()
        except Exception:
            pass
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(0, 0)
        except Exception:
            pass
        try:
            self.query_one("#code-view", InlineCodeView).clear_quiz()
        except Exception:
            pass

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

    def _try_restore_session(self) -> bool:
        """Restore a saved quiz session for current SHA range. Returns True if restored."""
        sid = self._session_id()
        if not sid:
            return False

        # 챗 스레드 복원은 퀴즈 세션 여부와 무관하게 항상 시도
        self._restore_chat_thread(sid)

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

        self._questions = questions
        self._answers = payload.get("answers", {})
        self._grades = payload.get("grades", [])
        self._current_q_index = 0

        # Restore known_files from questions if needed (not persisted — will be empty)
        self._known_files = {}

        # Show code view with the restored range
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=self._oldest_sha,
            newest_commit_sha=self._newest_sha,
            local_repo_root=self._local_repo_root,
        )
        self._show_code_view()

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
                self._set_mode("quiz_answering")
                self._restore_answering_view()
        else:
            self._set_mode("quiz_answering")
            self._restore_answering_view()

        return True

    def _restore_chat_thread(self, sid: str) -> None:
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
                self._replay_thread_log(hv, thread_log[last_clear + 1:])
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
                    hv.append_result(content, style, block=block)
                case "app_markdown" | "markdown":
                    # block_id가 저장돼 있으면 매핑에서 원본 블록을 찾아 사용
                    target_block = block_id_map.get(orig_block_id, block) if orig_block_id else block
                    hv.append_markdown(content, block=target_block)
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
        threads_data.setdefault("threads", []).append({
            "id": tid,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "label": f"대화 {count}",
        })
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
            f"현재 선택된 커밋 범위: {self._oldest_sha[:7]}..{self._newest_sha[:7]}",
            "사용자의 질문에 한국어로 답변하세요.",
            "필요하면 get_file_content 도구를 사용해 파일 내용을 확인하세요.",
        ]
        return "\n".join(lines)

    def _build_commit_diff_context(self) -> str:
        """코드 리뷰어 에이전트용 커밋 범위 컨텍스트 문자열 생성."""
        if not self._oldest_sha:
            return ""
        return f"커밋 범위: {self._oldest_sha[:7]}..{self._newest_sha[:7]}"

    def _build_quiz_context(self) -> str:
        """퀴즈 튜터 에이전트용 퀴즈 문항 컨텍스트 문자열 생성."""
        if not self._questions:
            return ""
        lines = []
        for i, q in enumerate(self._questions, 1):
            file_info = f"{q.get('file_path', '')}:{q.get('anchor_line', '')}"
            lines.append(f"Q{i}. [{file_info}] {q.get('question', '')}")
            lines.append(f"    예상 답변: {q.get('expected_answer', '')}")
        return "\n".join(lines)

    @work(thread=True)
    def _start_chat(self, user_text: str) -> None:
        """채팅 메시지를 LLM으로 전송하고 스트리밍 응답을 표시."""
        if not user_text.strip():
            return

        tid = self._ensure_thread_id()
        if not tid:
            return

        self.call_from_thread(self._set_mode, "chatting")
        self.call_from_thread(self._show_chat_view)

        # HistoryView에 유저 메시지 표시
        # streaming 위젯은 route 이벤트 후에 생성 (에이전트 라벨이 위에 오도록)
        block_holder: list = []

        def _mount_user_msg():
            hv = self.query_one("#history-view", HistoryView)
            block = hv.append_user_message(user_text)
            block_holder.append((hv, block, None))  # streaming은 나중에 설정

        self.call_from_thread(_mount_user_msg)

        # 유저 메시지 thread log에 저장
        try:
            append_thread_event(
                tid,
                {"kind": "user_message", "content": user_text, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")},
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url or "",
                local_repo_root=self._local_repo_root,
            )
        except Exception:
            pass

        commit_context = self._build_commit_context()
        quiz_context = self._build_quiz_context()
        commit_diff_context = self._build_commit_diff_context()
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
            local_repo_root=self._local_repo_root,
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
        ):
            if not block_holder:
                continue

            etype = event.get("type", "")
            if etype == "route":
                label = event.get("label", "")
                def _add_route_then_streaming(lbl=label):
                    if not block_holder:
                        return
                    hv, block, _ = block_holder[0]
                    hv.append_result(f"→ {lbl}", "info", block)
                    sw = hv.begin_streaming(block)
                    block_holder[0] = (hv, block, sw)
                self.call_from_thread(_add_route_then_streaming)
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
                        {"kind": "tool_call", "content": name, "data": {"name": name, "args": event.get("args", {})}, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")},
                        repo_source=self._repo_source,
                        github_repo_url=self._github_repo_url or "",
                        local_repo_root=self._local_repo_root,
                    )
                except Exception:
                    pass
            elif etype == "done":
                full = event.get("full_content", accumulated)
                def _finish(f=full):
                    _ensure_streaming()
                    if not block_holder:
                        return
                    hv, block, streaming = block_holder[0]
                    if streaming is not None:
                        hv.end_streaming(block, streaming, f)
                self.call_from_thread(_finish)
                try:
                    append_thread_event(
                        tid,
                        {"kind": "assistant_message", "content": full, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")},
                        repo_source=self._repo_source,
                        github_repo_url=self._github_repo_url or "",
                        local_repo_root=self._local_repo_root,
                    )
                except Exception:
                    pass
                self.call_from_thread(self._set_mode, "idle")
                self.call_from_thread(self._set_status, "답변 완료.")
                return
            elif etype == "error":
                err = event.get("content", "오류 발생")
                def _on_error():
                    if block_holder:
                        hv, block, streaming = block_holder[0]
                        if streaming is not None:
                            hv.end_streaming(block, streaming, "")
                self.call_from_thread(_on_error)
                self.call_from_thread(self._log, f"채팅 오류: {err}", "error")
                self.call_from_thread(self._set_mode, "idle")
                return

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
        self._set_status("대화가 초기화됐습니다. /resume 으로 이전 대화를 불러올 수 있습니다.")

    def _handle_resume(self) -> None:
        """/resume: 이전 대화 목록 모달 표시 후 선택한 thread로 전환."""
        threads_data = load_chat_threads(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url or "",
            local_repo_root=self._local_repo_root,
        )
        threads = threads_data.get("threads", [])
        if not threads:
            self._set_status("이전 대화가 없습니다.")
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
        self._set_status(f"{result['label']} 재개됨.")

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
        )
        answered = len(self._answers)
        total = len(self._questions)
        try:
            self.query_one("#app-status", AppStatusBar).set_quiz_progress(
                self._current_q_index + 1, total
            )
        except Exception:
            pass
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        self._update_answer_status()
        cmd_bar.set_answer_mode(cmd_bar.status_text)
        cmd_bar.focus_input()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _repo_display_name(self) -> str:
        if self._repo_source == "github" and self._github_repo_url:
            return self._github_repo_url.split("github.com/")[-1].rstrip("/").removesuffix(".git")
        if self._local_repo_root:
            return self._local_repo_root.name
        return "unknown"

    def _mode_bar_text(self, code_active: bool = False) -> RichText:
        t = RichText()
        if code_active:
            t.append("▶▶ ", style="bold color(99)")
            t.append("Code view on", style="bold color(99)")
        else:
            t.append("▶▶ ", style="bold green")
            t.append("Chat mode on", style="bold green")
        t.append("  (shift+tab to cycle)", style="dim")
        return t

    def _update_mode_bar(self) -> None:
        code_active = self._is_code_view_active()
        try:
            self.query_one("#mode-bar", Static).update(
                self._mode_bar_text(code_active)
            )
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
        """App-level Tab: code view → panel cycle, chat mode → scroll+focus cmd-bar."""
        if self._is_code_view_active():
            # 코드 뷰에서는 패널 이동 유지
            self.action_focus_next()
            return
        # 챗 모드: 자동완성 열려 있으면 CommandBar에 위임, 아니면 스크롤+포커스
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        if cmd_bar._ac_candidates:
            cmd_bar.action_tab_pressed()
        else:
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
        if focused is None or getattr(focused, "id", None) not in ("cb-input", "cb-answer"):
            self.query_one("#cmd-bar", CommandBar).focus_input()

    def _has_code_content(self) -> bool:
        """Return True if commits have been selected and code view has content."""
        return bool(self._oldest_sha and self._newest_sha)

    def action_toggle_view(self) -> None:
        """Shift+Tab: toggle between Chat mode and Code View."""
        if self._is_code_view_active():
            self._show_chat_view()
        else:
            if self._has_code_content():
                self._show_code_view()
            else:
                self._set_status("선택된 커밋이 없습니다. /commits 로 커밋을 선택하세요.")
        self.call_after_refresh(self.query_one("#cmd-bar", CommandBar).focus_input)

    def _log_chat(self, kind: str, content: str, style: str = "", block_id: str = "") -> None:
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

    def _set_status(self, text: str) -> None:
        try:
            cmd_bar = self.query_one("#cmd-bar", CommandBar)
            cmd_bar.status_text = text
        except Exception:
            pass

    def _set_mode(self, mode: str) -> None:
        """Set internal mode and sync AppStatusBar."""
        self._mode = mode
        try:
            self.query_one("#app-status", AppStatusBar).set_mode(mode)
        except Exception:
            pass

    def _update_answer_status(self) -> None:
        q = self._current_q_index
        total = len(self._questions)
        fpath = self._questions[q].get("file_path", "") if q < total else ""
        fname = fpath.split("/")[-1] if fpath else ""
        file_info = f"  ·  {fname}" if fname else ""
        hint = f"Q{q + 1}/{total}{file_info}  [dim]Shift+Enter 제출 | Shift+↑↓ 이동 | Esc 종료[/dim]"
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        # mode는 이미 "answer"이므로 set_answer_mode 대신 status_text 직접 설정
        # (reactive가 동일 mode값 재할당 시 후속 업데이트를 무시할 수 있음)
        cmd_bar.status_text = hint


def run_v2() -> None:
    """Entry point for git-study-v2."""
    import argparse
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(prog="git-study-v2")
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Git 저장소 경로 (기본값: 현재 디렉토리)",
    )
    args = parser.parse_args()
    repo_path = Path(args.path).expanduser().resolve() if args.path else None
    GitStudyAppV2(repo_path=repo_path).run()

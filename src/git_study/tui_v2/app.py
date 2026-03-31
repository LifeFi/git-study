"""GitStudyAppV2: inline-quiz-first TUI with command bar."""

from __future__ import annotations

import time
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive

from ..domain.code_context import get_commit_parent_sha
from ..domain.inline_anchor import parse_file_context_blocks
from ..domain.repo_context import (
    build_commit_context,
    build_multi_commit_context,
    get_commit_list_snapshot,
    get_repo,
)
from ..services.inline_grade_service import stream_inline_grade_progress
from ..services.inline_quiz_service import stream_inline_quiz_progress
from ..tui.commit_selection import CommitSelection, selected_commit_indices
from ..tui.state import (
    find_local_repo_root,
    load_app_state,
    load_learning_session_file,
    save_app_state,
    save_learning_session_file,
)
from ..types import InlineQuizGrade, InlineQuizQuestion

from .commands import parse_command
from .screens import CommitPickerScreen
from .widgets.app_status_bar import AppStatusBar
from .widgets.command_bar import CommandBar
from .widgets.inline_code_view import InlineCodeView


class GitStudyAppV2(App):
    """Inline-quiz TUI for git-study."""

    TITLE = "git-study v2"

    CSS = """
    Screen {
        layout: vertical;
    }

    #main {
        width: 1fr;
        height: 1fr;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    _commits: reactive[list[dict]] = reactive(list, init=False)
    _repo_source: str = "local"
    _github_repo_url: str | None = None
    _local_repo_root: Path | None = None

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._questions: list[InlineQuizQuestion] = []
        self._answers: dict[str, str] = {}
        self._grades: list[InlineQuizGrade] = []
        self._known_files: dict[str, str] = {}
        self._current_q_index: int = 0
        self._mode: str = "idle"  # idle | quiz_loading | quiz_answering | grading
        self._oldest_sha: str = ""
        self._newest_sha: str = ""
        # CommitSelection tracks which indices are selected in the picker
        self._commit_selection: CommitSelection = CommitSelection()

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield InlineCodeView(id="code-view")
        yield CommandBar(id="cmd-bar")
        yield AppStatusBar(id="app-status")

    def on_mount(self) -> None:
        self._load_local_repo()
        self.query_one("#cmd-bar", CommandBar).focus_input()

    # ------------------------------------------------------------------
    # Initial load
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_local_repo(self) -> None:
        root = find_local_repo_root()
        if root is None:
            self.call_from_thread(self._set_status, "Git 저장소를 찾을 수 없습니다.")
            return
        self._local_repo_root = root
        try:
            snapshot = get_commit_list_snapshot(
                repo_source="local",
                refresh_remote=False,
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
            return

        # Phase 3: restore saved SHA range, fall back to HEAD
        saved_oldest = saved_state.get("selected_range_start_sha", "")
        saved_newest = saved_state.get("selected_range_end_sha", "")

        sha_index = {c.get("sha", ""): i for i, c in enumerate(commits)}
        if saved_oldest and saved_newest and saved_oldest in sha_index and saved_newest in sha_index:
            self._oldest_sha = saved_oldest
            self._newest_sha = saved_newest
            # commits 리스트는 newest-first이므로 newest가 작은 인덱스
            newest_idx = sha_index[saved_newest]
            oldest_idx = sha_index[saved_oldest]
            if newest_idx == oldest_idx:
                self._commit_selection = CommitSelection(start_index=newest_idx)
            else:
                self._commit_selection = CommitSelection(start_index=newest_idx, end_index=oldest_idx)
        else:
            self._oldest_sha = commits[0]["sha"]
            self._newest_sha = commits[0]["sha"]

        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=self._oldest_sha,
            newest_commit_sha=self._newest_sha,
        )

        # Update AppStatusBar repo name and initial range
        try:
            status_bar = self.query_one("#app-status", AppStatusBar)
            if self._local_repo_root is not None:
                status_bar.set_repo(self._local_repo_root.name)
            if self._oldest_sha and self._newest_sha:
                sha_index = {c.get("sha", ""): i for i, c in enumerate(commits)}
                o_idx = sha_index.get(self._oldest_sha, 0)
                n_idx = sha_index.get(self._newest_sha, 0)
                count = abs(o_idx - n_idx) + 1
                status_bar.set_range(self._oldest_sha, self._newest_sha, count)
        except Exception:
            pass

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
        match cmd.kind:
            case "quiz":
                self._start_quiz(cmd.range_arg)
            case "grade":
                self._start_grading()
            case "help":
                self._show_help()
            case "commits":
                self._open_commit_picker()
            case "answer":
                self._resume_answer_mode()
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
        self._update_answer_status()
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
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
                repo_source=self._repo_source,
                refresh_remote=False,
            )
            fresh_commits = snapshot.get("commits", [])
        except Exception as exc:
            self.call_from_thread(self._set_status, f"커밋 갱신 실패: {exc}")
            fresh_commits = []

        self.call_from_thread(self._do_open_picker, fresh_commits)

    def _do_open_picker(self, fresh_commits: list[dict]) -> None:
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
            ),
            callback=self._on_commit_picker_result,
        )

    def _on_commit_picker_result(self, result: CommitSelection | None) -> None:
        if result is None:
            return
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
        )

        # Phase 2: persist the selected range
        self._save_app_state()

        count = len(indices)

        # Update AppStatusBar range
        try:
            self.query_one("#app-status", AppStatusBar).set_range(oldest_sha, newest_sha, count)
        except Exception:
            pass

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
            repo = get_repo(repo_source=self._repo_source, github_repo_url=self._github_repo_url, refresh_remote=False)
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
            return

        # Also fetch file contents from git for files referenced in questions
        try:
            for q in questions:
                fpath = q.get("file_path", "")
                if fpath and fpath not in known_files:
                    from ..domain.code_context import get_file_content_at_commit_or_empty
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
        cmd_bar.set_answer_mode(
            f"Q1/{total}  |  [Shift+Enter] 제출  [Esc] 종료"
        )
        cmd_bar.focus_input()

    def _show_range_in_view(self, oldest_sha: str, newest_sha: str) -> None:
        code_view = self.query_one("#code-view", InlineCodeView)
        code_view.show_range(
            repo_source=self._repo_source,
            github_repo_url=self._github_repo_url,
            oldest_commit_sha=oldest_sha,
            newest_commit_sha=newest_sha,
        )
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
        cmd_bar.status_text = f"채점 완료! 평균 {avg:.1f}/10  ({len(grades)}문제)"

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        self._set_status(
            "/commits - 커밋 범위 선택  |  "
            "/quiz [범위] - 퀴즈 생성 (예: /quiz HEAD~3)  |  "
            "/grade - 채점  |  "
            "/help - 도움말  |  "
            "Ctrl+Q - 종료"
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
        if self._local_repo_root is None:
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
            f"이전 세션 복원됨. 채점 완료 — 평균 {avg:.1f}/10 ({len(self._grades)}문제). "
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
        self._set_status(
            f"이전 세션 복원됨. 모든 답변 완료 ({len(self._questions)}문제). "
            "/grade 로 채점하세요."
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
        cmd_bar.set_answer_mode(
            f"Q{self._current_q_index + 1}/{total}  |  [Shift+Enter] 제출  [Esc] 종료"
        )
        cmd_bar.focus_input()
        self._set_status(
            f"이전 세션 복원됨. {answered}/{total} 답변 완료."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        hint = f"Q{q + 1}/{total}{file_info}  |  [Shift+Enter] 제출  [Esc] 종료  [/answer] 재진입"
        cmd_bar = self.query_one("#cmd-bar", CommandBar)
        # mode는 이미 "answer"이므로 set_answer_mode 대신 status_text 직접 설정
        # (reactive가 동일 mode값 재할당 시 후속 업데이트를 무시할 수 있음)
        cmd_bar.status_text = hint


def run_v2() -> None:
    """Entry point for git-study-v2."""
    GitStudyAppV2().run()

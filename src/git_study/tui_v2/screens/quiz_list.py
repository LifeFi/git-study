"""QuizListScreen — 세션별 퀴즈 목록 모달 (2패널: 세션 목록 | 상세 퀴즈 목록)."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from ...tui.inline_quiz import QUESTION_TYPE_KO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_label(questions: list, answers: dict, grades: list) -> str:
    q = len(questions)
    a = len(answers)
    g = len(grades)
    parts = [f"Q:{q}"]
    if a:
        parts.append(f"A:{a}")
    if g:
        parts.append(f"G:{g}")
    return " ".join(parts)


def _session_status(questions: list, answers: dict, grades: list) -> str:
    if not questions:
        return ""
    if grades and len(grades) >= len(questions):
        return "★ 채점완료"
    if answers and len(answers) >= len(questions):
        return "✔ 답변완료"
    if answers:
        return f"… {len(answers)}/{len(questions)} 답변중"
    return "○ 미답변"


def _quiz_icon(qid: str, answers: dict, grades: list) -> tuple[str, str]:
    """(icon, icon_style) 반환."""
    grade_ids = {g.get("id", "") if isinstance(g, dict) else "" for g in grades}
    if qid in grade_ids:
        return "★", "bold yellow"
    if qid in answers:
        return "✔", "bold green"
    return "○", "dim"


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_len] + "…" if len(text) > max_len else text


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class QuizListScreen(ModalScreen[str | None]):
    """세션 목록(좌) + 상세 퀴즈 목록(우) 2패널 모달.

    Returns: 로드할 session_id (str) 또는 None (닫기).
    """

    BINDINGS = [
        Binding("escape", "cancel", "닫기"),
        Binding("enter", "confirm", "세션 로드", priority=True),
    ]

    CSS = """
    QuizListScreen {
        align: center middle;
    }

    #ql-container {
        width: 92%;
        height: 92%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }

    #ql-title {
        padding: 1 0 1 0;
    }

    #ql-help {
        color: $text-muted;
        padding-bottom: 1;
    }

    #ql-panels {
        height: 1fr;
    }

    /* ── 좌: 세션 패널 ── */
    #ql-session-panel {
        width: 32;
        border: solid $primary-darken-2;
    }

    #ql-session-list {
        height: 1fr;
    }

    #ql-session-list ListItem {
        padding: 0 1;
        border-bottom: solid $panel-darken-1;
    }

    /* ── 우: 퀴즈 패널 ── */
    #ql-quiz-panel {
        width: 1fr;
        border: solid $primary-darken-2;
        margin-left: 1;
    }

    #ql-quiz-list {
        height: 1fr;
    }

    #ql-quiz-list ListItem {
        padding: 1 1;
        border-bottom: solid $panel-darken-1;
    }

    /* ── 퀴즈 카드: InlineQuizBlock과 동일한 스타일 ── */
    #ql-quiz-list .ql-header {
        width: 1fr;
        color: $accent;
        text-style: bold;
    }

    #ql-quiz-list .ql-body {
        width: 1fr;
        margin-top: 1;
    }

    #ql-quiz-list .ql-score {
        width: 1fr;
        margin-top: 1;
        color: yellow;
        text-style: bold;
    }

    #ql-quiz-list .ql-my-answer {
        width: 1fr;
        margin-top: 1;
        color: $text-muted;
    }

    #ql-quiz-list .ql-model-answer {
        width: 1fr;
        margin-top: 1;
        color: cyan;
    }

    #ql-quiz-list .ql-feedback {
        width: 1fr;
        margin-top: 1;
        color: $text;
    }

    #ql-quiz-list .ql-no-answer {
        width: 1fr;
        margin-top: 1;
        color: $text-muted;
    }

    /* ── 하단 힌트 ── */
    #ql-hint {
        height: 1;
        padding: 0 1;
        background: $panel-darken-1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        sessions: list[dict],
        current_sid: str,
        *,
        repo_source: str = "local",
        github_repo_url: str = "",
        local_repo_root: Path | None = None,
    ) -> None:
        super().__init__()
        self._sessions = sorted(
            sessions,
            key=lambda s: s.get("updated_label", ""),
            reverse=True,
        )
        self._current_sid = current_sid
        self._repo_source = repo_source
        self._github_repo_url = github_repo_url
        self._local_repo_root = local_repo_root

        self._session_data_cache: dict[str, dict] = {}
        self._active_panel: str = "sessions"
        self._sel_session_idx: int = 0
        # 퀴즈 패널 generation 카운터 (clear() 비동기 → DuplicateIds 방지)
        self._panel_gen: int = 0

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    @staticmethod
    def _nav_title() -> Text:
        t = Text(no_wrap=True)
        t.append("커밋 범위 선택", style="dim")
        t.append(" ◀ ", style="bold white")
        t.append("[ 퀴즈 세션 선택 ]", style="bold color(99)")
        t.append("    Shift+Tab", style="bold white")
        t.append(" to switch", style="dim")
        return t

    def compose(self) -> ComposeResult:
        with Vertical(id="ql-container"):
            yield Static(self._nav_title(), id="ql-title")
            yield Static(
                "Tab: 패널 전환  |  Enter: 세션 로드  |  ↑↓: 이동  |  Esc: 닫기",
                id="ql-help",
            )
            with Horizontal(id="ql-panels"):
                with Vertical(id="ql-session-panel"):
                    yield ListView(
                        *[self._make_session_item(i) for i in range(len(self._sessions))],
                        id="ql-session-list",
                    )
                with Vertical(id="ql-quiz-panel"):
                    yield ListView(id="ql-quiz-list")

    def on_mount(self) -> None:
        self.query_one("#ql-session-list", ListView).focus()

        # 현재 세션 인덱스 초기화
        if self._current_sid and self._sessions:
            for i, s in enumerate(self._sessions):
                if s["session_id"] == self._current_sid:
                    self._sel_session_idx = i
                    break
            lv = self.query_one("#ql-session-list", ListView)
            lv.index = self._sel_session_idx

        self._refresh_quiz_panel()

    # ------------------------------------------------------------------
    # _on_key: Tab 가로채기 (App.global_tab보다 먼저 실행)
    # ------------------------------------------------------------------

    async def _on_key(self, event) -> None:
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self.action_switch_panel()
        else:
            await super()._on_key(event)

    # ------------------------------------------------------------------
    # Session item builder
    # ------------------------------------------------------------------

    def _make_session_item(self, idx: int) -> ListItem:
        s = self._sessions[idx]
        sid = s["session_id"]
        is_current = sid == self._current_sid
        updated = s.get("updated_label", "")[:16].replace("T", " ")

        # 세션 데이터에서 진행 정보 로드
        data = self._load_session_data(idx)
        questions: list = data.get("questions", [])
        answers: dict = data.get("answers", {})
        grades: list = data.get("grades", [])
        q_total = len(questions)
        a_count = len(answers)
        g_count = len(grades)

        line1 = Text()
        line1.append("● " if is_current else "  ")
        line1.append(sid, style="bold" if is_current else "")
        if is_current:
            line1.append(" ←현재", style="cyan dim")

        # 진행 정보 줄
        line2 = Text()
        line2.append("  ")
        if q_total > 0:
            if g_count >= q_total:
                scores = [g.get("score", 0) for g in grades if isinstance(g, dict)]
                avg = int(sum(scores) / len(scores)) if scores else 0
                line2.append(f"Q:{g_count}/{q_total} {avg}점", style="yellow")
            elif a_count >= q_total:
                line2.append(f"Q:{a_count}/{q_total}", style="green")
            elif a_count > 0:
                line2.append(f"Q:{a_count}/{q_total}", style="dim")
            else:
                line2.append(f"Q:0/{q_total}", style="dim")
        else:
            line2.append("—", style="dim")

        # 날짜 줄
        line3 = Text()
        line3.append("  ")
        line3.append(updated, style="dim")

        return ListItem(Label(line1), Label(line2), Label(line3), id=f"qs-{idx}")

    # ------------------------------------------------------------------
    # Session data
    # ------------------------------------------------------------------

    def _load_session_data(self, idx: int) -> dict:
        if idx < 0 or idx >= len(self._sessions):
            return {}
        sid = self._sessions[idx]["session_id"]
        if sid in self._session_data_cache:
            return self._session_data_cache[sid]
        from ...tui.state import load_learning_session_file
        try:
            data = load_learning_session_file(
                sid,
                repo_source=self._repo_source,
                github_repo_url=self._github_repo_url,
                local_repo_root=self._local_repo_root,
            ) or {}
        except Exception:
            data = {}
        self._session_data_cache[sid] = data
        return data

    # ------------------------------------------------------------------
    # Quiz panel
    # ------------------------------------------------------------------

    def _refresh_quiz_panel(self) -> None:
        self._panel_gen += 1
        gen = self._panel_gen

        data = self._load_session_data(self._sel_session_idx)
        questions: list = data.get("questions", [])
        answers: dict = data.get("answers", {})
        grades: list = data.get("grades", [])

        quiz_lv = self.query_one("#ql-quiz-list", ListView)
        quiz_lv.clear()

        if not questions:
            quiz_lv.append(
                ListItem(Label(Text("퀴즈 없음", style="dim")), id=f"qq-{gen}-empty")
            )
            return

        for i, q in enumerate(questions):
            item = self._make_quiz_item(i, q, answers, grades, gen)
            quiz_lv.append(item)

    def _make_quiz_item(
        self,
        idx: int,
        q: dict,
        answers: dict,
        grades: list,
        gen: int,
    ) -> ListItem:
        qid = q.get("id", "") if isinstance(q, dict) else ""
        question_text = q.get("question", "") if isinstance(q, dict) else str(q)
        file_path = q.get("file_path", "") if isinstance(q, dict) else ""
        anchor_line = q.get("anchor_line", 0) if isinstance(q, dict) else 0
        question_type = q.get("question_type", "") if isinstance(q, dict) else ""
        expected_answer = q.get("expected_answer", "") if isinstance(q, dict) else ""

        grade = next(
            (g for g in grades if isinstance(g, dict) and g.get("id") == qid),
            None,
        )
        answer = answers.get(qid, "")

        # ── 헤더: InlineQuizBlock과 동일 포맷 ──
        # "[Q번호/전체] 문제유형  ·  file.py:line"
        qtype_ko = QUESTION_TYPE_KO.get(question_type, question_type)
        num_label = f"[{idx + 1}]"
        loc = f"{file_path}:{anchor_line}" if anchor_line else file_path
        header_text = f"{num_label} {qtype_ko}  ·  {loc}"

        labels: list = [
            Label(header_text, classes="ql-header"),
            Label(question_text, classes="ql-body"),
        ]

        if grade:
            score = grade.get("score", 0)
            feedback = grade.get("feedback", "")

            labels.append(Label(f"★  {score} / 100", classes="ql-score"))
            labels.append(Label(f"내 답변:\n{answer or '(없음)'}", classes="ql-my-answer"))

            if expected_answer:
                labels.append(Label(f"모범 답안:\n{expected_answer}", classes="ql-model-answer"))

            if feedback:
                labels.append(Label(f"채점 이유:\n{feedback}", classes="ql-feedback"))

        elif answer:
            labels.append(Label(f"내 답변:\n{answer}", classes="ql-my-answer"))

        else:
            labels.append(Label("(미답변)", classes="ql-no-answer"))

        return ListItem(*labels, id=f"qq-{gen}-{idx}")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = event.list_view
        if lv.id != "ql-session-list":
            return
        if event.item is None:
            return
        item_id = event.item.id or ""
        if not item_id.startswith("qs-"):
            return
        try:
            new_idx = int(item_id[3:])
        except ValueError:
            return
        if new_idx == self._sel_session_idx:
            return
        self._sel_session_idx = new_idx
        self._refresh_quiz_panel()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "ql-session-list":
            self.action_confirm()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_switch_screen(self) -> None:
        """Shift+Tab: 커밋 피커로 전환."""
        self.dismiss("switch")

    def action_confirm(self) -> None:
        if not self._sessions:
            self.dismiss(None)
            return
        sid = self._sessions[self._sel_session_idx]["session_id"]
        self.dismiss(sid)

    def action_switch_panel(self) -> None:
        if self._active_panel == "sessions":
            self._active_panel = "quizzes"
            self.query_one("#ql-quiz-list", ListView).focus()
        else:
            self._active_panel = "sessions"
            self.query_one("#ql-session-list", ListView).focus()

    def on_focus(self, event) -> None:
        fid = getattr(self.focused, "id", None)
        if fid == "ql-session-list":
            self._active_panel = "sessions"
        elif fid == "ql-quiz-list":
            self._active_panel = "quizzes"

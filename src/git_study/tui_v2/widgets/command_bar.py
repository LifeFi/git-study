"""CommandBar widget: status line + input for commands / answers."""

import unicodedata

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Static, TextArea

from .app_status_bar import AppStatusBar

# (command, description) — autocomplete candidates (인자 있는 변형은 포함하지 않음)
_COMMANDS: list[tuple[str, str]] = [
    ("/commits", "커밋 범위 선택"),
    ("/quiz", "퀴즈 생성 — 범위·개수·저자 옵션 지원 (스페이스로 목록)"),
    ("/review", "커밋 해설 — /review 뒤에 스페이스로 범위 목록"),
    ("/map", "파일 구조·역할 맵 — /map 뒤에 스페이스로 옵션"),
    ("/grade", "채점"),
    ("/answer", "답변 재진입"),
    ("/clear", "대화 초기화"),
    ("/resume", "이전 대화 불러오기"),
    ("/repo", "저장소 전환 (URL 또는 경로)"),
    ("/apikey ", "API key 관리 — /apikey 뒤에 스페이스로 목록"),
    ("/model ", "모델 변경 — /model 뒤에 스페이스로 목록"),
    ("/hook ", "post-commit hook 관리 — /hook 뒤에 스페이스로 on/off 선택"),
    ("/help", "도움말"),
    ("/exit", "종료 (quit, Ctrl+Q 가능)"),
]

_QUIZ_CANDIDATES: list[tuple[str, str]] = [
    ("/quiz list", "세션별 퀴즈 목록 보기"),
    ("/quiz clear", "현재 범위 퀴즈 세션 삭제"),
    ("/quiz retry", "답변만 초기화하고 다시 풀기 (문제·채점 결과 유지)"),
    ("/quiz 5", "질문 5개 생성 (기본 3개)"),
    ("/quiz --ai", "AI 생성 코드 모드 (취약점·테스트·성능 집중)"),
    ("/quiz --others", "타인 코드 모드 (의도·동작·아키텍처 집중)"),
    ("/quiz HEAD", "HEAD 커밋 1개"),
    ("/quiz HEAD~3", "최근 4개 커밋"),
    ("/quiz HEAD~1..HEAD~4", "범위 직접 지정"),
    ("/quiz HEAD~3 --ai 6", "범위 + 저자 옵션 + 개수 조합"),
]

_REVIEW_CANDIDATES: list[tuple[str, str]] = [
    ("/review HEAD", "HEAD 커밋 1개"),
    ("/review HEAD~3", "최근 4개 커밋"),
]

_REPO_CANDIDATES: list[tuple[str, str]] = [
    ("/repo", "저장소 선택 창 열기"),
    ("/repo <경로 또는 URL>", "신규 저장소 추가 / 전환"),
]

_MAP_CANDIDATES: list[tuple[str, str]] = [
    ("/map", "커밋 맵 + 프로젝트 구조"),
    ("/map --refresh", "캐시 무시하고 재생성"),
]

_HOOK_CANDIDATES: list[tuple[str, str]] = [
    ("/hook on", "post-commit hook 설치 (커밋 후 자동 퀴즈)"),
    ("/hook off", "post-commit hook 제거"),
]

_APIKEY_CANDIDATES: list[tuple[str, str]] = [
    ("/apikey", "현재 API key 상태 표시"),
    ("/apikey set ", "API key 설정 (key 입력 후 Enter)"),
    ("/apikey unset", "저장된 API key 삭제"),
]

_MODEL_DESCRIPTIONS: dict[str, str] = {
    "gpt-5.4": "최신 플래그십",
    "gpt-5.4-pro": "고성능 플래그십",
    "gpt-5.4-mini": "코딩·서브에이전트",
    "gpt-5.4-nano": "초저가 대량 작업",
    "gpt-5": "GPT-5 기본",
    "gpt-5-mini": "GPT-5 소형",
    "gpt-4.1": "코딩·instruction 특화",
    "gpt-4.1-mini": "소형 저비용",
    "gpt-4.1-nano": "초소형",
    "gpt-4o": "멀티모달",
    "gpt-4o-mini": "빠르고 저렴 (기본값)",
    "gpt-4.5-preview": "GPT-4.5 프리뷰",
    "o4-mini": "추론 모델 (빠름)",
    "o3": "고성능 추론",
    "o3-pro": "고성능 추론 강화",
    "o3-mini": "수학·과학·코딩",
}


class CommandInput(TextArea):
    """TextArea 기반 커맨드 입력창.

    - Enter: 커맨드 제출
    - Shift+Enter: 줄바꿈 (TextArea 기본 동작)
    - Input 호환 API(value, cursor_position)를 래퍼 프로퍼티로 제공
    - 한국어 IME 조합 시 Input과 달리 커서 글리치 없음
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    BINDINGS = [
        Binding("enter", "submit_input", priority=True),
        Binding("shift+enter", "newline", priority=True),
    ]

    _programmatic: bool = False
    ghost_text: str = ""

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        if not self.ghost_text:
            return strip
        scroll_y = int(self.scroll_y)
        doc_line = scroll_y + y
        last_doc_line = max(0, self.document.line_count - 1)
        if doc_line != last_doc_line:
            return strip
        # 커서 위치까지만 잘라내고 ghost text를 그 뒤에 이어붙임
        cursor_col = self.cursor_location[1]
        left = strip.crop(0, cursor_col + 1)
        available = strip.cell_length - left.cell_length
        if available <= 1:
            return strip
        return Strip([*left._segments, Segment(self.ghost_text, Style(color="grey35"))])

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new_value: str) -> None:
        self._programmatic = True
        try:
            self.load_text(new_value)
            self.move_cursor((0, len(new_value)))
        finally:
            self._programmatic = False

    @property
    def cursor_position(self) -> int:
        return self.cursor_location[1]

    @cursor_position.setter
    def cursor_position(self, position: int) -> None:
        row, _ = self.cursor_location
        self.move_cursor((row, position))

    def action_submit_input(self) -> None:
        """Enter 키: 줄바꿈 없이 제출."""
        self.post_message(CommandInput.Submitted(self.text.rstrip("\n")))

    def action_newline(self) -> None:
        """Shift+Enter 키: 줄바꿈 삽입."""
        self.insert("\n")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """programmatic 변경 시 부모로 이벤트 전파 차단."""
        if self._programmatic:
            event.stop()


def _filter_slash_candidates(text: str) -> list[tuple[str, str]]:
    """/ 명령어 자동완성 후보 반환."""
    if not text or not text.startswith("/"):
        return []
    lower = text.lower()

    if lower == "/model" or lower.startswith("/model "):
        from ...settings import SUGGESTED_MODELS, DEFAULT_MODEL, load_settings

        query = lower[len("/model") :].strip()
        try:
            current = load_settings().get("model", DEFAULT_MODEL)
        except Exception:
            current = DEFAULT_MODEL
        results = []
        if not query:
            results.append(("/model", "현재 모델 확인"))
        for model in SUGGESTED_MODELS:
            if not query or model.lower().startswith(query):
                desc = _MODEL_DESCRIPTIONS.get(model, "")
                if model == current:
                    desc = f"● {desc}" if desc else "● 현재 모델"
                results.append((f"/model {model}", desc))
        return results

    if lower == "/quiz" or lower.startswith("/quiz "):
        query = lower[len("/quiz") :].strip()
        if not query:
            return [("/quiz", "퀴즈 생성 (현재 범위)"), *_QUIZ_CANDIDATES]
        return [(cmd, desc) for cmd, desc in _QUIZ_CANDIDATES if query in cmd.lower()]

    if lower == "/review" or lower.startswith("/review "):
        query = lower[len("/review") :].strip()
        if not query:
            return [("/review", "커밋 해설 보기 (현재 범위)"), *_REVIEW_CANDIDATES]
        return [(cmd, desc) for cmd, desc in _REVIEW_CANDIDATES if query in cmd.lower()]

    if lower == "/repo" or lower.startswith("/repo "):
        return list(_REPO_CANDIDATES)

    if lower == "/map" or lower.startswith("/map "):
        return list(_MAP_CANDIDATES)

    if lower == "/hook" or lower.startswith("/hook "):
        query = lower[len("/hook") :].strip()
        if not query:
            return list(_HOOK_CANDIDATES)
        return [
            (cmd, desc)
            for cmd, desc in _HOOK_CANDIDATES
            if query in cmd[len("/hook ") :].lower()
        ]

    if lower == "/apikey" or lower.startswith("/apikey "):
        query = lower[len("/apikey") :].strip()
        if not query:
            return list(_APIKEY_CANDIDATES)
        return [
            (cmd, desc)
            for cmd, desc in _APIKEY_CANDIDATES
            if query in cmd[len("/apikey") :].lower()
        ]

    # /뒤의 쿼리를 명령어 + 설명 전체에서 부분 매칭 (대소문자 무시)
    query = lower[1:]  # leading "/" 제거
    return [
        (cmd, desc)
        for cmd, desc in _COMMANDS
        if query in cmd[1:].lower() or query in desc.lower()
    ]


class CommandBar(Widget):
    """Bottom bar: status line + command input (command mode) or textarea (answer mode)."""

    DEFAULT_CSS = """
    CommandBar {
        height: auto;
        max-height: 14;
        background: transparent;
        layout: vertical;
    }

    CommandBar #cb-status-row {
        height: auto;
        min-height: 3;
        border-top: solid white 70%;
    }

    CommandBar #cb-alert {
        width: auto;
        height: 1;
        margin: 0 0 0 1;
        background: transparent;
        content-align: left middle;
        display: none;
    }

    CommandBar #cb-progress {
        width: auto;
        height: auto;
        padding: 0 1;
        color: $text;
        display: none;
    }

    CommandBar #cb-status {
        height: 3;
        width: 1fr;
        padding: 0 1 0 2;
        color: $text-muted;
    }

    CommandBar #cb-input-row {
        height: auto;
        align: left top;
        border-bottom: solid white 70%;
    }

    CommandBar #cb-prompt {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text;
        content-align: left middle;
    }

    CommandBar #cb-input {
        width: 1fr;
        height: auto;
        min-height: 1;
        max-height: 8;
        border: none;
        padding: 0;
        background: transparent;
    }

    CommandBar #cb-autocomplete {
        height: auto;
        max-height: 4;
        display: none;
        background: transparent;
    }

    CommandBar #cb-ac-list {
        height: auto;
        padding: 0 0;
    }
    """

    BINDINGS = [
        Binding("tab", "tab_pressed", priority=True),
        Binding("shift+up", "prev_question", priority=True),
        Binding("shift+down", "next_question", priority=True),
    ]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class CommandSubmitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class PrevQuestion(Message):
        pass

    class NextQuestion(Message):
        pass

    _DEFAULT_HINT: str = "명령어를 입력하세요: /quiz, /grade, /help"
    _ALERT_SPINNER: str = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _ALERT_COLORS: list[str] = [
        "rgb(30,160,60)",
        "rgb(180,100,0)",
        "rgb(0,120,160)",
    ]

    # ------------------------------------------------------------------
    # Reactive state
    # ------------------------------------------------------------------

    status_text: reactive[str] = reactive("명령어를 입력하세요: /quiz, /grade, /help")

    # ------------------------------------------------------------------
    # Internal state
    # ------------------------------------------------------------------

    _history: list[str]
    _history_index: int
    _history_draft: str
    _ac_candidates: list[tuple[str, str]]
    _ac_index: int
    _showing_help: bool
    _mention_files: list[str]  # App이 주입하는 파일 목록 (@-mention 자동완성용)
    _mention_changed_files: set[str]  # diff 범위 변경 파일 셋 (초록 표시용)
    _quiz_questions: list[str]  # 퀴즈 질문 텍스트 목록 (@n 자동완성용)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._history = []
        self._history_index = -1
        self._history_draft = ""
        self._ac_candidates = []
        self._ac_index = -1
        self._showing_help = False
        self._mention_files = []
        self._mention_changed_files = set()
        self._quiz_questions = []
        self._at_prefix = ""  # 중간 @ 자동완성 시 @ 앞쪽 텍스트 보존용
        self._status_timer: Timer | None = None
        self._context_hint: str = self._DEFAULT_HINT
        self._dismissable: bool = False
        self._alert_timer: Timer | None = None
        self._alert_step: int = 0
        self._alert_flag: str = ""
        self._alert_hint: str = ""
        self._warning_flag: str = ""
        self._warning_hint: str = ""
        self._progress_map: dict[str, str] = {}
        self._progress_timer: Timer | None = None
        self._progress_spinner_idx: int = 0

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="cb-status-row"):
            yield Static("", id="cb-alert")
            yield Static("", id="cb-progress")
            yield Static(self.status_text, id="cb-status")
        with Horizontal(id="cb-input-row"):
            yield Static("❯", id="cb-prompt")
            yield CommandInput(id="cb-input", show_line_numbers=False)
        yield AppStatusBar(id="app-status")

    # ------------------------------------------------------------------
    # Watchers
    # ------------------------------------------------------------------

    def watch_status_text(self, value: str) -> None:
        try:
            self.query_one("#cb-status", Static).update(value)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_status_timed(self, text: str, timeout: float = 4.0) -> None:
        """상태 메시지를 표시하고 timeout 초 후 기본 힌트로 자동 복원."""
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        self._dismissable = False
        self.status_text = text
        self._status_timer = self.set_timer(timeout, self._restore_default_hint)

    def set_status_dismissable(self, text: str) -> None:
        """ESC 키로 해제 가능한 지속 상태 메시지를 표시."""
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        self._dismissable = True
        self.status_text = text
        self._context_hint = self._DEFAULT_HINT

    def set_quiz_alert(self, flag: str, hint: str = "") -> None:
        """알림 영역에 스피너+색상 애니메이션과 함께 텍스트를 표시."""
        if self._warning_flag:
            return  # 경고가 우선 — 경고 중엔 퀴즈 알림 표시 안함
        self._alert_flag = flag
        self._alert_hint = hint
        try:
            self.query_one("#cb-alert", Static).display = True
        except Exception:
            pass
        if self._alert_timer is None:
            self._alert_step = 0
            self._alert_timer = self.set_interval(0.1, self._step_alert)

    def clear_quiz_alert(self) -> None:
        """알림 영역 숨김 및 애니메이션 정지."""
        if self._alert_timer is not None:
            self._alert_timer.stop()
            self._alert_timer = None
        self._alert_flag = ""
        self._alert_hint = ""
        try:
            w = self.query_one("#cb-alert", Static)
            w.update("")
            w.display = False
        except Exception:
            pass
        if self._warning_flag:
            self._show_warning()

    def set_warning_alert(self, flag: str, hint: str = "") -> None:
        """정적 경고를 cb-alert에 표시. quiz alert보다 우선."""
        self._warning_flag = flag
        self._warning_hint = hint
        if self._alert_timer is None:
            self._show_warning()

    def clear_warning_alert(self) -> None:
        """정적 경고 제거. quiz alert가 없으면 cb-alert를 숨긴다."""
        self._warning_flag = ""
        self._warning_hint = ""
        if self._alert_timer is None:
            try:
                w = self.query_one("#cb-alert", Static)
                w.update("")
                w.display = False
            except Exception:
                pass

    def _build_alert(self, icon: str, flag: str, hint: str, color: str) -> Text:
        """flag+hint 구조의 Rich Text 알림을 생성한다."""
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f" {icon} {flag} ", style=f"bold white on {color}")
        if hint:
            t.append(f"  {hint} │", style=color)
        return t

    def _show_warning(self) -> None:
        try:
            w = self.query_one("#cb-alert", Static)
            w.display = True
            w.update(
                self._build_alert(
                    "⚠", self._warning_flag, self._warning_hint, "#cc5500"
                )
            )
        except Exception:
            pass

    def _step_alert(self) -> None:
        """타이머 콜백 — 스피너 프레임 + 배경색 순환."""
        try:
            w = self.query_one("#cb-alert", Static)
            spinner = self._ALERT_SPINNER[self._alert_step % len(self._ALERT_SPINNER)]
            color = self._ALERT_COLORS[
                (self._alert_step // 10) % len(self._ALERT_COLORS)
            ]
            w.update(
                self._build_alert(spinner, self._alert_flag, self._alert_hint, color)
            )
            self._alert_step += 1
        except Exception:
            pass

    def set_progress(self, op_id: str, text: str | None) -> None:
        """op_id별 진행 상태를 #cb-progress에 수평 표시. text=None이면 해당 op 제거."""
        if not op_id:
            return
        if text is None:
            self._progress_map.pop(op_id, None)
        else:
            self._progress_map[op_id] = text

        if self._progress_map:
            try:
                self.query_one("#cb-progress", Static).display = True
            except Exception:
                pass
            if self._progress_timer is None:
                self._progress_spinner_idx = 0
                self._progress_timer = self.set_interval(
                    0.1, self._tick_progress_spinner
                )
        else:
            if self._progress_timer is not None:
                self._progress_timer.stop()
                self._progress_timer = None
            try:
                w = self.query_one("#cb-progress", Static)
                w.update("")
                w.display = False
            except Exception:
                pass

    def _tick_progress_spinner(self) -> None:
        if not self._progress_map:
            return
        frame = self._ALERT_SPINNER[
            self._progress_spinner_idx % len(self._ALERT_SPINNER)
        ]
        self._progress_spinner_idx += 1
        items = [f"{frame} {msg}" for msg in self._progress_map.values()]
        try:
            self.query_one("#cb-progress", Static).update("\n".join(items))
        except Exception:
            pass

    def _restore_default_hint(self) -> None:
        if self._progress_map:
            return
        self._status_timer = None
        self.status_text = self._context_hint

    def update_context_hint(
        self,
        zone: str,
        quiz_count: int = 0,
        answered_count: int = 0,
        graded: bool = False,
    ) -> None:
        """포커스 존과 퀴즈 상태에 따라 컨텍스트 힌트를 갱신한다.

        zone: "left_panel" | "right_panel" | "command_bar_chat" |
              "command_bar_code" | "focus_lost"
        타이머로 표시 중인 임시 메시지가 있으면 _context_hint만 갱신하고
        실제 status_text는 타이머 만료 후 자동 반영된다.
        """
        has_quiz = quiz_count > 0

        if graded:
            hint = "다시 풀어보기 /quiz retry  │  새로운 커밋 공부 /commits"
            if zone in ("command_bar_chat", "command_bar_code"):
                if self._status_timer is not None:
                    self._status_timer.stop()
                    self._status_timer = None
        elif zone == "left_panel":
            hint = "📂 파일 트리 — Tab: 코드뷰  │  Shift+Tab: 채팅"
            if has_quiz:
                hint += "  │  Shift+↑↓: 문제 이동"
        elif zone == "right_panel":
            hint = "💻 코드뷰 — Tab: 명령창  │  Shift+Tab: 채팅"
            if has_quiz:
                hint += "  │  Shift+↑↓: 문제 이동"
        elif zone == "focus_lost":
            hint = "Tab → 명령창으로 이동"
            if has_quiz:
                hint += "  │  Shift+↑↓: 문제 이동"
        elif zone in ("command_bar_chat", "command_bar_code"):
            if has_quiz:
                hint = "명령어: /quiz /grade  │  Shift+↑↓: 문제 이동"
            else:
                hint = self._DEFAULT_HINT

        self._context_hint = hint
        if self._status_timer is None:
            self.status_text = hint

    def get_current_answer(self) -> str:
        return self.query_one("#cb-input", CommandInput).value

    def clear_input(self) -> None:
        self.query_one("#cb-input", CommandInput).value = ""

    def focus_input(self) -> None:
        self.query_one("#cb-input", CommandInput).focus()

    def set_quiz_questions(self, questions: list[str]) -> None:
        """퀴즈 질문 텍스트 목록 주입. @n 자동완성에 사용."""
        self._quiz_questions = list(questions)

    def set_mention_files(self, paths: list[str]) -> None:
        """App이 현재 커밋 파일 목록을 주입. @-mention 자동완성에 사용."""
        self._mention_files = list(paths)

    def set_mention_changed_files(self, paths: set[str]) -> None:
        """diff 범위 변경 파일 셋 주입. @-mention 자동완성에서 초록 표시."""
        self._mention_changed_files = set(paths)

    def insert_mention(self, file_path: str, start: int = 0, end: int = 0) -> None:
        """코드뷰 push 또는 자동완성 선택 시 입력창에 @file[start-end] 삽입."""
        if start > 0 and end > 0:
            mention_text = f"@{file_path}[{start}-{end}]"
        else:
            mention_text = f"@{file_path}"
        try:
            inp = self.query_one("#cb-input", CommandInput)
            current = inp.value
            if current and not current.endswith(" "):
                inp.value = current + " " + mention_text + " "
            else:
                inp.value = (current or "") + mention_text + " "
            inp.cursor_position = len(inp.value)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Input placeholder hint
    # ------------------------------------------------------------------

    _INPUT_HINTS: dict[str, str] = {
        "/quiz":   "[숫자]  HEAD | HEAD~N | A..B  [--ai|--others]",
        "/review": "HEAD | HEAD~N | A..B",
        "/model":  "<모델명>  (스페이스로 목록 확인)",
        "/hook":   "on | off",
        "/apikey": "set <key> | unset",
        "/repo":   "<경로 또는 URL>  (생략 시 선택창)",
        "/map":    "--refresh",
    }

    def _get_input_hint(self, text: str) -> str:
        """입력된 커맨드에 따라 인자 힌트를 반환. 공백/인자가 있으면 빈 문자열."""
        if not text or " " in text or "\n" in text:
            return ""
        return self._INPUT_HINTS.get(text.strip(), "")

    def _update_input_hint(self, text: str) -> None:
        hint = self._get_input_hint(text)
        try:
            inp = self.query_one("#cb-input", CommandInput)
            inp.ghost_text = f"  {hint}" if hint else ""
            inp.refresh()
        except Exception:
            pass

    def _get_ac_candidates(self, text: str) -> list[tuple[str, str]]:
        """입력 텍스트에 따라 자동완성 후보 반환 (/ 명령어 또는 @ 멘션)."""
        # @ 트리거: 텍스트 시작 또는 중간에서 공백 없는 마지막 @ 세그먼트 감지
        at_idx = text.rfind("@")
        if at_idx >= 0:
            after_at = text[at_idx + 1:]
            # 공백/줄바꿈이 없으면 아직 입력 중인 @ mention
            if " " not in after_at and "\n" not in after_at:
                self._at_prefix = text[:at_idx]
                return self._get_mention_candidates("@" + after_at)
        self._at_prefix = ""
        return _filter_slash_candidates(text)

    def _get_mention_candidates(self, text: str) -> list[tuple[str, str]]:
        """@ mention 후보 반환. text는 '@...' 형태."""
        if text.startswith("@"):
            query = text[1:]
            if "[" in query:
                return []

            # 퀴즈 번호 후보 (@숫자): 파일 후보보다 먼저 표시
            quiz_results: list[tuple[str, str]] = []
            if not query or query.isdigit():
                for i, q_text in enumerate(self._quiz_questions, 1):
                    if query and not str(i).startswith(query):
                        continue
                    preview = q_text[:28] + "…" if len(q_text) > 28 else q_text
                    quiz_results.append((f"@{i} ", f"퀴즈 #{i} — {preview}"))

            # 캐시된 파일 목록 없으면 code view에서 직접 가져오기 (fallback)
            mention_files = self._mention_files
            if not mention_files:
                try:
                    code_view = self.app.query_one("#code-view")
                    mention_files = list(getattr(code_view, "file_paths", []))
                    if mention_files:
                        self._mention_files = mention_files
                except Exception:
                    pass
            # changed_paths도 fallback
            if not self._mention_changed_files:
                try:
                    code_view = self.app.query_one("#code-view")
                    changed = set(getattr(code_view, "changed_paths", set()))
                    if changed:
                        self._mention_changed_files = changed
                except Exception:
                    pass
            lower_query = query.lower()

            # 현재 디렉토리 prefix와 이름 필터 분리
            # 예) "src/git" → current_dir="src/", name_filter="git"
            #     "src/"   → current_dir="src/", name_filter=""
            #     "foo"    → current_dir="",      name_filter="foo"
            if "/" in lower_query:
                slash_idx = lower_query.rfind("/")
                current_dir = lower_query[: slash_idx + 1]
                name_filter = lower_query[slash_idx + 1 :]
            else:
                current_dir = ""
                name_filter = lower_query

            results: list[tuple[str, str]] = []
            seen_dirs: set[str] = set()

            for path in mention_files:
                lower_path = path.lower()
                # 현재 디렉토리 하위에 있는 파일만
                if not lower_path.startswith(current_dir):
                    continue
                # current_dir 이후 남은 경로
                rest = path[len(current_dir) :]
                # 이름 필터 매칭
                if name_filter and name_filter not in rest.lower():
                    continue
                # 바로 아래 단계만: rest에 '/'가 있으면 폴더, 없으면 파일
                parts = rest.split("/")
                if len(parts) > 1:
                    sub_dir = current_dir + parts[0] + "/"
                    if sub_dir not in seen_dirs:
                        seen_dirs.add(sub_dir)
                        # 이 폴더 하위에 변경 파일이 있으면 "changed" 표시
                        prefix = sub_dir
                        is_changed_dir = any(
                            p.startswith(prefix) for p in self._mention_changed_files
                        )
                        results.append(
                            (f"@{sub_dir}", "changed" if is_changed_dir else "")
                        )
                else:
                    is_changed = path in self._mention_changed_files
                    results.append((f"@{path}", "changed" if is_changed else ""))

            # 폴더(/) 먼저, 그 다음 파일
            results.sort(key=lambda x: (0 if x[0].endswith("/") else 1, x[0]))
            return quiz_results + results
        return []

    def action_prev_question(self) -> None:
        self.post_message(self.PrevQuestion())

    def action_next_question(self) -> None:
        self.post_message(self.NextQuestion())

    def action_tab_pressed(self) -> None:
        if self._ac_candidates and 0 <= self._ac_index < len(self._ac_candidates):
            cmd, _ = self._ac_candidates[self._ac_index]
            self._close_autocomplete()
            inp = self.query_one("#cb-input", CommandInput)
            if cmd.startswith("@"):
                inp.value = self._at_prefix + cmd
                inp.cursor_position = len(inp.value)
            else:
                inp.value = cmd
                inp.cursor_position = len(cmd)
        else:
            try:
                self.app.handle_tab_no_autocomplete()
            except Exception:
                self.app.action_focus_next()

    # ------------------------------------------------------------------
    # Autocomplete helpers
    # ------------------------------------------------------------------

    def _open_autocomplete(self, candidates: list[tuple[str, str]], index: int) -> None:
        self._ac_candidates = candidates
        self._ac_index = index
        self._render_autocomplete()
        try:
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = 7
            ac.display = True
            self.app.query_one("#mode-bar").display = False
            self.app.query_one("#content-spacer").display = False
            self.query_one("#app-status").display = False
            self.app.query_one("#scroll-wrapper").scroll_end(animate=False)
        except Exception:
            pass

    def _close_autocomplete(self) -> None:
        self._ac_candidates = []
        self._ac_index = -1
        try:
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = 7
            ac.display = False
            self.app.query_one("#mode-bar").display = True
            self.app.query_one("#content-spacer").display = True
            self.query_one("#app-status").display = True
        except Exception:
            pass

    def show_help_panel(self, lines: list[tuple[str, str]]) -> None:
        """Show help text in the autocomplete panel area."""
        self._ac_candidates = []
        self._ac_index = -1
        self._showing_help = True
        try:

            def _display_width(s: str) -> int:
                return sum(
                    2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s
                )

            t = Text()
            for i, (cmd, desc) in enumerate(lines):
                if cmd == "" and desc == "":
                    t.append("\n")
                    continue
                pad = max(0, 25 - _display_width(cmd))
                t.append(f"   {cmd}" + " " * pad, style="bold")
                if isinstance(desc, Text):
                    t.append(" ")
                    t.append_text(desc)
                else:
                    t.append(f" {desc}", style="dim")
                if i < len(lines) - 1:
                    t.append("\n")
            self.app.query_one("#cb-ac-list", Static).update(t)
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = "auto"
            ac.display = True
            self.app.query_one("#mode-bar").display = False
            self.query_one("#app-status").display = False
            self.app.query_one("#scroll-wrapper").scroll_end(animate=False)
        except Exception:
            pass

    def _close_help_panel(self) -> None:
        self._showing_help = False
        try:
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = 4
            ac.display = False
            self.app.query_one("#mode-bar").display = True
            self.app.query_one("#content-spacer").display = True
            self.query_one("#app-status").display = True
        except Exception:
            pass

    def _render_autocomplete(self) -> None:
        try:
            t = Text()
            for i, (cmd, desc) in enumerate(self._ac_candidates):
                is_selected = i == self._ac_index
                is_current = desc.startswith("● ")
                is_changed = desc == "changed"
                rest_desc = desc[2:] if is_current else desc

                if is_selected:
                    if is_changed:
                        t.append(
                            f" ▶ {cmd:<22}", style="bold bright_green on color(99)"
                        )
                    else:
                        t.append(f" ▶ {cmd:<22}", style="bold white on color(99)")
                    t.append(" ", style="bold white on color(99)")
                    if is_current:
                        t.append("●", style="bold bright_green on color(99)")
                        t.append(f" {rest_desc} ", style="bold white on color(99)")
                    elif not is_changed:
                        t.append(f"{rest_desc} ", style="bold white on color(99)")
                else:
                    if is_changed:
                        t.append(f"   {cmd:<22}", style="bold bright_green")
                    else:
                        t.append(f"   {cmd:<22}", style="dim")
                    t.append(" ", style="dim")
                    if is_current:
                        t.append("●", style="bold bright_green")
                        t.append(f" {rest_desc}", style="dim")
                    elif not is_changed:
                        t.append(f"{rest_desc}", style="dim")

                if i < len(self._ac_candidates) - 1:
                    t.append("\n")
            self.app.query_one("#cb-ac-list", Static).update(t)
            try:
                scroll = self.app.query_one("#cb-autocomplete")
                visible_height = 7
                current_top = int(scroll.scroll_y)
                if self._ac_index >= current_top + visible_height:
                    scroll.scroll_to(
                        y=self._ac_index - visible_height + 1, animate=False
                    )
                elif self._ac_index < current_top:
                    scroll.scroll_to(y=self._ac_index, animate=False)
            except Exception:
                pass
        except Exception:
            pass

    def _ac_select(self) -> None:
        if 0 <= self._ac_index < len(self._ac_candidates):
            cmd, _ = self._ac_candidates[self._ac_index]
            self._close_autocomplete()

            # 입력만 모드: 후행 공백으로 끝나는 후보는 입력창에만 채우고 실행 안 함
            if cmd.endswith(" "):
                inp = self.query_one("#cb-input", CommandInput)
                full = self._at_prefix + cmd if cmd.startswith("@") else cmd
                inp.value = full
                inp.cursor_position = len(full)
                return

            text = cmd.strip()

            # @ 멘션 선택
            if text.startswith("@"):
                try:
                    inp = self.query_one("#cb-input", CommandInput)
                    if text.endswith("/"):
                        # 폴더 선택: prefix + @dir/ 채우고 자동완성 재트리거
                        inp.value = self._at_prefix + text
                    else:
                        # 파일/퀴즈 선택: prefix + @... 채우기
                        inp.value = self._at_prefix + text
                    inp.cursor_position = len(inp.value)
                except Exception:
                    pass
                return

            if not text.startswith("/") and (
                not self._history or self._history[-1] != text
            ):
                self._history.append(text)
            self._history_index = -1
            self._history_draft = ""
            self.query_one("#cb-input", CommandInput).value = ""
            self.post_message(self.CommandSubmitted(text))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_command_input_blur(self, event) -> None:
        """#cb-input 포커스 이탈 시, 합법적인 대상이 아니면 복구."""

        def _restore_if_needed() -> None:
            focused = self.app.focused
            if focused is not None:
                fid = getattr(focused, "id", None)
                if fid in ("cb-input", "code-scroll", "file-tree"):
                    return
                if focused.has_class("iqb-input"):
                    return
            # 모달(추가 화면)이 열려 있으면 복구 안 함
            if len(self.app.screen_stack) > 1:
                return
            self.focus_input()

        self.call_after_refresh(_restore_if_needed)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "cb-input":
            return
        value = event.text_area.text
        if self._showing_help:
            if value:
                self._close_help_panel()
            else:
                return
        candidates = self._get_ac_candidates(value)
        if candidates:
            self._open_autocomplete(candidates, 0)
        else:
            self._close_autocomplete()
        self._update_input_hint(value)

    def on_command_input_submitted(self, event: CommandInput.Submitted) -> None:
        if self._ac_candidates:
            self._ac_select()
            event.stop()
            return
        text = event.value.strip()
        if text:
            if not text.startswith("/") and (
                not self._history or self._history[-1] != text
            ):
                self._history.append(text)
            self._history_index = -1
            self._history_draft = ""
            self.clear_input()
            self.post_message(self.CommandSubmitted(text))
        event.stop()

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            if self._ac_candidates:
                self._ac_index = max(0, self._ac_index - 1)
                self._render_autocomplete()
            else:
                self._history_prev()
            event.stop()
            event.prevent_default()
        elif event.key == "down":
            if self._ac_candidates:
                self._ac_index = min(len(self._ac_candidates) - 1, self._ac_index + 1)
                self._render_autocomplete()
            else:
                self._history_next()
            event.stop()
            event.prevent_default()
        elif event.key == "escape":
            if self._showing_help:
                self._close_help_panel()
                event.stop()
                event.prevent_default()
            elif self._dismissable:
                self._dismissable = False
                self.status_text = self._DEFAULT_HINT
                self._context_hint = self._DEFAULT_HINT
                event.stop()
                event.prevent_default()
            elif self._ac_candidates:
                self._close_autocomplete()
                event.stop()
                event.prevent_default()

    # ------------------------------------------------------------------
    # History navigation
    # ------------------------------------------------------------------

    def _history_prev(self) -> None:
        if not self._history:
            return
        inp = self.query_one("#cb-input", CommandInput)
        if self._history_index == -1:
            self._history_draft = inp.value
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        inp.value = self._history[self._history_index]
        inp.cursor_position = len(inp.value)

    def _history_next(self) -> None:
        if self._history_index == -1:
            return
        inp = self.query_one("#cb-input", CommandInput)
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            inp.value = self._history[self._history_index]
        else:
            self._history_index = -1
            inp.value = self._history_draft
            self._history_draft = ""
        inp.cursor_position = len(inp.value)

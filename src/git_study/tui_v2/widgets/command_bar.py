"""CommandBar widget: status line + input for commands / answers."""

import unicodedata

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Static

# (command, description) — autocomplete candidates
_COMMANDS: list[tuple[str, str]] = [
    ("/commits", "커밋 범위 선택"),
    ("/quiz", "퀴즈 생성 (현재 범위)"),
    ("/quiz HEAD", "HEAD 커밋 퀴즈"),
    ("/quiz HEAD~3", "최근 4개 커밋 퀴즈"),
    ("/quiz HEAD~1..HEAD~4", "범위 지정 퀴즈"),
    ("/review", "커밋 해설 보기 (현재 범위)"),
    ("/review HEAD", "HEAD 커밋 해설"),
    ("/review HEAD~3", "최근 4개 커밋 해설"),
    ("/grade", "채점"),
    ("/answer", "답변 재진입"),
    ("/clear", "대화 초기화 (이전 대화는 /resume 으로 복원)"),
    ("/resume", "이전 대화 불러오기"),
    ("/repo", "저장소 전환 (URL 또는 경로)"),
    ("/apikey", "OpenAI API key 설정"),
    ("/model", "모델 변경 — /model 뒤에 스페이스로 목록"),
    ("/install-hook", "현재 저장소에 post-commit hook 설치 (예: /install-hook iterm2)"),
    ("/uninstall-hook", "post-commit hook 제거"),
    ("/help", "도움말"),
    ("/exit", "종료 (quit, Ctrl+Q 가능)"),
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
        for model in SUGGESTED_MODELS:
            if not query or model.lower().startswith(query):
                desc = _MODEL_DESCRIPTIONS.get(model, "")
                if model == current:
                    desc = f"● {desc}" if desc else "● 현재 모델"
                results.append((f"/model {model}", desc))
        return results

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

    CommandBar #cb-status {
        height: 1;
        width: 1fr;
        padding: 0 1;
        color: $text-muted;
        background: $panel;

    }

    CommandBar #cb-input-row {
        height: 3;
        align: left middle;
        border-top: solid white 70%;
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
        height: 1;
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
        self._status_timer: Timer | None = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self.status_text, id="cb-status")
        with Horizontal(id="cb-input-row"):
            yield Static("❯", id="cb-prompt")
            yield Input(placeholder="", id="cb-input")

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
        self.status_text = text
        self._status_timer = self.set_timer(timeout, self._restore_default_hint)

    def _restore_default_hint(self) -> None:
        self._status_timer = None
        self.status_text = self._DEFAULT_HINT

    def get_current_answer(self) -> str:
        return self.query_one("#cb-input", Input).value

    def clear_input(self) -> None:
        self.query_one("#cb-input", Input).value = ""

    def focus_input(self) -> None:
        self.query_one("#cb-input", Input).focus()

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
            inp = self.query_one("#cb-input", Input)
            current = inp.value
            if current and not current.endswith(" "):
                inp.value = current + " " + mention_text + " "
            else:
                inp.value = (current or "") + mention_text + " "
            inp.cursor_position = len(inp.value)
        except Exception:
            pass

    def _get_ac_candidates(self, text: str) -> list[tuple[str, str]]:
        """입력 텍스트에 따라 자동완성 후보 반환 (/ 명령어 또는 @ 멘션)."""
        if text.startswith("@"):
            query = text[1:]
            if "[" in query:
                return []
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
            return results
        return _filter_slash_candidates(text)

    def action_prev_question(self) -> None:
        self.post_message(self.PrevQuestion())

    def action_next_question(self) -> None:
        self.post_message(self.NextQuestion())

    def action_tab_pressed(self) -> None:
        if self._ac_candidates and 0 <= self._ac_index < len(self._ac_candidates):
            cmd, _ = self._ac_candidates[self._ac_index]
            self._close_autocomplete()
            inp = self.query_one("#cb-input", Input)
            if cmd.startswith("@"):
                inp.value = cmd if cmd.endswith("/") else cmd + "["
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
                pad = max(0, 25 - _display_width(cmd))
                t.append(f"   {cmd}" + " " * pad, style="bold")
                t.append(f" {desc}", style="dim")
                if i < len(lines) - 1:
                    t.append("\n")
            self.app.query_one("#cb-ac-list", Static).update(t)
            ac = self.app.query_one("#cb-autocomplete")
            ac.styles.height = "auto"
            ac.display = True
            self.app.query_one("#mode-bar").display = False
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
            text = cmd.strip()

            # @ 멘션 선택
            if text.startswith("@"):
                try:
                    inp = self.query_one("#cb-input", Input)
                    if text.endswith("/"):
                        # 폴더 선택: @dir/ 채우고 on_input_changed가 자동완성 재트리거
                        inp.value = text
                    else:
                        # 파일 선택: @file.py[ 채우고 라인 범위 입력 대기
                        inp.value = text + "["
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
            self.query_one("#cb-input", Input).value = ""
            self.post_message(self.CommandSubmitted(text))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_blur(self, event) -> None:
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._showing_help:
            if event.value:
                self._close_help_panel()
            else:
                return
        candidates = self._get_ac_candidates(event.value)
        if candidates:
            self._open_autocomplete(candidates, 0)
        else:
            self._close_autocomplete()

    def on_input_submitted(self, event: Input.Submitted) -> None:
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
        inp = self.query_one("#cb-input", Input)
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
        inp = self.query_one("#cb-input", Input)
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            inp.value = self._history[self._history_index]
        else:
            self._history_index = -1
            inp.value = self._history_draft
            self._history_draft = ""
        inp.cursor_position = len(inp.value)

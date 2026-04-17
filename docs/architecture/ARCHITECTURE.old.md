# git-study Architecture

> 최종 업데이트: 2026-04-15 | 대상: tui_v2 (현재 작업 버전)

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [레이어 구조](#2-레이어-구조)
3. [프론트엔드: TUI v2](#3-프론트엔드-tui-v2)
4. [백엔드: Services / Graphs / Domain](#4-백엔드-services--graphs--domain)
5. [공용 인프라: LLM / 설정 / 타입](#5-공용-인프라-llm--설정--타입)
6. [데이터 흐름: 전체 시나리오](#6-데이터-흐름-전체-시나리오)
7. [상태 저장 구조](#7-상태-저장-구조)
8. [의존성 및 기술 스택](#8-의존성-및-기술-스택)
9. [v1 레거시와의 관계](#9-v1-레거시와의-관계)
10. [확장 가이드](#10-확장-가이드)

---

## 1. 시스템 개요

**git-study**는 Git 커밋 히스토리를 기반으로 코드 학습 퀴즈를 생성·채점하는 AI 튜터 TUI 앱입니다.

```
┌─────────────────────────────────────────────────────────┐
│                   git-study v2                           │
│                                                          │
│  사용자 → TUI(Textual) → 명령 파싱 → AI 서비스(LangGraph) │
│                            ↓                             │
│              Git 저장소 (로컬 / GitHub)                   │
└─────────────────────────────────────────────────────────┘
```

### 주요 기능

| 기능 | 설명 |
|------|------|
| `/quiz` | 커밋 diff 기반 인라인 퀴즈 생성 |
| `/grade` | AI 채점 + 피드백 + 약점 분석 |
| 채팅 | 커밋 컨텍스트 포함 멀티턴 대화 |
| `/repo` | 로컬·GitHub 저장소 전환 |
| `/map` | 파일별 역할 요약 |

---

## 2. 레이어 구조

```
┌──────────────────────────────────────────────────────────────┐
│  TUI Layer  (tui_v2/)                                        │
│  GitStudyAppV2 · CommandBar · InlineCodeView · HistoryView   │
│  Screens: CommitPicker · QuizList · RepoPicker · ThreadPicker│
└──────────────────────┬───────────────────────────────────────┘
                       │ Iterator[dict] (스트리밍)
┌──────────────────────▼───────────────────────────────────────┐
│  Service Layer  (services/)                                   │
│  stream_inline_quiz · stream_inline_grade · stream_chat · …  │
└──────────────────────┬───────────────────────────────────────┘
                       │ graph.stream()
┌──────────────────────▼───────────────────────────────────────┐
│  Graph Layer  (graphs/)                                       │
│  inline_quiz_graph · quiz_graph · chat_graph · read_graph …  │
│  (LangGraph StateGraph — Draft → Review → [Repair] → Final)  │
└───────────┬──────────────────┬───────────────────────────────┘
            │                  │
┌───────────▼──────┐  ┌────────▼───────────────────────────────┐
│  Domain Layer    │  │  LLM Layer                              │
│  (domain/)       │  │  (llm/ · prompts/ · tools/)             │
│  repo_context    │  │  LLMClient · invoke_structured          │
│  code_context    │  │  invoke_json_with_tools · TokenTracker  │
│  inline_anchor   │  └─────────────────────────────────────────┘
│  repo_cache      │
└───────────┬──────┘
            │
┌───────────▼──────────────────────────────────────────────────┐
│  Git Layer  (GitPython)                                       │
│  로컬 Repo · GitHub 원격 캐시 · diff · commit tree           │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 프론트엔드: TUI v2

### 3.1 컴포넌트 계층

```
GitStudyAppV2 (App)
├── HistoryView          ← 채팅·결과 로그 (Markdown, Static, LoadingRow)
├── InlineCodeView       ← 파일 트리 + 코드 + 퀴즈 블록
│   ├── Tree (파일 목록)
│   ├── TextArea (코드, 읽기 전용)
│   └── InlineQuizBlock[] (앵커 기반 퀴즈 위젯)
├── CommandBar           ← 하단 입력창 + 자동완성 + 힌트
│   ├── AppStatusBar     ← 상단: 저장소·범위·모드·진행률
│   └── CommandInput     ← TextArea 기반 입력
└── Modal Screens (push_screen)
    ├── CommitPickerScreen  (커밋 범위 선택)
    ├── QuizListScreen      (세션별 퀴즈 목록, 2패널)
    ├── RepoPickerScreen    (저장소 전환)
    └── ThreadPickerScreen  (대화 스레드 선택)
```

### 3.2 앱 모드 상태머신

```
IDLE
 ├─[/quiz]──→ QUIZ_LOADING ──[완료]──→ QUIZ_ANSWERING
 │                                        ├─[/grade]──→ GRADING ──→ REVIEWING
 │                                        └─[ESC/Shift+Tab]──→ IDLE
 └─[텍스트]──→ CHATTING ──[/quiz]──→ QUIZ_LOADING
```

| 모드 | 설명 | 가용 액션 |
|------|------|----------|
| `idle` | 대기 | 모든 명령 |
| `quiz_loading` | 퀴즈 생성 중 | 취소 불가 (스트리밍) |
| `quiz_answering` | 답변 입력 중 | Enter(제출), Shift+↑↓(이동), ESC(종료) |
| `grading` | 채점 중 | 취소 불가 |
| `reviewing` | 결과 검토 | /quiz, /clear, /resume |
| `chatting` | 자유 대화 | 텍스트 입력, /quiz |

> **규칙**: 모드 전환은 반드시 `_set_mode(mode)` 사용 (AppStatusBar + 힌트 동기화).  
> `quiz_answering` 진입/탈출 시 `_refresh_quiz_blocks_state()` 호출 필수.

### 3.3 이벤트 흐름

```
사용자 입력
  ↓
CommandBar.on_input_submitted()
  ↓
parse_command(text) → ParsedCommand(kind, range_arg, ...)
  ↓
GitStudyAppV2._handle_parsed_command(cmd)
  ├─ "quiz"  → _start_quiz()    @work(thread=True)
  ├─ "grade" → _grade_answers() @work(thread=True)
  ├─ "chat"  → stream_chat()    @work(thread=True)
  ├─ "repo"  → push_screen(RepoPickerScreen)
  └─ ...
```

### 3.4 주요 위젯

| 위젯 | 역할 | 핵심 메서드 |
|------|------|------------|
| `AppStatusBar` | 저장소·모드·진행률 표시 | `set_mode()`, `set_quiz_progress()` |
| `CommandBar` | 명령 입력·자동완성·힌트 | `update_context_hint()`, `_set_status()` |
| `HistoryView` | 결과·채팅 로그 | `append_loading_row()`, `update_loading_row()` |
| `InlineCodeView` | 코드+퀴즈 렌더링 | `_show_questions()`, `_update_quiz_blocks()` |
| `InlineQuizBlock` | 단일 퀴즈 위젯 | `focus_answer_input()`, `show_grade()` |

### 3.5 Textual 핵심 패턴

```python
# 백그라운드 작업
@work(thread=True)
def _start_quiz(self): ...

# 메인 스레드 복귀
self.call_from_thread(self._set_mode, "quiz_answering")

# 모달 스크린
self.push_screen(CommitPickerScreen(...), self._on_commit_picker_result)

# CSS 클래스 토글 (코드뷰 ↔ 채팅뷰)
self.query_one("Screen").toggle_class("-code-active")
```

---

## 4. 백엔드: Services / Graphs / Domain

### 4.1 Service Layer

모든 서비스는 `Iterator[dict]` 스트리밍 패턴으로 TUI에 진행 상황을 전달합니다.

| 서비스 함수 | 연결 그래프 | 역할 |
|------------|------------|------|
| `stream_inline_quiz_progress()` | `inline_quiz_graph` | 코드 앵커 기반 퀴즈 생성 |
| `stream_inline_grade_progress()` | `inline_grade_graph` | 인라인 퀴즈 채점 |
| `stream_general_grade_progress()` | `general_grade_graph` | 일반 퀴즈 채점 |
| `stream_quiz_progress()` | `quiz_graph` | 커밋 분석 기반 일반 퀴즈 |
| `stream_read_progress()` | `read_graph` | 학습용 읽기 자료 생성 |
| `stream_map_progress()` | 직접 LLM | 파일별 역할 요약 |
| `stream_chat()` | `chat_graph` | 멀티턴 대화 (SqliteSaver) |

**스트리밍 이벤트 타입**:
```python
{"type": "node",   "node": "...", "label": "한국어 레이블"}
{"type": "token",  "content": "..."}          # chat 전용
{"type": "usage",  "input_tokens": N, ...}
{"type": "result", "result": {...}}
{"type": "error",  "message": "..."}
```

### 4.2 Graph Layer (LangGraph)

모든 그래프는 **Draft → Review → [Repair] → Finalize** 패턴을 따릅니다.

```
START → prepare → generate/draft → review → [repair(최대 2회)] → finalize → END
```

| 그래프 | 특이사항 |
|--------|---------|
| `inline_quiz_graph` | 도구 호출(`get_neighbor_code_context`) 포함, repair 최대 2회 |
| `quiz_graph` | `commit_analysis_subgraph` 재사용, repair 최대 1회 |
| `read_graph` | repair 최대 2회 |
| `inline_grade_graph` | 순차(repair 없음) |
| `general_grade_graph` | 순차, `GradingSummary` 포함 |
| `chat_graph` | 멀티 에이전트 라우팅, SqliteSaver 체크포인트, 무한 순환 |

**Chat 그래프 에이전트 라우팅**:
```
supervisor
  ├─ route: "commit_question" → code_reviewer
  ├─ route: "quiz_question"   → quiz_explainer
  ├─ route: "learning_path"   → learning_advisor
  └─ route: "general"         → general_assistant
                ↓
           [tools 노드] → 순환
```

### 4.3 Domain Layer

**repo_context.py** — Git 접근 진입점
```python
build_commit_context(commit, repo) → {
  "commit_sha", "commit_subject", "diff_text",      # max 12KB
  "file_context_text",                               # max 24KB
  "changed_files_summary"
}
build_multi_commit_context(commits, repo) → 범위 집계
```

**code_context.py** — 파일 내용 추출
```python
MAX_DIFF_CHARS = 12_000
MAX_FILE_CONTEXT_CHARS = 24_000
MAX_FILE_CONTEXT_FILES = 10

get_file_content_at_commit_or_empty()  # 에러 시 "" 반환
format_hunk_file_context_block()        # hunk 중심 ±30줄만 추출
```

**inline_anchor.py** — 퀴즈 앵커 검증
```python
parse_file_context_blocks()    # "FILE: path" 세그먼트 파싱
snippet_exists_in_content()    # 앵커 스니펫 존재 확인
```

**repo_cache.py** — 원격 저장소 캐시
- 경로: `~/Library/Caches/git-study/github/` (macOS)
- 30일 미사용 시 자동 삭제
- `origin.fetch(prune=True)` 로 최신화

---

## 5. 공용 인프라: LLM / 설정 / 타입

### 5.1 공용 TypedDict (types.py)

| 타입 | 용도 |
|------|------|
| `CommitListSnapshot` | 커밋 목록 조회 결과 |
| `CommitHead` | 커밋 메타데이터 (sha, subject, author, date) |
| `InlineQuizQuestion` | 인라인 질문 (앵커 포함) |
| `InlineQuizGrade` | 인라인 채점 결과 (score 0-100, feedback) |
| `GeneralQuizQuestion` | 일반 퀴즈 (choices, code_snippet 등 선택적) |
| `GradingSummary` | 채점 요약 (weak_points, next_steps) |
| `ChatEvent` | TUI 채팅 이벤트 (kind, content, style) |
| `RemoteRepoCacheEntry` | GitHub 캐시 메타데이터 |

### 5.2 LLM 클라이언트 (llm/client.py)

```python
class LLMClient:
    invoke_text(prompt) → str
    invoke_json(prompt) → dict               # 마크다운 펜스 자동 제거
    invoke_json_with_tools(prompt, tools)    # 최대 4라운드 도구 호출
    invoke_structured(prompt, schema)        # Pydantic — 4단계 폴백
```

**invoke_structured 폴백 순서**:
1. `json_schema` (strict=True)
2. `json_schema` (strict=False)
3. `json_mode`
4. `invoke_json` (최후 수단)

**모델 선택**:
```python
model_override_var: ContextVar[str]  # 호출 단위 오버라이드
# 기본값: settings.json의 model 필드
```

### 5.3 설정 관리

| 파일 | 저장 위치 | 내용 |
|------|----------|------|
| `settings.json` | `~/.git-study/settings.json` | 모델 선택 |
| `secrets.json` | `~/.git-study/secrets.json` | OpenAI API 키 |
| `state.json` | `.git-study/state.json` | 선택 SHA 범위 |
| `sessions/{id}.json` | `.git-study/sessions/` | 퀴즈 세션 데이터 |

**API 키 우선순위**: 환경변수(`OPENAI_API_KEY`) → 파일 → 세션 메모리

### 5.4 프롬프트 구조

12개 프롬프트 파일, 총 645줄. 모두 한국어 기본, 함수로 빌드:

```python
build_inline_combined_prompt(diff_text, file_context, ...) → str
build_quiz_generation_prompt(commit_context, count, ...) → str
```

---

## 6. 데이터 흐름: 전체 시나리오

### 시나리오: `/quiz HEAD~3`

```
사용자: "/quiz HEAD~3"
  ↓ CommandBar
parse_command() → ParsedCommand(kind="quiz", range_arg="HEAD~3")
  ↓ App
_set_mode("quiz_loading")
_start_quiz()  @work(thread=True)
  ↓ Domain
build_multi_commit_context(["HEAD~3", "HEAD~2", "HEAD~1", "HEAD"])
  → {diff_text, file_context_text, changed_files_summary}
  ↓ Service
stream_inline_quiz_progress(commit_context, count=3)
  ↓ Graph (inline_quiz_graph)
  [prepare] parse_file_context_blocks()
  [generate] LLMClient.invoke_json_with_tools() + get_neighbor_code_context
  [review]  LLMClient.invoke_structured() → is_valid?
  [repair?] 최대 2회
  [finalize] InlineQuizQuestion[]
  ↓ 스트리밍 이벤트
  {"type": "node", ...} × N   ← HistoryView 실시간 업데이트
  {"type": "result", ...}
  ↓ App (call_from_thread)
_set_mode("quiz_answering")
_show_questions() → InlineCodeView 렌더링
InlineQuizBlock[0].focus_answer_input()
```

### 시나리오: `/grade`

```
사용자: "/grade"
  ↓
_grade_answers()  @work(thread=True)
  ↓ Service
stream_inline_grade_progress(questions, answers)
  ↓ Graph (inline_grade_graph)
  [prepare]  Question + Answer + Expected 블록 구성
  [grade]    LLMClient.invoke_structured() → raw_grades
  [validate] LLMClient.invoke_structured() → GradeReviewStructuredOutput
  [finalize] final_grades + GradingSummary
  ↓
_set_mode("reviewing")
InlineCodeView 채점 결과 갱신
```

### 시나리오: 자유 채팅

```
사용자: "이 커밋에서 가장 중요한 변경이 뭔가요?"
  ↓
_set_mode("chatting")
stream_chat(thread_id, user_text, commit_context)
  ↓ Graph (chat_graph, SqliteSaver 체크포인트)
  supervisor → route="commit_question"
  code_reviewer → tools(get_file_content, list_changed_files) → 순환
  ↓ 토큰 스트리밍
  {"type": "token", "content": "..."} × N   ← HistoryView 실시간
  {"type": "result"}
  ↓
_set_mode("idle")
```

---

## 7. 상태 저장 구조

```
프로젝트 디렉토리/
└── .git-study/
    ├── state.json              # 선택 SHA 범위 (oldest/newest)
    └── sessions/
        └── {oldest7}-{newest7}.json  # questions, answers, grades

홈 디렉토리/
└── ~/.git-study/
    ├── settings.json           # model 설정
    └── secrets.json            # OPENAI_API_KEY

LangGraph 체크포인트/
└── .git-study/chat_{thread_id}.db  # SQLite (멀티턴 대화 히스토리)

원격 캐시/
└── ~/Library/Caches/git-study/github/{slug}/  # clone 캐시 (30일)
```

---

## 8. 의존성 및 기술 스택

| 레이어 | 패키지 | 버전 |
|--------|--------|------|
| **AI/LLM** | langchain[openai] | ≥0.3.27 |
| **워크플로우** | langgraph | ≥0.6.6 |
| **체크포인트** | langgraph-checkpoint-sqlite | ≥2.0.11 |
| **TUI** | textual | ≥0.85.2 |
| **Git** | gitpython | ≥3.1.46 |
| **DB** | aiosqlite | ≥0.21.0 |
| **웹 UI** | streamlit | ≥1.40.0 |
| **환경변수** | python-dotenv | ≥1.1.1 |
| **Python** | — | ≥3.13 |

**엔트리포인트**:
```toml
[project.scripts]
git-study       = "git_study.tui_v2:run_v2"       # 현재
git-study-v1    = "git_study.tui:run"              # 레거시
git-study-streamlit = "git_study.streamlit_app:run_chat"
```

---

## 9. v1 레거시와의 관계

v2는 v1을 대체하되, 일부 유틸을 재사용하는 **점진적 마이그레이션** 전략입니다.

| v2 파일 | v1 임포트 | 재사용 이유 |
|---------|----------|------------|
| `screens/commit_picker.py` | `tui.commit_selection`, `tui.state` | 커밋 선택 UI + 세션 저장 |
| `screens/quiz_list.py` | `tui.inline_quiz`, `tui.state` | 질문 타입 상수 + 세션 로드 |
| `app.py` | `tui.commit_selection`, `tui.state` | CommitSelection, 상태 경로 |
| `widgets/inline_code_view.py` | `tui.code_browser`, `tui.inline_quiz` | 신택스 하이라이팅, 앵커 유틸 |

**공유 vs 버전별 분리**:
```
공유 (버전 무관): graphs/ · services/ · domain/ · llm/ · prompts/ · types.py
버전별 분리:      tui/ (v1) vs tui_v2/ (v2) — UI 구현만 분리
```

---

## 10. 확장 가이드

### 새 명령어 추가 (3곳 수정)

```python
# 1. commands.py — CommandKind Literal + parse_command() 분기
# 2. command_bar.py — _COMMANDS 목록 + 자동완성 후보
# 3. app.py — case "커맨드명": 핸들러
```

### 새 AI 서비스 추가

```python
# 1. graphs/new_graph.py — StateGraph 정의
# 2. services/new_service.py — stream_new_service() → Iterator[dict]
# 3. app.py — 서비스 호출 + 이벤트 처리
```

### 새 Chat 에이전트 추가

```python
# chat_graph.py에 노드 추가
def new_agent_node(state: MultiAgentChatState) -> dict:
    response = bound_llm.invoke(...)
    return {"messages": [response]}

# supervisor 라우팅 규칙 추가
if route == "new_route":
    return "new_agent"
```

### InlineQuizQuestion 새 질문 타입

`types.py`의 `question_type` Literal에 추가 후, `inline_question.py` 프롬프트에 설명 추가.

---

*이 문서는 3개의 병렬 분석 에이전트(frontend / backend / misc)의 결과를 통합하여 작성되었습니다.*

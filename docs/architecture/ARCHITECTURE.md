# git-study Architecture

> 4개 레이어 병렬 분석을 종합한 아키텍처 문서.
> 작성일: 2026-04-17 / 버전: 0.2.10 / 분석 범위: `src/git_study/` 전체

---

## 1. 시스템 개요

git-study는 **Git 커밋을 학습 자료로 변환하는 LLM 기반 학습 도구**다. 사용자는 임의의 Git 저장소(로컬/GitHub)에서 커밋 범위를 선택하고, AI가 그 변경 사항에 기반한 인라인 퀴즈를 생성·채점·해설한다.

### 핵심 개념

- **Inline Quiz** — 코드의 특정 라인에 앵커링된 퀴즈. 코드 옆에 위젯으로 표시.
- **Session** — 커밋 범위 단위로 묶인 학습 단위 (`{oldest_sha[:7]}-{newest_sha[:7]}`).
- **Multi-agent Chat** — supervisor 라우터가 commit/quiz/learning/general 분류 후 전문 에이전트로 분기.
- **Repair Loop** — LLM 응답 품질이 낮으면 review→repair 사이클 (최대 1~2회).

### 4계층 구조

```
┌──────────────────────────────────────────────────────┐
│  TUI Layer (Textual)                                 │
│  tui_v2/ — App, Screens, Widgets, Commands           │
└──────────────┬───────────────────────────────────────┘
               │ stream_*_progress() (Iterator[dict])
┌──────────────▼───────────────────────────────────────┐
│  AI/LangGraph Layer                                  │
│  services/ → graphs/ → llm/, tools/, prompts/        │
└──────────────┬───────────────────────────────────────┘
               │ build_*_context() / get_repo()
┌──────────────▼───────────────────────────────────────┐
│  Domain Layer                                        │
│  domain/ — Git 접근, 커밋·파일 컨텍스트, 퀴즈 파싱   │
└──────────────┬───────────────────────────────────────┘
               │ Path / JSON / Git
┌──────────────▼───────────────────────────────────────┐
│  Persistence                                         │
│  ~/.git-study/, ~/.cache/git-study/, .git-study/    │
└──────────────────────────────────────────────────────┘
```

---

## 2. 진입점 & 빌드

### Console Scripts (`pyproject.toml:20-23`)

| 스크립트 | 타겟 | 설명 |
|---------|------|------|
| `git-study` | `git_study.tui_v2:run_v2` | **v2 TUI** (현재 메인) |
| `git-study-v1` | `git_study.tui:run` | v1 TUI (레거시) |
| `git-study-streamlit` | `git_study.streamlit_app:run_chat` | Streamlit 웹 앱 |

### 핵심 의존성 (`pyproject.toml`)

| 라이브러리 | 버전 | 역할 |
|-----------|------|------|
| `textual` | ≥0.85.2 | TUI 프레임워크 |
| `langgraph` | ≥0.6.6 | 그래프 워크플로우 |
| `langgraph-checkpoint-sqlite` | ≥2.0.11 | 채팅 thread 영속화 |
| `langchain` | ≥0.3.27 | LLM 통합 |
| `langchain-openai` | 1.1.12 | OpenAI 클라이언트 |
| `gitpython` | ≥3.1.46 | Git 저장소 접근 |
| `python-dotenv` | ≥1.1.1 | `.env` 자동 로드 |
| `streamlit` | ≥1.40.0 | 웹 UI |

- **Python**: ≥3.13
- **빌드**: `setuptools` (src layout, `package-dir = {"" = "src"}`)
- **패키지 매니저**: `uv` (UV_CACHE_DIR 강제 권장 — CLAUDE.md 참고)

### 환경 변수 (`.env.example`)

`OPENAI_API_KEY` 외 9종 (Anthropic, Perplexity, Google, Mistral, xAI, Groq, OpenRouter, Azure, Ollama, GitHub) 정의되어 있으나 **현재 코드는 OpenAI 키만 실사용**한다.

---

## 3. Domain Layer

### 모듈 책임

| 모듈 | 책임 |
|------|------|
| `types.py` | 도메인 TypedDict (`CommitListSnapshot`, `InlineQuizQuestion`, `RemoteRepoCacheEntry` 등) |
| `runtime_paths.py` | 전역 런타임 디렉토리 (`GIT_STUDY_HOME` → `~/.git-study`) |
| `secrets.py` | 3계층 API 키 우선순위: env → file → session 메모리 |
| `settings.py` | 앱 설정 (현재는 모델명만) |
| `update_checker.py` | PyPI 버전 비교 (3초 타임아웃) |
| `domain/repo_context.py` | Git 저장소 접근, 커밋 리스트, 단일/범위 컨텍스트 빌드 |
| `domain/code_context.py` | diff/파일 추출, hunk 기반 컨텍스트 포매팅 |
| `domain/inline_anchor.py` | `FILE: ...\n\`\`\`...` 마크다운 코드 블록 파싱 |
| `domain/repo_cache.py` | GitHub 원격 저장소 캐싱 + 30일 자동 정리 |
| `domain/general_quiz.py` | 퀴즈 마크다운 렌더링 + 부족분 fallback 보충 |
| `domain/quiz_parsing.py` | LLM 출력(`### 질문 N`) 정규식 파싱 |
| `domain/repo_map.py` | 파일 트리 + 커밋 빈도 분석 |

### 핵심 함수 (file:line)

- `repo_context.get_repo()` (`repo_context.py:28`) — local/github 통합 진입점
- `repo_context.get_commit_list_snapshot()` (`repo_context.py:119`) — 페이지네이션 가능 커밋 리스트
- `repo_context.build_commit_context()` / `build_multi_commit_context()` — diff + 파일 컨텍스트 묶음
- `code_context.build_file_context_text()` — 변경 파일들을 LLM 입력용으로 포매팅
- `code_context.format_hunk_file_context_block()` — diff hunk 주변 ±30줄 컨텍스트
- `quiz_parsing.parse_quiz_markdown_questions()` — `### 질문 N` 섹션 정규식 추출

### 컨텍스트 크기 제한 (`code_context.py:7-11`)

| 상수 | 값 | 용도 |
|------|----|------|
| `MAX_DIFF_CHARS` | 12,000 | diff_text 자르기 |
| `MAX_FILE_CONTEXT_CHARS` | 24,000 | 전체 파일 컨텍스트 |
| `MAX_FILE_CONTEXT_FILES` | 10 | 최대 포함 파일 수 |
| `MAX_FILE_SNIPPET_CHARS` | 3,000 | 단일 파일 스니펫 |
| `HUNK_CONTEXT_LINES` | 30 | hunk 주변 컨텍스트 |
| `MAX_COMMITS_TO_SCAN` | 8 | auto 모드 최대 스캔 수 |

### 의존성 그래프 (Domain 내부)

```
types ◄── repo_cache ◄── repo_context ◄── (services/*)
runtime_paths ◄── secrets, settings, repo_cache
code_context ◄── repo_context
inline_anchor (독립) ◄── inline_quiz_service
general_quiz, quiz_parsing ◄── quiz 그래프
repo_map (독립) ◄── map 서비스
```

---

## 4. AI / LangGraph Layer

### 서비스 진입점 (7개)

| 서비스 | 스트리밍 진입점 | 용도 |
|--------|---------------|------|
| `inline_quiz_service` | `stream_inline_quiz_progress()` | 코드 라인에 앵커된 퀴즈 생성 |
| `inline_grade_service` | `stream_inline_grade_progress()` | 인라인 퀴즈 답안 채점 |
| `quiz_service` | `stream_quiz_progress()` | 일반(non-inline) 퀴즈 생성 |
| `general_grade_service` | `stream_general_grade_progress()` | 일반 퀴즈 채점 |
| `read_service` | `stream_read_progress()` | 학습 가이드 (마크다운) 생성 |
| `map_service` | `stream_map_progress()` / `stream_full_map_progress()` | 저장소 구조 요약 (그래프 미사용) |
| `chat_service` | `stream_chat()` | 멀티턴 대화 (SqliteSaver thread) |

스트리밍 yield 타입: `{"type": "node"|"usage"|"result"|"token"|"tool_call"|"action"|"done"|"error", ...}`

### 그래프별 흐름 (6개)

#### inline_quiz_graph

```
prepare_inline_context → generate_with_anchor → review_inline_questions
                                                       │
                          repair_inline_questions ◄────┤ (review.is_valid=false, attempts<2)
                                       │
                                       ▼
                          finalize_inline_questions  (fallback 보충)
```

- `generate_with_anchor`는 `require_tool=True`로 `get_neighbor_code_context` 도구 호출 강제
- `repair` 최대 2회

#### quiz_graph / read_graph

```
resolve_commit_context → analyze_change → draft_* → review_* → repair_* (≤1~2회) → finalize_*
```

- `commit_analysis_subgraph` (resolve_commit_context, analyze_change)을 두 그래프가 공유
- `read`는 `invoke_text` (일반 텍스트), `quiz`는 `invoke_structured`

#### inline_grade_graph / general_grade_graph

```
prepare_grading_payload → grade_answers → validate_grades → finalize_grades  (선형, 분기 없음)
```

#### chat_graph (멀티 에이전트 라우터)

```
              supervisor
                  │
       ┌──────────┼──────────┬──────────────────┐
       ▼          ▼          ▼                  ▼
action_responder code_reviewer quiz_explainer learning_advisor general_assistant
       │           │             │                              │
       └──── tools (ToolNode: list_changed_files, get_file_content, list_all_files) ────┘
                              │
                              ▼
                             END
```

- `supervisor` 분류: `route ∈ {commit_question, quiz_question, learning_path, general}`, `actions ∈ {quiz, review, grade, map, none}`
- `learning_advisor`만 도구 미사용 (pure LLM)
- `SqliteSaver` 경로: `~/.git-study/chat_{repo_source}.db` — `thread_id`별 히스토리 영속

### LLM Client (`llm/client.py`)

#### 메서드

| 메서드 | 반환 |
|--------|------|
| `invoke_text(prompt)` | `str` |
| `invoke_json(prompt)` | `dict` (extract_json_block → json.loads) |
| `invoke_json_with_tools(prompt, tools, require_tool, max_rounds=4)` | tool 호출 루프 |
| `invoke_structured(prompt, schema, method="json_schema", strict=True)` | 5단계 fallback |

#### `invoke_structured` Fallback 5단계

1. `json_schema` strict=True
2. retry_prompt + `json_schema` strict=True
3. `json_schema` strict=False
4. `json_mode` 메서드
5. `invoke_json()` 폴백

> **Unretryable**: `context_length_exceeded` 또는 `400`+`maximum context length` 즉시 throw.

#### 모델 오버라이드

`ContextVar model_override_var` 사용 (스레드 안전, finally에서 reset).

### Tools (`tools/code_context.py`)

- `get_neighbor_code_context(file_path, anchor_line, before=8, after=8)` — 1-indexed 라인 번호 포함 스니펫 반환
- `inline_quiz` 그래프에서 `require_tool=True`로 강제 호출 (앵커 검증)

### Prompts (`prompts/`)

| 파일 | 출력 스키마 | 사용 노드 |
|------|-----------|---------|
| `quiz_analysis.py` | `QuizAnalysisStructuredOutput` | quiz/read: analyze_change |
| `inline_question.py` | `list[InlineQuestionStructuredOutput]` | inline_quiz: generate_with_anchor |
| `inline_question_review.py` | `InlineQuestionReviewStructuredOutput` | inline_quiz: review |
| `inline_grade.py` | `InlineGradeListStructuredOutput` | inline_grade: grade_answers |
| `inline_grade_review.py` | `InlineGradeReviewStructuredOutput` | inline_grade: validate |
| `quiz_generation.py` | `GeneralQuestionListStructuredOutput` | quiz: draft |
| `quiz_review.py` | `QuizReviewStructuredOutput` | quiz: review |
| `general_grade.py` | `GeneralGradeListStructuredOutput` | general_grade: grade |
| `general_grade_review.py` | `GeneralGradeReviewStructuredOutput` | general_grade: validate |
| `read_generation.py` | text (마크다운) | read: draft |
| `read_review.py` | `ReadReviewStructuredOutput` | read: review |

### 응답 정규화 (`llm/schemas.py:110-372`)

`normalize_quiz_analysis`, `normalize_inline_questions`, `normalize_inline_grades` 등 9개 함수.

- 모호한 표현 필터 (예: "대규모 업데이트" 제거)
- ID 중복 제거 (seen_ids 세트)
- 범위 제한 (snippets≤4, questions≤4)
- 점수 [0,100] clamp, 유효 question_type만 허용

---

## 5. TUI Layer (`tui_v2/`)

### 앱 모드 머신

```
idle ──/quiz──► quiz_loading ──► quiz_answering ──► idle
     ──/grade─► grading        ─────────────────► idle
     ──/review► reviewing      ─────────────────► idle
     ──/chat──► chatting       ─────────────────► idle
```

- 모드 변경은 반드시 `_set_mode(mode)` (AppStatusBar + CommandBar hint + InlineQuizBlock 클래스 동기화)

### App 클래스 (`tui_v2/app.py:GitStudyAppV2`)

#### 핵심 상태

| 필드 | 타입 | 의미 |
|------|------|------|
| `_mode` | str | 모드 머신 |
| `_commits` | list[dict] | newest-first 커밋 목록 |
| `_repo_source` | str | "local" / "github" |
| `_oldest_sha`, `_newest_sha` | str | 선택 범위 SHA |
| `_commit_selection` | `CommitSelection` | start/end 인덱스 (frozen dataclass) |
| `_questions` | list[InlineQuizQuestion] | 현재 세션 퀴즈 |
| `_answers` | dict[str, str] | qid → 답변 |
| `_grades` | list[InlineQuizGrade] | 채점 결과 |
| `_current_q_index` | int | 활성 퀴즈 |
| `_current_log_block` | Vertical | 마지막 명령 결과 컨테이너 |
| `_progress_rows` | dict[str, LoadingRow] | op_id → 진행 위젯 |

#### 라이프사이클

1. `on_mount()` — 저장소 로드, 상태 복원
2. `_load_local_repo()` — 로컬 탐지, GitHub 캐시 복원
3. `_apply_commits()` — 커밋 적용, 선택 SHA 복원, 세션 복원

### Screens (모달)

| 모달 | 결과 타입 |
|------|---------|
| `CommitPickerScreen` | `CommitSelection \| None` (S/E/· 마커, 세션 마커, "Load More") |
| `QuizListScreen` | (좌: 세션 목록 / 우: 퀴즈 카드, 2패널) |
| `RepoPickerScreen` | 저장소 전환 (로컬 최근 + GitHub 캐시) |
| `ThreadPickerScreen` | 채팅 스레드 선택/생성 |

### Widgets

#### CommandBar

```
┌─ cb-alert (스피너+플래그) ─ cb-progress (진행률) ──┐
│  cb-status (컨텍스트 힌트)                         │
│  ❯ [TextArea 입력창]                              │
│  [자동완성 7줄]                                    │
│  mode-bar | app-status                            │
└────────────────────────────────────────────────────┘
```

자동완성: `/quiz`, `/review`, `/map`, `/hook`, `/apikey` + `@파일`(quiz refs `@1, @2`).

#### AppStatusBar

`repo | old7..new7(count) | hook:ON/OFF | MODE progress`
- IDLE: dim / QUIZ N/M: color(214) / LOADING/GRADING: bright_yellow

#### HistoryView

- `FullLogoAnimated`: 물방울 드리프트
- `append_command()` → 결과 붙일 Vertical 반환
- `begin_streaming()` → 누적 갱신용 Static
- `LoadingRow`: 스피너 + 경과시간

#### InlineCodeView

- 좌측: 파일 트리 (변경 파일 bold green, `● N`/`✔ N`/`★ N` 마커)
- 우측: 코드 (추가=초록, 삭제=빨강 hunk diff) + `InlineQuizBlock`
- CSS: `-active` / `-answered` / `-graded`
- 활성 여부: `is_answering = (_mode == "quiz_answering")` ↔ `_refresh_quiz_blocks_state()` 매번 호출

### 명령어 시스템 (`commands.py`)

```python
ParsedCommand:
    kind: CommandKind          # quiz | grade | review | chat | unknown
    range_arg: str             # "HEAD~3" 또는 메시지
    quiz_count: int            # 1~10 (기본 3)
    author_context: str        # self | ai | others
    model_override: str | None # --model=xxx
    mentioned_files: tuple     # [(path, start, end), ...]
    mentioned_quizzes: tuple   # [1, 2, 3]
```

### 서비스 연동 패턴

```python
@work(thread=True)
def _start_quiz(...):
    for chunk in stream_inline_quiz_progress(...):
        self.call_from_thread(self._on_quiz_chunk, chunk)
    self.call_from_thread(self._handle_quiz_loaded)
```

- 워커 스레드에서 스트리밍 소비, UI 업데이트는 `call_from_thread`로 메인 스레드 위임

### 키 바인딩

| 키 | 액션 |
|----|------|
| Ctrl+Q | quit |
| Tab | global_tab (priority=True) |
| Shift+Tab | toggle_view (chat ↔ code) |
| Shift+↑/↓ | prev/next_question |
| F1 | quiz_hint (`@N` 삽입) |
| ESC | escape_to_cmdbar |

답변 모드(TextArea): Enter=제출 / Shift+Enter=줄바꿈 / Shift+Tab=채팅뷰 / ESC=cmd-bar.

### v1 → v2 의존성

v2가 import하는 v1 모듈:

| v1 파일 | v2 사용처 |
|---------|---------|
| `tui/commit_selection.py` | `CommitSelection` 데이터클래스 |
| `tui/state.py` | 경로 헬퍼, 파일 I/O |
| `tui/code_browser.py` | `highlight_code_lines()` |
| `tui/inline_quiz.py` | `find_anchor_line()`, `QUESTION_TYPE_KO` |

---

## 6. 영구 저장소

| 경로 | 내용 | 포맷 | 관리 모듈 |
|------|------|------|---------|
| `~/.git-study/secrets.json` | `{"openai_api_key": "..."}` | JSON | `secrets.py` |
| `~/.git-study/settings.json` | `{"model": "gpt-4o-mini"}` | JSON | `settings.py` |
| `~/.git-study/chat_{repo_source}.db` | 채팅 thread 메시지 | SQLite | `langgraph-checkpoint-sqlite` |
| `.git-study/state.json` | 선택 SHA 범위 | JSON | `tui/state.py` |
| `.git-study/sessions/{session_id}.json` | questions / answers / grades | JSON | `tui_v2/app.py` |
| `~/.cache/git-study/github/{slug}/` | 원격 저장소 clone | Git repo | `repo_cache.py` |
| `~/.cache/git-study/github-meta/{slug}.json` | `{repo_url, cache_path, last_used_at}` | JSON | `repo_cache.py` |

원격 저장소 캐시는 30일(`REMOTE_CACHE_RETENTION_DAYS`) 미사용 시 `get_repo()` 호출마다 자동 정리.

---

## 7. End-to-End 데이터 흐름

### `/quiz HEAD~3` 처리 흐름

```
사용자 입력 "/quiz HEAD~3"
   ↓
CommandBar (commands.py: parse_command)
   ↓
GitStudyAppV2._start_quiz()  [worker thread]
   ↓
inline_quiz_service.stream_inline_quiz_progress(commit_context, count, ...)
   ↓
domain.repo_context.build_multi_commit_context(commits)
   ├─ get_repo(local|github)             # 캐시/원격 처리
   ├─ extract_patch_text(commit)
   ├─ sanitize_diff()                    # ≤12,000 chars
   └─ build_range_file_context_text()    # ≤24,000 chars / ≤10 파일
   ↓
inline_quiz_graph.stream(state, stream_mode="updates")
   ├─ prepare_inline_context             # 앵커 후보 추출
   ├─ generate_with_anchor               # LLM + get_neighbor_code_context (require_tool)
   ├─ review_inline_questions            # invoke_structured
   ├─ repair_inline_questions (≤2회)
   └─ finalize_inline_questions          # fallback 보충
   ↓
yield {"type": "node"|"usage"|"result"} 스트림
   ↓
GitStudyAppV2._on_quiz_chunk()  [call_from_thread]
   ↓
_handle_quiz_loaded() → InlineCodeView.mount_quiz_blocks()
   ↓
.git-study/sessions/{session_id}.json 저장
```

### 채팅(`/chat`) 흐름

```
stream_chat(thread_id, user_text, commit_context, quiz_context, ...)
   ↓
chat_graph.app.stream(payload, config={"configurable": {"thread_id": ...}}, stream_mode=["messages","updates"])
   ↓
SqliteSaver (~/.git-study/chat_{source}.db)
   ↓
supervisor → [code_reviewer | quiz_explainer | learning_advisor | general_assistant | action_responder]
   ↓ (도구 필요 시)
ToolNode (list_changed_files | get_file_content | list_all_files)
   ↓
yield {"type": "route"|"token"|"tool_call"|"action"|"done"|"error"}
```

---

## 8. 테스트 & 품질

### 테스트 디렉토리 (`tests/`)

| 파일 | 영역 |
|------|------|
| `test_inline_anchor.py` | 앵커 스니펫 파싱 |
| `test_quiz_parsing.py` | 퀴즈 마크다운 파싱 |
| `test_session_state.py` | 세션 상태 라이프사이클 |
| `test_schemas.py` | LLM 응답 정규화 |
| `test_services.py` | 서비스 스트림 + StubGraph |
| `test_tui_app.py` | v1 TUI 앱 로직 |
| `test_llm_client.py` | LLM 클라이언트 fallback |
| `test_graph_fallbacks.py` | LangGraph 분기 |
| `test_action_queue.py` | 액션 큐 |
| `conftest.py` | `src/`를 sys.path 주입 |

### 커버리지 갭

- `tui_v2/widgets/`, `tui_v2/screens/` (수동 테스트만)
- Streamlit 앱
- 엔드-투-엔드 LangGraph 워크플로우 (실제 LLM 호출)
- 에러 처리, 비정상 API 응답 시나리오

### 테스트 패턴

`StubGraph` + `monkeypatch`로 LangGraph/LLM 호출 차단:

```python
class StubGraph:
    def stream(self, payload, config=None, stream_mode=None):
        yield {"resolve_commit_context": {...}}
        yield {"finalize_quiz": {...}}

monkeypatch.setattr("git_study.services.quiz_service.quiz_graph", StubGraph())
```

### 도구

- **Ruff** — 린트/포맷 (`uv run ruff check`). `pyproject.toml`에 별도 설정 없음 (기본값).
- **Mypy** — 미설정 (타입 힌트는 있으나 CI 검증 없음).
- **Twine** — PyPI 배포.

### 워크플로우 (CLAUDE.md)

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync
UV_CACHE_DIR=/tmp/uv-cache uv run git-study-v2
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests
uv run ruff check
```

---

## 9. 알려진 함정

### Domain

1. **인코딩 손상** — `code_context.get_file_content_at_commit()`는 `errors="replace"`. 바이너리 파일 감지 없음.
2. **컨텍스트 자르기** — diff > 12KB / 파일 컨텍스트 > 24KB는 무손실 잘림. LLM 입력 품질 저하.
3. **Hunk ±30줄** — 큰 함수에서 맥락 부족 가능.
4. **inline_anchor 정규식** — 중첩 ``` 코드펜스 파싱 실패 가능 (라인 기반 역순 탐색으로 일부 우회).
5. **repo_cache 동시성** — 메타데이터 JSON에 lock 없음. 동시 사용 시 덮어쓰기 위험.
6. **30일 자동 삭제** — 의도치 않은 재 clone 발생 가능 (`REMOTE_CACHE_RETENTION_DAYS=30`).
7. **세션 메모리 키** — `_session_openai_api_key` 모듈 전역. 멀티스레드 시 lock 필요.

### AI

8. **`context_length_exceeded`** 즉시 throw (재시도 불가).
9. **`require_tool=True` + max_rounds=4** 초과 시 ValueError.
10. **Repair 한도** — inline_quiz≤2 / quiz≤1 / read≤2 (무한 루프 방지).
11. **Model override 누수** — `ContextVar` finally reset 필수.
12. **`json_schema` 미지원 모델** (GPT-4 base, GPT-3.5-turbo) → `json_mode` 자동 fallback.
13. **map_service**는 그래프 미사용. 직접 `LLMClient` 호출 + ContextVar 수동 관리.

### TUI

14. **모드 전환** — `_set_mode()` 우회 시 AppStatusBar/InlineQuizBlock 동기화 실패.
15. **Shift+Tab 흐름** — `_show_chat_view()` 먼저, `insert_mention()` 그 다음 (레이아웃 안정화).
16. **`@work(thread=True)`** — UI 갱신은 반드시 `call_from_thread()`.
17. **모달 스택 확인** — `len(self.screen_stack) > 1` 시 새 모달 차단.
18. **CSS 네임스페이스** — `cb-*` (CommandBar), `hv-*` (HistoryView), `iqb-*` (InlineQuizBlock), `icv-*` (InlineCodeView).

### Infra

19. **`UV_CACHE_DIR=/tmp/uv-cache`** 강제 — CI 자동화에서 누락 시 의존성 재설치 비용 폭증.
20. **v1 / v2 공존** — `tui/state.py`, `tui/commit_selection.py` 등은 공용. v2 작업 시 import 경로 혼동 주의.

---

## 10. 참고 문서

- `docs/PRD.md` — TypeScript 마이그레이션 제품 요구사항
- `docs/migration/MIGRATION_PLAN.md` — 마이그레이션 단계별 계획
- `docs/textual-key-handling.md` — Textual 키 이벤트 처리 가이드
- `docs/textual-color-system.md` — Textual 색상 시스템
- `CLAUDE.md` — 프로젝트 작업 규칙 (모드 전환, 명령어, 상태 저장)
- `docs/architecture/ARCHITECTURE.old.md` — 이전 버전 (백업)

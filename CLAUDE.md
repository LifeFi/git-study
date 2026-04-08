# CLAUDE.md

## 마이그레이션 계획

TypeScript 완전 전환 작업 예정. 자세한 계획은 `MIGRATION_PLAN.md` 참고.

---

## Commands

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync          # 의존성 설치
UV_CACHE_DIR=/tmp/uv-cache uv run git-study-v2        # v2 TUI (현재 작업 대상)
UV_CACHE_DIR=/tmp/uv-cache uv run git-study-streamlit # Streamlit 웹 앱
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests        # 테스트
uv run ruff check                                      # 린트
```

환경변수: `.env`에 `OPENAI_API_KEY` 필요. `load_dotenv()`로 자동 로드.

---

## Architecture (v2)

### 디렉토리 구조

```
src/git_study/
├── types.py                 ← 공용 TypedDict (InlineQuizQuestion, InlineQuizGrade 등)
├── runtime_paths.py         ← 전역 런타임 디렉토리 경로 헬퍼
├── secrets.py               ← API 키 로드/저장 (~/.git-study/secrets.json)
├── settings.py              ← 전역 설정 로드/저장 (~/.git-study/settings.json)
├── update_checker.py        ← PyPI 버전 업데이트 체크
├── tui_v2/                  ← 현재 작업 대상
│   ├── app.py               ← GitStudyAppV2 (메인 앱, 모드 관리, 이벤트 핸들링)
│   ├── commands.py          ← 명령어 파싱
│   ├── screens/
│   │   ├── commit_picker.py ← 커밋 범위 선택 모달
│   │   ├── quiz_list.py     ← 세션별 퀴즈 목록 모달 (2패널: 세션 목록 | 퀴즈 상세)
│   │   ├── repo_picker.py   ← 저장소 선택 모달
│   │   └── thread_picker.py ← 대화 스레드 선택 모달
│   └── widgets/
│       ├── app_status_bar.py   ← 상단 상태바
│       ├── command_bar.py      ← 하단 입력창 + 자동완성 + 상태 힌트
│       ├── history_view.py     ← 대화 히스토리
│       └── inline_code_view.py ← 파일 트리 + 코드 뷰 + 퀴즈 블록
├── tui/                     ← v1 레거시 (v2에서 일부 import)
│   ├── commit_selection.py  ← CommitSelection 데이터클래스
│   ├── state.py             ← 앱 상태 저장/로드
│   └── code_browser.py      ← highlight_code_lines()
├── domain/                  ← Git 접근, 컨텍스트 빌드
│   ├── repo_context.py      ← get_repo(), get_commit_list_snapshot(), build_commit_context()
│   ├── code_context.py      ← get_file_content_at_commit_or_empty(), detect_code_language()
│   ├── inline_anchor.py     ← find_anchor_line(), parse_file_context_blocks()
│   ├── repo_cache.py        ← 원격 저장소 캐시
│   ├── general_quiz.py      ← GeneralQuiz 질문 타입 레이블/유틸
│   ├── quiz_parsing.py      ← LLM 응답 퀴즈 파싱 (질문 섹션 regex)
│   └── repo_map.py          ← 저장소 구조 분석 (파일 트리 + 커밋 빈도)
├── services/                ← LangGraph AI 서비스 진입점
│   ├── inline_quiz_service.py    ← stream_inline_quiz_progress()
│   ├── inline_grade_service.py   ← stream_inline_grade_progress()
│   ├── general_grade_service.py  ← stream_general_grade_progress()
│   ├── quiz_service.py           ← stream_quiz_progress()
│   ├── read_service.py           ← stream_read_progress()
│   ├── map_service.py            ← stream_map_progress()
│   └── chat_service.py           ← stream_chat()
├── tools/
│   └── code_context.py      ← NeighborCodeContextInput LangChain 툴
├── graphs/                  ← LangGraph 워크플로우
├── prompts/                 ← LLM 프롬프트 템플릿
└── llm/                     ← LLM 클라이언트, 스키마
```

---

### 앱 모드 (`_mode`)

`idle` | `quiz_loading` | `quiz_answering` | `grading` | `reviewing` | `chatting`

- `quiz_answering`: 퀴즈 블록이 활성화된 상태. ESC/Shift+Tab 시 `idle`로 전환 필수.
- 모드 전환은 반드시 `_set_mode()`를 사용 (AppStatusBar + hint 동기화).

---

### 커밋 범위

- `CommitSelection(start_index, end_index)`: newest-first 인덱스 기반
- `_oldest_sha` / `_newest_sha`: 실제 SHA (도메인 레이어 전달용)
- `session_id`: `{oldest_sha[:7]}-{newest_sha[:7]}`

### /quiz 범위 인자

| 입력 | 해석 |
|------|------|
| `/quiz` | 현재 선택 범위 |
| `/quiz HEAD` | HEAD 1개 |
| `/quiz HEAD~3` | HEAD~3..HEAD |
| `/quiz A..B` | oldest/newest 자동 판별 |

---

### InlineCodeView

- **파일 트리**: 변경 파일 = bold green, `● N`(미답변) / `✔ N`(완료) / `★ N`(채점)
- **코드 뷰**: 추가=초록, 삭제=빨강 배경. Overview Ruler 클릭으로 스크롤.
- **InlineQuizBlock**: 앵커 스니펫으로 위치 결정. CSS 클래스: `-active` / `-answered` / `-graded`
  - 활성 블록: `border: round #ffff55`. 비활성 채점 블록: `border: round #666600`.
  - 활성 여부: `is_answering = (_mode == "quiz_answering")` — `_refresh_quiz_blocks_state()` 참조.
- 퀴즈 블록에서 ESC/Shift+Tab 발생 시 `_set_mode("idle")` + `_refresh_quiz_blocks_state()` 호출 필수.

### Textual 키 이벤트

Tab 등 특수 키를 커스텀 처리할 때 → `docs/textual-key-handling.md` 참고.

### CommandBar

- 프롬프트 `>` 고정 (삭제 불가)
- **답변 모드**: `Enter` = 제출, `Shift+Enter` = 줄바꿈, `Shift+↑↓` = 문제 이동, `ESC` = 종료
- 자동완성: `/hook`, `/quiz`, `/model` 등은 subcommand 후보 표시
- `update_context_hint()`: 포커스 존에 따라 힌트 갱신. 타이머 중이면 `_context_hint`만 업데이트.
- `_set_status()`: `status_text` + `_context_hint` 동시 설정 (타이머 미취소).

---

### 상태 저장

| 경로 | 내용 |
|------|------|
| `.git-study/state.json` | 선택 SHA 범위 |
| `.git-study/sessions/{session_id}.json` | questions / answers / grades |
| `~/.git-study/settings.json` | 전역 설정 |
| `~/.git-study/secrets.json` | API 키 |

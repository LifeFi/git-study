# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 의존성 설치
UV_CACHE_DIR=/tmp/uv-cache uv sync

# v2 TUI 실행 (현재 작업 대상)
UV_CACHE_DIR=/tmp/uv-cache uv run git-study-v2

# v1 TUI 실행 (레거시, 건드리지 않음)
UV_CACHE_DIR=/tmp/uv-cache uv run git-study
```

환경 변수: `.env` 파일에 `OPENAI_API_KEY` 필요. `load_dotenv()`로 자동 로드됨.

---

## Architecture (v2)

### 디렉토리 구조

```
src/git_study/
├── tui_v2/                  ← 현재 작업 중인 TUI (v2)
│   ├── app.py               ← 메인 앱 GitStudyAppV2
│   ├── commands.py          ← 명령어 파싱 (/quiz, /grade, /commits, /answer, /help)
│   ├── screens/
│   │   └── commit_picker.py ← 커밋 범위 선택 모달 (CommitPickerScreen)
│   └── widgets/
│       ├── command_bar.py   ← 하단 상태바 + 입력창 (CommandBar)
│       └── inline_code_view.py ← 파일 트리 + 코드 뷰 + 퀴즈 블록 (InlineCodeView)
├── tui/                     ← v1 레거시 (공용 유틸 일부 재사용)
│   ├── commit_selection.py  ← CommitSelection 데이터클래스 (v2에서 import)
│   ├── state.py             ← 앱 상태 저장/로드 (v2에서 import)
│   └── code_browser.py      ← highlight_code_lines() (v2에서 import)
├── domain/                  ← 비즈니스 로직 (Git 접근, 컨텍스트 빌드)
│   ├── repo_context.py      ← get_repo(), get_commit_list_snapshot(), build_commit_context()
│   ├── code_context.py      ← get_file_content_at_commit_or_empty(), detect_code_language()
│   └── inline_anchor.py     ← find_anchor_line(), parse_file_context_blocks()
├── services/                ← LangGraph AI 서비스
│   ├── inline_quiz_service.py  ← stream_inline_quiz_progress()
│   └── inline_grade_service.py ← stream_inline_grade_progress()
└── types.py                 ← InlineQuizQuestion, InlineQuizGrade TypedDict
```

---

### 데이터 흐름

```
앱 시작
  → _load_local_repo() [스레드]
  → get_commit_list_snapshot() + load_app_state()
  → _apply_commits(): CommitSelection 복원, code_view.show_range()
  → _try_restore_session(): 이전 퀴즈 세션 복원

/commits
  → _refresh_and_open_picker() [스레드]: 커밋 목록 갱신
  → CommitPickerScreen 모달: Space로 S/E 선택, Enter 확인
  → _on_commit_picker_result(): _oldest_sha/_newest_sha 설정, 저장

/quiz [범위]
  → _start_quiz() [스레드]
  → _resolve_range(): oldest_sha, newest_sha 결정
  → build_commit_context() or build_multi_commit_context()
  → stream_inline_quiz_progress(): LangGraph로 질문 4개 생성
  → code_view.load_inline_quiz(): 앵커 라인에 퀴즈 블록 삽입
  → cmd_bar.set_answer_mode()

답변 (Shift+Enter)
  → on_command_bar_answer_submitted()
  → code_view.update_answer() + _save_session()

/grade
  → _start_grading() [스레드]
  → stream_inline_grade_progress(): LangGraph로 채점
  → code_view.update_grades()
```

---

### 커밋 범위 표현

- `CommitSelection(start_index, end_index)`: 파이크 인덱스 기반 (newest-first 리스트)
- `selected_commit_indices()`: min/max로 정규화 → 순서 무관
- `_oldest_sha` / `_newest_sha`: 실제 SHA (도메인 레이어에 전달)
- session_id: `{oldest_sha[:7]}-{newest_sha[:7]}`

---

### /quiz 범위 인자 규칙

| 입력 | 해석 |
|------|------|
| `/quiz` | 현재 선택된 범위 (_oldest_sha.._newest_sha) |
| `/quiz HEAD` | HEAD 커밋 1개 |
| `/quiz HEAD~3` | HEAD~3..HEAD (4개 커밋) |
| `/quiz A..B` | A와 B 중 자동으로 oldest/newest 판별 (`merge-base --is-ancestor`) |

---

### 상태 저장 위치

| 경로 | 내용 | 저장 시점 |
|------|------|----------|
| `.git-study/state.json` | 선택된 SHA 범위 | /commits 확인 후, /quiz 실행 후 |
| `.git-study/sessions/{session_id}.json` | questions, answers, grades | 답변마다, 채점 후 |

---

### InlineCodeView 주요 구조

- **파일 트리**: 변경 파일 = bold green, 퀴즈 있는 파일 = `● N`(미답변) / `✔ N`(완료) / `★ N`(채점)
- **코드 뷰**: 전체 파일 표시. 추가=초록 배경, 삭제=빨강 배경
- **Overview Ruler**: 오른쪽 2칸 세로 바. 초록(+), 빨강(-), 청록(퀴즈). 클릭으로 스크롤
- **InlineQuizBlock**: 앵커 스니펫으로 위치 결정. 클릭 시 답변 모드 재진입

### CommandBar 동작

- `>` 프롬프트는 삭제 불가 고정 표시
- 모드: `command` / `answer`
- 히스토리: Up/Down으로 이전 명령 탐색
- ESC (answer 모드): 커맨드 모드로 전환 + 재진입 힌트 표시
- `/answer`: 마지막 활성 질문으로 답변 모드 재진입
- 퀴즈 블록 클릭: 모드 무관하게 해당 질문으로 재진입

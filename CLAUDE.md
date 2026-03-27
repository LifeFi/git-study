# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 의존성 설치
UV_CACHE_DIR=/tmp/uv-cache uv sync

# LangGraph 개발 서버 실행
UV_CACHE_DIR=/tmp/uv-cache uv run langgraph dev

# Textual TUI 실행
UV_CACHE_DIR=/tmp/uv-cache uv run git-study
# 또는
UV_CACHE_DIR=/tmp/uv-cache uv run python -m git_study.tui
```

환경 변수: `.env` 파일에 `OPENAI_API_KEY` 필요. `load_dotenv()`로 자동 로드됨.

## Architecture

### 데이터 흐름

```
Git 저장소 (local / GitHub)
  → graph.py: get_repo() → get_latest_commit_context()
  → collect_commit_context 노드: diff, 파일 컨텍스트 추출
  → build_quiz 노드: GPT-4o-mini로 퀴즈 생성 (gpt-4o-mini)
  → TUI 결과 패널 표시 / .git-study/outputs/ 저장
```

### LangGraph 그래프 (`src/git_study/graph.py`)

그래프 이름: `commit_diff_quiz` (langgraph.json에 등록됨)

노드 2개, 순차 실행:
1. **`collect_commit_context`** — GitPython으로 커밋 메타데이터, diff, 변경 파일 전체 코드 수집. `commit_mode`에 따라 동작:
   - `auto`: 최근 커밋 중 텍스트 diff가 있는 것 자동 선택 (최대 `MAX_COMMITS_TO_SCAN=8` 스캔)
   - `latest`: 무조건 최신 커밋
   - `selected`: `requested_commit_shas` 리스트 기반. 복수 커밋이면 `build_multi_commit_context()`로 범위 병합
2. **`build_quiz`** — 수집된 컨텍스트로 LLM 프롬프트 구성 후 퀴즈 생성

`State` TypedDict가 그래프 전체 상태. `messages`는 `add_messages` reducer 사용, 나머지는 직접 덮어씀.

GitHub 저장소는 `.repo_cache/github/` 아래에 bare clone으로 캐시. URL은 `slugify_repo_url()`로 해시 기반 디렉토리명 생성.

### Textual TUI (`src/git_study/tui/`)

- **`app.py`** — 메인 앱 (`GitStudyApp`). 레이아웃, 이벤트 처리, `@work` 비동기 태스크로 그래프 호출
- **`state.py`** — 앱 설정 영속화 (`.git-study/state.json`). 출력 결과는 `.git-study/outputs/`
- **`commit_selection.py`** — `CommitSelection` 데이터클래스. `start_index` / `end_index` 로 커밋 범위 표현. `selected_commit_indices()`가 인덱스 집합 반환
- **`repo_loading.py`** — 저장소 로딩 상태, 원격 폴링 여부 결정 (`should_check_remote`)
- **`result_metadata.py`** — 퀴즈 결과 상단 메타데이터 블록 생성/분리, md/plain 뷰 변환
- **`widgets.py`** — `LabeledMarkdownViewer`, `ResultLoadScreen` 등 커스텀 위젯
- **`code_browser.py`** — 코드 브라우저 도크

폴링: 로컬 저장소 3초, 원격(GitHub) 30초 간격으로 새 커밋 자동 감지.

### 파일 저장 위치

| 경로 | 내용 |
|---|---|
| `.git-study/state.json` | TUI 앱 설정 (저장소, 난이도, 스타일 등) |
| `.git-study/outputs/` | 저장된 퀴즈 결과 파일 |
| `.repo_cache/github/` | GitHub 원격 저장소 캐시 |

### 주요 상수 (`graph.py`)

| 상수 | 값 | 의미 |
|---|---|---|
| `MAX_DIFF_CHARS` | 12,000 | diff 텍스트 최대 길이 |
| `MAX_COMMITS_TO_SCAN` | 8 | auto 모드에서 스캔할 최대 커밋 수 |
| `MAX_FILE_CONTEXT_CHARS` | 12,000 | 파일 컨텍스트 최대 길이 |
| `MAX_FILE_CONTEXT_FILES` | 5 | 컨텍스트에 포함할 최대 파일 수 |

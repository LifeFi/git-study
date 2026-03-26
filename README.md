# git-study

Git 커밋을 읽고 변경 맥락을 바탕으로 학습용 퀴즈를 만드는 `LangGraph` + `Textual` 프로젝트입니다. 로컬 Git 저장소와 GitHub 저장소를 모두 대상으로 삼을 수 있고, 일반 퀴즈와 코드 앵커 기반 인라인 퀴즈를 함께 제공합니다.

## 핵심 기능

- 최근 커밋 또는 사용자가 고른 커밋 범위를 분석해 퀴즈를 생성합니다.
- 텍스트 diff뿐 아니라 변경 파일의 전체 코드 컨텍스트도 함께 읽어 문제를 만듭니다.
- 바이너리 변경이나 hunk 없는 patch는 자동으로 걸러냅니다.
- `auto`, `latest`, `selected` 커밋 모드를 지원합니다.
- Textual TUI에서 커밋 탐색, 범위 선택, 자동 새로고침, 결과 저장/불러오기를 지원합니다.
- 코드 브라우저에서 선택 범위의 파일과 변경 라인을 시각적으로 비교할 수 있습니다.
- 인라인 퀴즈 화면에서 실제 코드 위치에 앵커된 질문을 풀고 바로 채점할 수 있습니다.

## 아키텍처 한눈에 보기

```text
Git 저장소 (local / GitHub)
  -> graph.py: 저장소 로드, 커밋/범위 컨텍스트 수집
  -> LangGraph: collect_commit_context -> build_quiz
  -> TUI app.py: 커밋 목록/상세/옵션/결과 렌더링
  -> inline_quiz.py: 코드 앵커 질문 생성 및 채점
  -> repo 또는 사용자 상태 디렉터리: 결과 저장
```

### 주요 모듈

- `src/git_study/graph.py`
  Git 저장소 접근, 커밋 diff 정제, 변경 파일 컨텍스트 추출, LangGraph 정의, 인라인 퀴즈 생성/채점 로직을 담당합니다.
- `src/git_study/tui/app.py`
  메인 Textual 앱입니다. 저장소 선택, 커밋 목록, 퀴즈 옵션, 결과 패널, 코드 브라우저 및 인라인 퀴즈 진입점을 관리합니다.
- `src/git_study/tui/inline_quiz.py`
  코드 특정 위치에 앵커된 질문을 생성하고, 답변 수집과 채점을 처리합니다.
- `src/git_study/tui/code_browser.py`
  선택한 커밋 범위의 파일 목록과 코드 diff를 터미널 UI로 보여줍니다.
- `src/git_study/tui/state.py`
  앱 상태 저장 경로를 결정하고, 저장된 퀴즈 결과를 관리합니다.

## 요구 사항

- Python `3.13+`
- `uv`
- OpenAI API 키

`.env` 파일이 있으면 자동으로 로드되며, TUI의 `API Key` 버튼으로 전역 설정을 저장해 사용할 수도 있습니다.

```bash
OPENAI_API_KEY=...
```

## 설치 및 실행

의존성 설치:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync
```

LangGraph 개발 서버:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run langgraph dev
```

등록된 그래프 이름은 `commit_diff_quiz` 입니다.

Textual TUI:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run git-study
```

또는:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m git_study.tui
```

## TUI 사용법

### 저장소 선택

- `Local .git`: 현재 작업 디렉터리 기준으로 Git 루트를 탐색합니다.
- `GitHub Repo`: `https://github.com/owner/repo` 형식의 URL을 입력하면 원격 저장소를 캐시해 사용합니다.
- 원격 저장소 캐시는 프로젝트 내부가 아니라 사용자 캐시 디렉터리 아래에 저장됩니다.
- 원격 저장소 캐시는 마지막 사용 시각 기준 `30일` 동안 유지되며, 앱 시작 또는 원격 저장소 접근 시 오래된 캐시를 자동 정리합니다.
- `GitHub Repo` 옵션 옆의 `Caches` 버튼으로 현재 저장된 원격 저장소 캐시 목록과 마지막 사용 시각, 실제 경로를 확인할 수 있습니다.
- 캐시 목록 화면에서 `Remove Selected` 버튼 또는 `d` 키로 선택한 캐시를 수동 제거할 수 있습니다.

OS별 기본 위치:

- macOS: `~/Library/Caches/git-study/github/`
- Linux: `$XDG_CACHE_HOME/git-study/github/` 또는 기본값 `~/.cache/git-study/github/`
- Windows: `%LOCALAPPDATA%/git-study/Cache/github/` 또는 기본값 `~/AppData/Local/git-study/Cache/github/`

### 커밋 탐색

- 기본으로 최근 `10`개 커밋을 불러옵니다.
- 목록 하단의 `Load More Commits (+10)`으로 10개씩 추가 로드할 수 있습니다.
- `Load All Commits`로 전체 커밋을 한 번에 가져올 수 있습니다.
- 로컬 저장소는 3초, GitHub 저장소는 30초 간격으로 새 커밋을 폴링합니다.
- 자동 새로고침으로 들어온 새 커밋은 강조 표시됩니다.

### 커밋 선택 모드

- `Auto Fallback`
  가장 최근 커밋을 우선 사용하되, 텍스트 diff가 없으면 최근 몇 개 커밋 안에서 퀴즈 가능한 커밋을 찾습니다.
- `Latest Only`
  무조건 최신 커밋만 대상으로 삼습니다.
- `Selected Range`
  목록에서 `S`와 `E`를 잡아 하나 이상의 커밋 범위를 선택합니다.

### 퀴즈 옵션

- 난이도: `Easy`, `Medium`, `Hard`
- 스타일:
  `Mixed`, `Study Session`, `Multiple Choice`, `Short Answer`, `Conceptual`
- 추가 요청 입력창에서 "테스트 관점으로 내줘", "설계 의도를 많이 물어봐" 같은 지시를 덧붙일 수 있습니다.
- `API Key` 버튼으로 OpenAI API 키를 설정할 수 있습니다.
- `Session Only`는 현재 실행 동안만 메모리에 보관합니다.
- `Global File`은 `~/.git-study/secrets.json`에 저장합니다.
- 저장 방식과 모델 설정은 `~/.git-study/settings.json`에 저장합니다.

### 결과 패널

- `Gen`: 현재 옵션으로 퀴즈 생성
- `Save`: 결과를 markdown 또는 plain text로 저장
- `Load`: 저장된 결과 다시 불러오기
- `meta`: 결과 상단 메타데이터 표시/숨김
- `md | plain`: 렌더링 모드 전환

저장 위치:

- 로컬 Git 저장소 실행: `<repo-root>/.git-study/`
- GitHub 원격 저장소 또는 일반 실행: `~/.git-study/`
- 앱 상태: `<storage-root>/state.json`
- 전역 설정: `~/.git-study/settings.json`
- 전역 비밀값: `~/.git-study/secrets.json`
- 퀴즈 결과: `<storage-root>/outputs/quiz-output-*`

### Commit Detail 패널

- 선택한 커밋의 SHA, 작성자, 날짜, 변경 파일 요약, diff preview를 보여줍니다.
- `Code` 버튼으로 코드 브라우저를 열 수 있습니다.
- `Inline` 버튼으로 코드 앵커 기반 인라인 퀴즈를 열 수 있습니다.
- 인라인 퀴즈를 풀어두면 버튼 레이블이 `Inline`, `Inline ✎`, `Inline ✓`로 바뀌어 상태를 표시합니다.

### 인라인 퀴즈

- 변경 파일의 실제 코드 3-5줄 스니펫에 질문을 앵커링합니다.
- 질문 유형은 `intent`, `behavior`, `tradeoff`, `vulnerability`를 고르게 사용합니다.
- 좌우 이동으로 문제를 넘기고, 답변 입력 후 `채점하기`로 일괄 채점합니다.
- 현재 풀이 상태와 채점 결과는 같은 커밋 선택 조합 기준으로 메모리 캐시에 유지됩니다.

## 키 조작

- `Tab` / `Shift+Tab`: 섹션 간 포커스 이동
- `Space`: 버튼 실행, 범위 선택, `Load More`, `Load All`
- `g`: 퀴즈 생성
- `r`: 커밋 목록 새로고침
- `q`: 종료
- `Ctrl+C`: 바로 종료되지 않고 짧은 시간 안에 한 번 더 눌러야 종료
- 인라인 퀴즈 화면:
  `Esc` 닫기, `Left/Right` 또는 `h/l`로 이전/다음 질문 이동

## LangGraph 입력 예시

기본 로컬 저장소:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "최근 커밋 기반으로 퀴즈 만들어줘. 난이도는 중간으로 해줘."
    }
  ],
  "repo_source": "local",
  "commit_mode": "auto",
  "difficulty": "medium",
  "quiz_style": "mixed"
}
```

GitHub 저장소 대상:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "이 저장소의 최근 변경으로 퀴즈 만들어줘"
    }
  ],
  "repo_source": "github",
  "github_repo_url": "https://github.com/nomadcoders/ai-agents-masterclass",
  "commit_mode": "auto",
  "difficulty": "medium",
  "quiz_style": "mixed"
}
```

선택 범위 기반 생성:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "선택한 커밋 흐름을 학습 세션으로 정리해줘"
    }
  ],
  "repo_source": "local",
  "commit_mode": "selected",
  "requested_commit_shas": ["abc1234...", "def5678..."],
  "difficulty": "medium",
  "quiz_style": "study_session"
}
```

## 구현 메모

- LLM 기본 모델은 `gpt-4o-mini` 입니다.
- 그래프는 `collect_commit_context`와 `build_quiz` 두 노드로 구성됩니다.
- `selected` 모드에서 여러 커밋을 고르면 범위 diff와 per-commit 문맥을 합쳐 하나의 학습 세션으로 만듭니다.
- 인라인 퀴즈는 일반 퀴즈와 별개로 동작하며, 질문 생성과 채점을 각각 LLM 호출로 처리합니다.
- 변경 파일 전체 컨텍스트는 최대 파일 수와 문자 수 제한 안에서 잘라서 사용합니다.

## 디렉터리

```text
src/git_study/
  graph.py
  tui/
    app.py
    code_browser.py
    commit_selection.py
    inline_quiz.py
    repo_loading.py
    result_metadata.py
    state.py
    widgets.py
langgraph.json
CLAUDE.md
AGENTS.md
```

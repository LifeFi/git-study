# git-study

![git-study inline quiz](https://raw.githubusercontent.com/LifeFi/git-study/main/assets/screenshot-20260328.png)

Git 커밋을 읽고 **코드 이해 퀴즈**로 바꿔주는 터미널 학습 도구.  
왜 이렇게 바뀌었는지, 어떤 트레이드오프인지 — diff만 봐서는 설명하기 어려운 것들을 물어봅니다.

## 특징

- 커밋 범위를 지정하면 실제 코드 위치에 앵커된 인라인 퀴즈 자동 생성
- `intent` · `behavior` · `tradeoff` · `vulnerability` 유형을 고르게 출제
- 답변 → 채점 → 피드백까지 한 화면에서 완결
- 로컬 저장소 + GitHub URL 모두 지원
- Textual TUI (v2) + Streamlit 웹 앱

## 설치

**요구 사항**: Python 3.13+, uv, OpenAI API 키

```bash
# TestPyPI에서 설치
UV_CACHE_DIR=/tmp/uv-cache uv tool install --refresh \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  git-study==0.1.9

# 로컬 개발
UV_CACHE_DIR=/tmp/uv-cache uv sync
UV_CACHE_DIR=/tmp/uv-cache uv run git-study-v2
```

## 빠른 시작

```
git-study-v2          # TUI v2
git-study-streamlit   # Streamlit 웹 앱
```

1. 저장소 경로 또는 GitHub URL 입력
2. `/commits` → Space로 범위 선택 → Enter 확인
3. `/quiz` → 퀴즈 생성
4. 블록 클릭 또는 `/answer` → 답변 입력 → Enter 제출
5. `/grade` → 채점

## 명령어

| 명령어 | 설명 |
|--------|------|
| `/commits` | 커밋 범위 선택 |
| `/quiz [범위]` | 퀴즈 생성 (`HEAD`, `HEAD~3`, `A..B`) |
| `/grade` | 채점 |
| `/answer` | 마지막 질문으로 답변 재진입 |
| `/review [범위]` | 커밋 코드리뷰 |
| `/map` | 저장소 구조 요약 |
| `/model <id>` | 모델 변경 |
| `/hook on\|off` | git post-commit 훅 설치/해제 |
| `/repo <path\|url>` | 저장소 전환 |
| `/apikey` | API 키 설정 |
| `?` | 도움말 |

## 키 조작

### 커맨드바

| 키 | 동작 |
|----|------|
| `Enter` | 명령 실행 |
| `↑↓` | 명령 히스토리 |
| `Tab` | 자동완성 |

### 퀴즈 답변 중

| 키 | 동작 |
|----|------|
| `Enter` | 답변 제출 |
| `Shift+Enter` | 줄바꿈 |
| `Shift+↑↓` | 이전/다음 질문 |
| `ESC` | 답변 모드 종료 |

### 커밋 선택창

| 키 | 동작 |
|----|------|
| `Space` | S/E 선택 토글 |
| `Shift+ESC` | 선택 전체 초기화 |
| `Home / End` | 맨 위 / 맨 아래 |
| `Enter` | 확인 |
| `ESC` | 취소 |

## API Key 설정

환경변수 또는 앱 내 `/apikey` 명령으로 설정합니다.

```bash
OPENAI_API_KEY=sk-...   # .env 또는 셸 환경
```

- **Session Only**: 현재 실행 중 메모리에만 보관
- **Global File**: `~/.git-study/secrets.json`에 영구 저장

## 저장 위치

| 경로 | 내용 |
|------|------|
| `<repo>/.git-study/state.json` | 선택된 커밋 범위 |
| `<repo>/.git-study/sessions/` | 퀴즈 세션 (질문·답변·채점) |
| `~/.git-study/settings.json` | 전역 설정 |
| `~/.git-study/secrets.json` | API 키 |

GitHub 원격 저장소나 저장소 외부 실행 시 `~/.git-study/`에 저장됩니다.

## GitHub 저장소 지원

`https://github.com/owner/repo` URL을 그대로 입력하면 자동 클론 후 분석합니다.  
클론 캐시: macOS `~/Library/Caches/git-study/github/` · Linux `~/.cache/git-study/github/`

## 테스트

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests
```

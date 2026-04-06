[English](README.md) | 한국어

# git-study

![git-study 데모](assets/demo_hq.gif)

> Git 커밋을 읽고, **코드 이해 퀴즈**로 만들어주는 터미널 학습 도구.

왜 이렇게 바뀌었는지, 어떤 트레이드오프인지 — diff만 봐서는 설명하기 어려운 것들을 물어봅니다.

---

## 특징

- **인라인 퀴즈** — 실제 코드 위치에 앵커된 질문 자동 생성
- **4가지 유형** — `intent` · `behavior` · `tradeoff` · `vulnerability`를 고르게 출제
- **한 화면 완결** — 답변 → 채점 → 피드백까지
- **어디서든** — 로컬 저장소 + GitHub URL 모두 지원
- **두 가지 UI** — Textual TUI (v2) + Streamlit 웹 앱

---

## 빠른 시작

**요구 사항**: Python 3.13+, [uv](https://docs.astral.sh/uv/), OpenAI API 키

**Step 1. uv 설치 (처음 한 번만)**

macOS / Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Step 2. git-study 설치**

```bash
uv tool install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  git-study
```

**Step 3. API 키 설정 (처음 한 번만)**

```bash
cd /path/to/your/repo   # .git 폴더가 있는 프로젝트 루트로 이동
git-study               # 실행
/apikey set sk-...      # 앱 안에서 입력 (영구 저장)
```

**Step 4. 학습 시작**

```
/commits      # 커밋 범위 선택
/quiz         # 퀴즈 생성
/grade        # 채점
```

`Shift+Tab`으로 대화창 ↔ 코드뷰 전환

> **Tip.** `/hook on`을 한 번 실행해두면 git post-commit 훅이 설치됩니다.  
> 이후 커밋할 때마다 git-study가 자동으로 실행되어 해당 커밋의 퀴즈를 바로 생성합니다.

---

## 명령어

| 명령어 | 설명 |
|--------|------|
| `/commits` | 커밋 범위 선택 |
| `/quiz [범위] [개수] [--ai\|--others]` | 퀴즈 생성 |
| `/quiz list` | 세션별 퀴즈 목록 |
| `/quiz clear` | 현재 범위 퀴즈 삭제 |
| `/quiz retry` | 답변만 초기화 후 다시 풀기 |
| `/grade` | 채점 |
| `/answer` | 마지막 질문으로 답변 재진입 |
| `/review [범위]` | 커밋 해설 |
| `/map [--full] [--refresh]` | 저장소 구조 맵 |
| `/clear` | 대화 초기화 |
| `/resume` | 이전 대화 불러오기 |
| `/repo [경로\|URL]` | 저장소 전환 |
| `/apikey` | API 키 관리 |
| `/model <id>` | 모델 변경 |
| `/hook on\|off` | git post-commit 훅 설치/해제 |
| `/exit` | 종료 |
| `?` | 도움말 |

---

## 저장 위치

| 경로 | 내용 |
|------|------|
| `<repo>/.git-study/state.json` | 선택된 커밋 범위 |
| `<repo>/.git-study/sessions/` | 퀴즈 세션 (질문·답변·채점) |
| `~/.git-study/settings.json` | 전역 설정 |
| `~/.git-study/secrets.json` | API 키 |

GitHub URL 또는 저장소 외부 실행 시 `~/.git-study/`에 저장됩니다.

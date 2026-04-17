# PRD: TypeScript 완전 전환

> 작성일: 2026-04-16  
> 상태: 계획 확정, 미착수  
> 대상 브랜치: main

---

## 배경 및 목적

현재 앱은 Python/Textual 기반 TUI + LangGraph AI 백엔드로 구성된 약 12,000줄 규모의 코드베이스다. Python과 TypeScript를 혼재할 경우 장기적으로 런타임·툴체인·의존성 관리 복잡도가 누적된다. 이를 해소하기 위해 TypeScript 단일 런타임으로 완전 전환한다.

---

## 범위

### In-scope

- Python LangGraph 그래프 → LangGraph.js 포팅
- Python TUI(Textual) → Ink(React 기반 CLI) 포팅
- Git 도메인 레이어 → simple-git 기반 TypeScript 재구현
- Pydantic 스키마 → Zod 스키마 전환
- SqliteSaver 체크포인트 → `@langchain/langgraph-checkpoint-sqlite` 전환
- Shiki 기반 코드 하이라이팅 (Rich/Pygments 대체)

### Out-of-scope

- 기능 추가 및 기존 동작 변경
- Python 버전 유지 여부 결정 (Phase 5 완료 후 선택)

---

## 기술 스택

| Python (현재) | TypeScript (목표) |
|---|---|
| LangGraph + SqliteSaver | `@langchain/langgraph` + `@langchain/langgraph-checkpoint-sqlite` |
| LangChain OpenAI | `@langchain/openai` |
| GitPython | `simple-git` |
| aiosqlite | `better-sqlite3` |
| Pydantic | `zod` |
| Rich/Pygments | `shiki` (VSCode 동일 엔진, ANSI 출력) |
| Textual | `ink` (React 기반 CLI) |

### 주요 패키지 버전

```json
{
  "ink": "^5.0.0",
  "react": "^18.3.0",
  "@langchain/langgraph": "^0.x",
  "@langchain/langgraph-checkpoint-sqlite": "^0.x",
  "@langchain/openai": "^0.x",
  "shiki": "^4.0.0",
  "chalk": "^5.3.0",
  "simple-git": "^3.x",
  "better-sqlite3": "^9.x",
  "zod": "^3.x",
  "marked-terminal": "^7.0.0",
  "@inkjs/ui": "^2.0.0"
}
```

---

## 디렉터리 구조

### 작업 중 구조

```
git-study/
├── py/                     ← 기존 src/ → py/로 이동 (Python 코드)
│   ├── pyproject.toml
│   ├── uv.lock
│   └── git_study/
├── ts/                     ← 신규 TypeScript 프로젝트
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── main.tsx
│       ├── graphs/         ← LangGraph.js 워크플로우
│       ├── services/       ← AI 서비스 레이어
│       ├── domain/         ← Git 도메인 (simple-git)
│       ├── llm/            ← LLM 클라이언트
│       └── tui/            ← Ink 컴포넌트
└── docs/migration/MIGRATION_PLAN.md
```

### 전환 완료 후 구조 (옵션 A: py/ 유지)

```
git-study/
├── py/                 ← Python 버전 (레거시/참고용)
├── package.json
├── tsconfig.json
└── src/
    ├── main.tsx
    ├── graphs/
    ├── services/
    ├── domain/
    ├── llm/
    └── tui/
```

### 전환 완료 후 구조 (옵션 B: py/ 제거)

```
git-study/
├── package.json
├── tsconfig.json
└── src/
    ├── main.tsx
    ├── graphs/
    ├── services/
    ├── domain/
    ├── llm/
    └── tui/
```

---

## 핵심 구현 명세

### 1. LLM 클라이언트 — 4-step fallback chain

Python `src/git_study/llm/client.py:135` 동작을 아래와 같이 재구현한다.

```typescript
async invokeStructured<T>(prompt: string, schema: ZodSchema<T>): Promise<T> {
  // 1. json_schema strict
  try { return await llm.withStructuredOutput(schema, { strict: true }).invoke(prompt) }
  catch { /* retry */ }
  // 2. strict=false
  try { return await llm.withStructuredOutput(schema, { strict: false }).invoke(prompt) }
  catch { /* retry */ }
  // 3. json_mode
  try { return await llm.withStructuredOutput(schema, { method: 'jsonMode' }).invoke(prompt) }
  catch { /* retry */ }
  // 4. raw JSON parse
  const raw = await llm.invoke(prompt)
  return parseJsonFromText(raw.content as string, schema)
}
```

### 2. SqliteSaver 체크포인트

Python `src/git_study/services/chat_service.py:60,91` 동작을 아래와 같이 대체한다.

```typescript
import { SqliteSaver } from '@langchain/langgraph-checkpoint-sqlite'
const checkpointer = SqliteSaver.fromConnString(dbPath)
const app = graph.compile({ checkpointer })
```

### 3. InlineCodeView — Shiki 기반 렌더링

Python `src/git_study/tui_v2/widgets/inline_code_view.py` 동작을 아래와 같이 대체한다.

```typescript
import { codeToANSI } from '@shikijs/cli'
const highlighted = await codeToANSI(code, { lang, theme: 'vitesse-dark' })
const visibleLines = lines.slice(scrollY, scrollY + viewportHeight)
```

### 4. 앱 상태 — useReducer + Context

```typescript
type AppState = {
  mode: 'idle' | 'quiz_loading' | 'quiz_answering' | 'grading' | 'chatting'
  questions: InlineQuizQuestion[]
  answers: Record<string, string>
  grades: InlineQuizGrade[]
  oldestSha: string
  newestSha: string
}
```

---

## 단계별 작업 계획

### Phase 1: TS 셋업 + LLM 클라이언트 검증 (1주)

> 가장 중요한 리스크(LLM 연동)를 먼저 검증한다.

- [ ] `ts/` 디렉터리 + `package.json` 초기화
- [ ] `ts/src/llm/client.ts` — 4-step fallback chain 구현 및 테스트
- [ ] `ts/src/graphs/chat_graph.ts` — SqliteSaver 동작 검증

**완료 기준:** Python `stream_chat()`과 동일 동작 확인

---

### Phase 2: LangGraph 그래프 포팅 (2주)

포팅 순서: `inline_quiz_graph` → `quiz_graph` → `read_graph` → `inline_grade_graph` → `general_grade_graph`

- [ ] 각 그래프: `StateGraph` + 노드 함수 포팅
- [ ] Zod 스키마로 Pydantic 모델 대체
- [ ] 서비스 레이어 (`stream_*_progress`) 포팅

---

### Phase 3: Git 도메인 레이어 (1주)

- [ ] `domain/repo_context.ts` — simple-git으로 GitPython 대체
- [ ] `domain/code_context.ts` — diff 추출, 파일 컨텍스트 빌드
- [ ] `domain/repo_cache.ts` — 원격 레포 캐시
- [ ] `domain/inline_anchor.ts` — 앵커 파싱

---

### Phase 4: Ink TUI 구현 (3주)

| 주차 | 작업 |
|---|---|
| 4-1 | `AppStatusBar` + `HistoryView` + `CommandBar` |
| 4-2 | `InlineCodeView` (CodePane + Shiki + OverviewRuler) |
| 4-3 | `InlineQuizBlock` + 모달 3개 + `App.tsx` 통합 |

---

### Phase 5: 통합 + 검증 + Python 제거 (1주)

- [ ] E2E: `/quiz HEAD~2` → 답변 → `/grade` 전체 플로우
- [ ] Python TUI와 동작 비교 검증
- [ ] `src/git_study/` 제거
- [ ] `ts/` → 루트로 이동
- [ ] `pyproject.toml`, `uv.lock` 제거

**총 예상 기간: 8주**

---

## 위험 요소 및 대응

| 위험 | 대응 |
|---|---|
| LangGraph.js `SqliteSaver` API 차이 | Phase 1에서 즉시 검증. 문제 시 `better-sqlite3` 직접 구현 |
| 4-step fallback 재현 불완전 | Python과 동일 테스트 케이스로 검증 |
| `simple-git` API 불일치 | `repo_context.py` 옆에 두고 1:1 비교 포팅 |
| Shiki ANSI 렌더링 차이 | Textual 버전과 스크린샷 비교 |
| 터미널 리사이즈 처리 | `useStdout` + `process.stdout.on('resize')` |

---

## 참고 파일

- `docs/migration/MIGRATION_PLAN.md` — 원본 마이그레이션 계획
- `src/git_study/llm/client.py:135` — 4-step fallback 원본
- `src/git_study/services/chat_service.py:60,91` — SqliteSaver 원본
- `src/git_study/tui_v2/widgets/inline_code_view.py` — 코드뷰 원본

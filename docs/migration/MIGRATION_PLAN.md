# TypeScript 완전 전환 계획

> 작성일: 2026-04-03  
> 상태: 계획 확정, 미착수

## 배경

현재 앱은 Python/Textual 기반 TUI + LangGraph AI 백엔드로 구성됨 (~12,000줄).  
Python+TS 혼재의 장기 복잡도를 피하기 위해 TypeScript 단일 런타임으로 완전 전환.

CCG(Codex+Gemini) 검토 결과 핵심 확인사항:
- LangGraph.js에 `@langchain/langgraph-checkpoint-sqlite` 공식 SqliteSaver 존재
- `withStructuredOutput`도 JS 공식 지원 (`jsonSchema/functionCalling/jsonMode`)
- 4-step structured output fallback chain은 앱 레벨 로직 — 직접 재구현 필요하지만 범위 제한적

---

## 작업 중 디렉터리 구조

> 먼저 `src/git_study/` → `py/git_study/`로 이동해 Python을 `py/`로 정리한 뒤 `ts/` 작업 시작.

```
git-study/
├── py/                     ← Python (기존 src/ → py/로 이동)
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
└── MIGRATION_PLAN.md
```

## 전환 완료 후 최종 구조

`py/` 유지 여부는 선택사항. 두 가지 옵션:

**옵션 A: py/ 유지** (레거시 참고, Python 폴백 가능)
```
git-study/
├── py/                 ← Python 버전 유지 (레거시/참고용)
├── package.json        ← ts/ 내용을 루트로 이동
├── tsconfig.json
└── src/
    ├── main.tsx
    ├── graphs/
    ├── services/
    ├── domain/
    ├── llm/
    └── tui/
```

**옵션 B: py/ 제거** (완전한 TS 단일 레포)
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

> py/ 유지 시 주의: `uv run` 실행은 `cd py && uv run git-study-v2` 방식으로 변경됨.

---

## 기술 스택

| Python | TypeScript 대체 |
|---|---|
| LangGraph + SqliteSaver | `@langchain/langgraph` + `@langchain/langgraph-checkpoint-sqlite` |
| LangChain OpenAI | `@langchain/openai` |
| GitPython | `simple-git` |
| aiosqlite | `better-sqlite3` |
| Pydantic | `zod` |
| Rich/Pygments | `shiki` (VSCode 동일 엔진, ANSI 출력) |
| Textual | `ink` (React 기반 CLI) |

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

## 핵심 구현 전략

### LLM 클라이언트 — 4-step fallback chain

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

참조: `src/git_study/llm/client.py` L135

### SqliteSaver 체크포인트

```typescript
import { SqliteSaver } from '@langchain/langgraph-checkpoint-sqlite'
const checkpointer = SqliteSaver.fromConnString(dbPath)
const app = graph.compile({ checkpointer })
```

참조: `src/git_study/services/chat_service.py` L60, L91

### InlineCodeView — Shiki 기반 렌더링

```typescript
import { codeToANSI } from '@shikijs/cli'
const highlighted = await codeToANSI(code, { lang, theme: 'vitesse-dark' })
// 가상 렌더링
const visibleLines = lines.slice(scrollY, scrollY + viewportHeight)
```

참조: `src/git_study/tui_v2/widgets/inline_code_view.py`

### 상태 관리: useReducer + Context

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

## 단계별 작업

### Phase 1: TS 셋업 + LLM 클라이언트 검증 (1주)

> 가장 중요한 리스크 먼저 검증

- [ ] `ts/` 디렉터리 + `package.json` 초기화
- [ ] `ts/src/llm/client.ts` — 4-step fallback chain 구현 및 테스트
- [ ] `ts/src/graphs/chat_graph.ts` — SqliteSaver 동작 검증
- [ ] 완료 기준: Python `stream_chat()`과 동일 동작 확인

### Phase 2: LangGraph 그래프 포팅 (2주)

포팅 순서: `inline_quiz_graph` → `quiz_graph` → `read_graph` → `inline_grade_graph` → `general_grade_graph`

- [ ] 각 그래프: StateGraph + 노드 함수 포팅
- [ ] Zod 스키마로 Pydantic 모델 대체
- [ ] 서비스 레이어 (stream_*_progress) 포팅

### Phase 3: Git 도메인 레이어 (1주)

- [ ] `domain/repo_context.ts` — simple-git으로 GitPython 대체
- [ ] `domain/code_context.ts` — diff 추출, 파일 컨텍스트 빌드
- [ ] `domain/repo_cache.ts` — 원격 레포 캐시
- [ ] `domain/inline_anchor.ts` — 앵커 파싱

### Phase 4: Ink TUI 구현 (3주)

**4-1 (1주)**: AppStatusBar + HistoryView + CommandBar  
**4-2 (1주)**: InlineCodeView (CodePane + Shiki + OverviewRuler)  
**4-3 (1주)**: InlineQuizBlock + 모달 스크린 3개 + App.tsx 통합

### Phase 5: 통합 + 검증 + Python 제거 (1주)

- [ ] E2E: `/quiz HEAD~2` → 답변 → `/grade` 전체 플로우
- [ ] Python TUI와 동작 비교
- [ ] `src/git_study/` 제거
- [ ] `ts/` → 루트로 이동
- [ ] `pyproject.toml`, `uv.lock` 제거

**총 예상 기간: 8주**

---

## 위험 요소

| 위험 | 대응 |
|---|---|
| LangGraph.js SqliteSaver API 차이 | Phase 1에서 즉시 검증, 문제 시 better-sqlite3 직접 구현 |
| 4-step fallback 재현 불완전 | Python과 동일 테스트 케이스로 검증 |
| simple-git API 불일치 | `repo_context.py` 옆에 두고 1:1 비교 포팅 |
| Shiki ANSI 렌더링 차이 | Textual 버전과 스크린샷 비교 |
| 터미널 리사이즈 | `useStdout` + `process.stdout.on('resize')` |

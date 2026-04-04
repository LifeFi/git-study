def build_read_generation_prompt(
    *,
    user_request: str,
    difficulty: str,
    selected_context_note: str,
    commit_sha: str,
    commit_subject: str,
    commit_author: str,
    commit_date: str,
    changed_files_summary: str,
    diff_text: str,
    file_context_text: str,
    analysis_json: str,
) -> str:
    return f"""
You are a senior engineer creating a pre-quiz reading guide from Git changes.

User request:
{user_request}

{selected_context_note}

Commit: {commit_sha[:12]} — {commit_subject} ({commit_author}, {commit_date}) | Difficulty: {difficulty}

Sanitized textual diff (primary source — base all code examples on this):
{diff_text}

Changed file full content (supplementary — use only when diff alone lacks context for a section):
{file_context_text or "(not available)"}

Changed files overview (supplementary):
{changed_files_summary}

Pre-analysis (follow strictly):
{analysis_json}

Instructions:
1. Respond in Korean unless the user explicitly requested another language.
2. Base everything only on the diff, changed-file context, and pre-analysis above.
3. Reading guide only — do NOT reveal answers or explain quiz solutions.
4. Each bullet under 30 words. All code blocks must be actual snippets from the diff, never invented.
5. Use markdown headings and fenced code blocks.

Output (in order):
- `## 이번 변경을 먼저 읽는 법` — 2-3 sentence intro
- `## 변경 개요` — 3-5 bullets
- `## 핵심 파일` — 2-5 files with one-line reason each
- `## 먼저 볼 코드` — 2-4 fenced code blocks from the diff
- `## 이번에 이해해야 할 것` — 3-5 bullets
- `## 퀴즈 전에 스스로 점검할 질문` — exactly 4 question bullets, no answers
- `## 주의할 점` — 2-4 bullets on risks or trade-offs
""".strip()

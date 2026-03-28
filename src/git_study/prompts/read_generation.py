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

Commit metadata:
- SHA: {commit_sha}
- Subject: {commit_subject}
- Author: {commit_author}
- Date: {commit_date}

Difficulty: {difficulty}

Changed files summary:
{changed_files_summary}

Sanitized textual diff:
{diff_text}

Changed file full content context:
{file_context_text or "No readable changed file content was extracted."}

Pre-analysis to follow strictly:
{analysis_json}

Instructions:
1. Respond in Korean unless the user explicitly requested another language.
2. Base everything only on the commit metadata, diff, changed-file full content context, and pre-analysis above.
3. This is reading material before the quiz, not the quiz itself.
4. Keep the tone explanatory and concise.
5. Do not invent code or intent not grounded in the provided context.
6. Use markdown headings and fenced code blocks so the result renders cleanly in a markdown viewer.

Output requirements:
- Start with `## 이번 변경을 먼저 읽는 법`.
- Then add `## 변경 개요` with 3-5 bullets.
- Then add `## 핵심 파일` with 2-5 files and why each matters.
- Then add `## 먼저 볼 코드` with 2-4 short fenced code blocks.
- Then add `## 이번에 이해해야 할 것` with 3-5 bullets.
- Then add `## 퀴즈 전에 스스로 점검할 질문` with exactly 4 short bullets.
- End with `## 주의할 점` and 2-4 bullets about risks, regressions, or trade-offs.
""".strip()

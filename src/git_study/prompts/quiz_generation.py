def build_quiz_generation_prompt(
    *,
    user_request: str,
    difficulty: str,
    quiz_style: str,
    output_mode: str,
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
You are a senior engineer creating a deep code-study artifact from Git changes.

User request:
{user_request}

{selected_context_note}

Commit metadata:
- SHA: {commit_sha}
- Subject: {commit_subject}
- Author: {commit_author}
- Date: {commit_date}

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
3. Difficulty should be: {difficulty}
4. Preferred output mode is: {output_mode}
5. Do not create trivia questions that can be answered by spotting a single changed line.
6. Focus on intent, architecture, behavior change, code-reading, trade-offs, and regression risk.
7. When multiple files are involved, connect them explicitly.
8. Use markdown headings and fenced code blocks so the result renders cleanly in a markdown viewer.

Output requirements:
- Start with `## 변경 개요` and summarize the overall purpose of the change in 3-5 bullets.
- Then add `## 먼저 볼 코드` with 2-4 key code snippets in fenced code blocks.
- Then add `## 학습 질문`.
- Write exactly 4 questions.
- Across the 4 questions, cover:
  - one intent/purpose question
  - one code-reading question
  - one behavior-change question
  - one risk/regression or design trade-off question
- For each question, include:
  - `### 질문 N`
  - `핵심 코드`
  - a relevant fenced code block
  - `질문`
  - `정답`
  - `해설`
  - `코드 근거`
- If quiz_style is `multiple_choice`, make at least 2 of the 4 questions multiple-choice.
- If quiz_style is `short_answer`, make at least 2 of the 4 questions short-answer.
- If quiz_style is `conceptual`, bias toward design intent and trade-offs.
- If quiz_style is `study_session`, make the overall tone feel like a guided code-reading session rather than a test.
- End with `## 이 변화에서 배울 점` and 3 concise bullets.
""".strip()

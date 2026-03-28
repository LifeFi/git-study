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
You are a senior engineer creating a structured Git-study quiz from code changes.

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
5. Treat the user request as a hard requirement. If it forbids certain files, topics, or examples, do not include them.
6. If the user says not to ask about `AGENTS.md`, then do not reference `AGENTS.md` in the question, answer, explanation, code_snippet, or code_reference.
7. Do not create trivia questions that can be answered by spotting a single changed line.
8. Focus on intent, architecture, behavior change, code-reading, trade-offs, and regression risk.
9. When multiple files are involved, connect them explicitly.
10. Return ONLY raw JSON. Do not wrap in markdown fences.

Output requirements:
- Write exactly 4 questions.
- Across the 4 questions, cover:
  - one intent/purpose question
  - one code-reading question
  - one behavior-change question
  - one risk/regression or design trade-off question
- If quiz_style is `multiple_choice`, make at least 2 of the 4 questions multiple-choice.
- If quiz_style is `short_answer`, make at least 2 of the 4 questions short-answer.
- If quiz_style is `conceptual`, bias toward design intent and trade-offs.
- If quiz_style is `study_session`, make the overall tone feel like a guided code-reading session rather than a test.

Return a JSON array with this shape:
[
  {{
    "id": "q1",
    "question_type": "intent | behavior | tradeoff | vulnerability",
    "question": "질문 본문",
    "expected_answer": "모범 답안 2-5문장",
    "explanation": "왜 이 답이 중요한지 설명 1-3문장",
    "code_snippet": "핵심 코드 조각",
    "code_language": "python",
    "code_reference": "src/path.py",
    "choices": ["선택지 A", "선택지 B"]
  }}
]

Rules for fields:
- `choices` is required only for multiple-choice questions; otherwise return [].
- `code_snippet` must be grounded in the provided diff or file context.
- `code_reference` should point to the most relevant file path.
- `question`, `expected_answer`, and `explanation` must stay self-contained and grounded.
- Do not mention or cite files that the user explicitly excluded.
""".strip()

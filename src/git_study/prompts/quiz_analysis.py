def build_quiz_analysis_prompt(
    *,
    user_request: str,
    difficulty: str,
    quiz_style: str,
    selected_context_note: str,
    commit_sha: str,
    commit_subject: str,
    commit_author: str,
    commit_date: str,
    changed_files_summary: str,
    diff_text: str,
    file_context_text: str,
) -> str:
    return f"""
You are a senior engineer analyzing Git changes for a code-study session.

User request:
{user_request}

{selected_context_note}

Commit metadata:
- SHA: {commit_sha}
- Subject: {commit_subject}
- Author: {commit_author}
- Date: {commit_date}

Difficulty: {difficulty}
Quiz style: {quiz_style}

Changed files summary:
{changed_files_summary}

Sanitized textual diff:
{diff_text}

Changed file full content context:
{file_context_text or "No readable changed file content was extracted."}

Return ONLY raw JSON:
{{
  "summary_bullets": ["3-5 bullets in Korean"],
  "key_files": ["2-5 file paths"],
  "key_snippets": [
    {{
      "path": "file path",
      "code": "short relevant code snippet copied or summarized from the provided context",
      "reason": "why this snippet matters in Korean"
    }}
  ],
  "learning_objectives": [
    "what the learner should understand"
  ],
  "question_plan": [
    {{
      "type": "intent | code_reading | behavior_change | risk_tradeoff",
      "focus": "question focus in Korean",
      "path": "most relevant file path",
      "code_hint": "brief code cue from the provided context"
    }}
  ],
  "change_risks": ["possible regression or trade-off in Korean"]
}}

Rules:
- Respond in Korean.
- Base everything only on the provided commit metadata, diff, and file context.
- The 4 question_plan items must cover intent, code_reading, behavior_change, risk_tradeoff exactly once each.
- Do not invent code that is not supported by the provided context.
""".strip()

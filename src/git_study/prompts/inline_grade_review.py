def build_inline_grade_review_prompt(
    *,
    grades_json: str,
    question_ids_json: str,
) -> str:
    return f"""
You are validating inline quiz grading output.

Expected question ids:
{question_ids_json}

Generated grades:
{grades_json}

Return ONLY raw JSON:
{{
  "is_valid": true,
  "issues": ["한국어로 된 문제점 목록"],
  "revision_instruction": "수정 지시를 한국어로 짧게",
  "normalized_grades": [
    {{"id": "q1", "score": 80, "feedback": "피드백"}}
  ]
}}

Validation rules:
- Every expected question id should appear exactly once.
- score must be an integer between 0 and 100.
- feedback must be non-empty Korean text with concrete comparison to the model answer.
- If valid, copy the cleaned array into normalized_grades and keep revision_instruction empty.
- If invalid, set is_valid to false and explain what to fix.
""".strip()

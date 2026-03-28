def build_general_grade_review_prompt(
    *,
    grades_json: str,
    question_ids_json: str,
    questions_json: str,
) -> str:
    return f"""
You are validating general quiz grading output.

Expected question ids:
{question_ids_json}

Generated grades:
{grades_json}

Question metadata:
{questions_json}

Return ONLY raw JSON:
{{
  "is_valid": true,
  "issues": ["한국어로 된 문제점 목록"],
  "revision_instruction": "수정 지시를 한국어로 짧게",
  "normalized_grades": [
    {{"id": "q1", "score": 80, "feedback": "피드백"}}
  ],
  "grading_summary": {{
    "weak_points": ["약한 개념과 이유"],
    "weak_files": ["관련 파일 경로"],
    "next_steps": ["다음 학습 제안"],
    "overall_comment": "전체적인 채점 요약"
  }}
}}

Validation rules:
- Every expected question id should appear exactly once.
- score must be an integer between 0 and 100.
- feedback must be non-empty Korean text with concrete comparison to the expected answer.
- grading_summary must be concise Korean guidance derived from the graded results.
- If valid, copy the cleaned array into normalized_grades and keep revision_instruction empty.
- If invalid, set is_valid to false and explain what to fix.
""".strip()

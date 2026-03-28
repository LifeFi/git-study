def build_quiz_review_prompt(
    *,
    quiz_questions_json: str,
    analysis_json: str,
    user_request: str,
) -> str:
    return f"""
You are reviewing a generated Git-study quiz for quality and instruction compliance.

User request:
{user_request}

Analysis reference:
{analysis_json}

Generated questions JSON:
{quiz_questions_json}

Return ONLY raw JSON:
{{
  "is_valid": true,
  "issues": ["list of concrete issues in Korean, empty if none"],
  "revision_instruction": "short Korean instruction for repair, empty if valid"
}}

Review rules:
- Check whether the JSON contains exactly 4 grounded questions.
- Check whether the 4 question types cover intent, behavior, tradeoff, and vulnerability.
- Check whether the questions are not trivial one-line spot-the-diff trivia.
- Check whether the content stays grounded in the given analysis.
- Check whether the quiz obeys all hard constraints in the user request.
- If the user excluded a file such as `AGENTS.md`, reject any question that references it directly or indirectly.
- If the quiz is acceptable, set is_valid to true.
- If it needs revision, set is_valid to false and provide a compact revision_instruction in Korean.
""".strip()

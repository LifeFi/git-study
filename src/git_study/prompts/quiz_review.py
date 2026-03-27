def build_quiz_review_prompt(*, quiz_markdown: str, analysis_json: str) -> str:
    return f"""
You are reviewing a generated Git-study quiz for quality and instruction compliance.

Analysis reference:
{analysis_json}

Generated markdown:
{quiz_markdown}

Return ONLY raw JSON:
{{
  "is_valid": true,
  "issues": ["list of concrete issues in Korean, empty if none"],
  "revision_instruction": "short Korean instruction for repair, empty if valid"
}}

Review rules:
- Check whether the markdown has the required sections and exactly 4 questions.
- Check whether the questions are not trivial one-line spot-the-diff trivia.
- Check whether the content stays grounded in the given analysis.
- If the quiz is acceptable, set is_valid to true.
- If it needs revision, set is_valid to false and provide a compact revision_instruction in Korean.
""".strip()

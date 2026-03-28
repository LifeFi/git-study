def build_read_review_prompt(*, reading_markdown: str, analysis_json: str) -> str:
    return f"""
You are reviewing a generated pre-quiz reading guide for quality and instruction compliance.

Analysis reference:
{analysis_json}

Generated markdown:
{reading_markdown}

Return ONLY raw JSON:
{{
  "is_valid": true,
  "issues": ["list of concrete issues in Korean, empty if none"],
  "revision_instruction": "short Korean instruction for repair, empty if valid"
}}

Review rules:
- Check whether the markdown has the required sections.
- Check whether it reads like a learning guide before a quiz, not like a quiz or answer sheet.
- Check whether the content stays grounded in the given analysis.
- If the reading guide is acceptable, set is_valid to true.
- If it needs revision, set is_valid to false and provide a compact revision_instruction in Korean.
""".strip()

def build_inline_question_review_prompt(
    *,
    inline_questions_json: str,
    validated_anchors_json: str,
    user_request: str,
    count: int = 4,
) -> str:
    return f"""
You are reviewing generated inline quiz questions for quality and instruction compliance.

User request:
{user_request}

Validated anchors:
{validated_anchors_json}

Generated inline questions JSON:
{inline_questions_json}

Return ONLY raw JSON:
{{
  "is_valid": true,
  "issues": ["list of concrete issues in Korean, empty if none"],
  "revision_instruction": "short Korean instruction for repair, empty if valid"
}}

Review rules:
- Check whether the JSON contains exactly {count} grounded inline questions unless fewer validated anchors were available.
- Check whether each question reuses a validated file_path and anchor_line.
- Check whether the questions are reasoning-heavy and not trivial line-matching prompts.
- Questions that can be answered without reading the diff (e.g., trivially named variables, obvious import statements,
  or lines whose purpose is self-evident from their name) must be replaced.
  If more than 1 such trivial question exists, set is_valid=false.
- Check whether the content obeys all hard constraints in the user request.
- If valid, set is_valid to true.
- If it needs revision, set is_valid to false and provide a compact revision_instruction in Korean.
""".strip()

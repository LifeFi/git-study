def build_inline_question_review_prompt(
    *,
    inline_questions_json: str,
    validated_anchors_json: str,
    user_request: str,
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
- Check whether the JSON contains exactly 4 grounded inline questions unless fewer validated anchors were available.
- Check whether each question reuses a validated file_path and anchor_snippet.
- Check whether the questions are reasoning-heavy and not trivial line-matching prompts.
- Check whether the question set covers intent, behavior, tradeoff, and vulnerability as evenly as possible.
- Check whether the content obeys all hard constraints in the user request.
- If valid, set is_valid to true.
- If it needs revision, set is_valid to false and provide a compact revision_instruction in Korean.
""".strip()

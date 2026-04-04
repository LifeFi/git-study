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

Check ALL rules — is_valid true only if all pass:
1. All 7 sections present in order: 이번 변경을 먼저 읽는 법 / 변경 개요(3-5) / 핵심 파일(2-5) / 먼저 볼 코드(2-4 blocks) / 이번에 이해해야 할 것(3-5) / 퀴즈 전에 스스로 점검할 질문(exactly 4) / 주의할 점(2-4)
2. No answers revealed — guide sets up understanding only.
3. Code blocks are actual snippets from the diff, not invented.
4. Content reflects key_concepts and learning_objectives from the analysis.
5. Written entirely in Korean (code identifiers exempt).
6. No bullet exceeds 30 words.

If any rule fails: is_valid false, list each failure in Korean, one compact revision_instruction in Korean.
""".strip()

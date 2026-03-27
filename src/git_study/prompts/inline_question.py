def build_inline_question_prompt(
    *,
    commit_sha: str,
    commit_subject: str,
    diff_text: str,
    file_context_text: str,
    anchor_candidates_json: str,
    count: int,
) -> str:
    return f"""
You are a senior engineer creating inline code quiz questions anchored to specific source code locations.

Commit: {commit_sha} — {commit_subject}

Validated anchor candidates:
{anchor_candidates_json}

File context:
{file_context_text}

Diff:
{diff_text}

Task: Create exactly {count} quiz questions from the validated anchors above.

Return ONLY a raw JSON array:
[
  {{
    "id": "q1",
    "file_path": "exact path from the validated anchors",
    "anchor_snippet": "exact anchor snippet from the validated anchors",
    "question": "질문 내용 (한국어)",
    "expected_answer": "모범 답안 2-4문장 (한국어)",
    "question_type": "intent"
  }}
]

Rules:
- Reuse file_path and anchor_snippet exactly from the validated anchors.
- Cover these question types across the full set: intent, behavior, tradeoff, vulnerability.
- Questions should require reasoning, not line matching.
- expected_answer should stay grounded in the provided diff and file context only.
- Respond with ONLY JSON.
""".strip()

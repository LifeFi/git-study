def build_inline_anchor_prompt(
    *,
    commit_sha: str,
    commit_subject: str,
    changed_files_summary: str,
    diff_text: str,
    file_context_text: str,
    count: int,
    actual_paths: list[str],
) -> str:
    paths_list = "\n".join(f"  - {path}" for path in actual_paths) or "  (경로 없음)"
    return f"""
You are a senior engineer selecting anchor points for inline code-study questions.

Commit: {commit_sha} — {commit_subject}

Exact file paths:
{paths_list}

Changed files summary:
{changed_files_summary}

File context (full content of changed files at this commit):
{file_context_text}

Diff:
{diff_text}

Return ONLY a raw JSON array with up to {count + 2} candidates:
[
  {{
    "file_path": "<exact path from the list above>",
    "anchor_snippet": "exact 3-5 consecutive lines copied verbatim from the file context",
    "question_type": "intent | behavior | tradeoff | vulnerability",
    "reason": "why this location is worth asking about in Korean"
  }}
]

Rules:
- file_path MUST exactly match one of the listed paths.
- anchor_snippet MUST be copied verbatim from file_context_text.
- Prefer anchors that require reasoning about behavior, intent, risks, or trade-offs.
- Avoid trivial anchors like imports or blank lines unless they are central to the change.
- Cover all question_type values if possible.
- Respond with ONLY JSON.
""".strip()

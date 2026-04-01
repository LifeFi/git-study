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

File context (full content of changed files at this commit, with line numbers):
{file_context_text}

Diff:
{diff_text}

Return ONLY a raw JSON array with up to {count + 2} candidates:
[
  {{
    "file_path": "<exact path from the list above>",
    "anchor_line": <line number from the file context (the number before the | separator)>,
    "anchor_snippet": "the 1-2 lines at that location (for verification only)",
    "question_type": "intent | behavior | tradeoff | vulnerability",
    "reason": "이 위치가 질문할 가치가 있는 이유 (한국어)"
  }}
]

Rules:
- file_path MUST exactly match one of the listed paths.
- anchor_line MUST be a line number visible in the file context (the number before the | separator).
- anchor_snippet is for your own verification — it does not need to be exact.
- Prefer anchors that require reasoning about behavior, intent, risks, or trade-offs.
- Avoid trivial anchors like imports or blank lines unless they are central to the change.
- Cover all question_type values if possible.
- Respond with ONLY JSON.
""".strip()

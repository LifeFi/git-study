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
- Cover all question_type values if possible.
- Respond with ONLY JSON.

Bad anchor examples — NEVER select these:
- Import statements (e.g., `import os`, `from x import y`)
- Variable declarations where the name makes the purpose obvious (e.g., `max_retries = 3`)
- Lines identical in intent to the surrounding 3 lines (context provides no additional insight)
- Blank lines, comment-only lines, closing braces or brackets

Good anchor examples — PREFER these:
- A condition that guards against a non-obvious edge case (removing it would cause a subtle bug)
- A line that changed the previous behavior (visible in the diff as a modification)
- Error handling whose absence would cause a hard-to-notice failure
- A design decision point where the author chose one approach over an equally plausible alternative
- A line that introduces a constraint, limit, or side effect not apparent from its name alone
""".strip()

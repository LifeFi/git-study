_AUTHOR_PRIORITY: dict[str, str] = {
    "self": "Prioritize: tradeoff, vulnerability, test, architecture (you wrote it — focus on blind spots and verification)",
    "others": "Prioritize: intent, behavior, architecture, improvement (understand why/how it works, then suggest improvements)",
    "ai": "Prioritize: vulnerability, test, performance, improvement (AI code often misses these)",
}


def _author_type_hint(author_context: str) -> str:
    return _AUTHOR_PRIORITY.get(author_context, _AUTHOR_PRIORITY["self"])


def _file_spread_rule(num_files: int) -> str:
    if num_files <= 1:
        return ""
    return (
        f"- {num_files} files are changed. Prefer to spread questions across different files. "
        f"You may place multiple questions in the same file if it contains the most significant changes."
    )


def build_inline_combined_prompt(
    *,
    commit_sha: str,
    commit_subject: str,
    changed_files_summary: str,
    diff_text: str,
    file_context_text: str,
    count: int,
    actual_paths: list[str],
    user_request: str = "",
    author_context: str = "self",
) -> str:
    """앵커 선정 + 질문 생성을 단일 LLM 호출로 수행하는 통합 프롬프트."""
    paths_list = "\n".join(f"  - {path}" for path in actual_paths) or "  (경로 없음)"
    request_block = (
        f"\nAdditional request:\n{user_request.strip()}\n" if user_request.strip() else ""
    )
    spread_rule = _file_spread_rule(len(actual_paths))
    spread_block = f"\n{spread_rule}" if spread_rule else ""
    author_hint = _author_type_hint(author_context)
    return f"""
You are a senior engineer creating inline code quiz questions anchored to specific source code locations.

Commit: {commit_sha} — {commit_subject}

Exact file paths you MUST use:
{paths_list}

Changed files summary:
{changed_files_summary}

File context (full content with line numbers):
{file_context_text}

Diff:
{diff_text}
{request_block}

Task: Select {count} anchor points and create one quiz question per anchor.

Step 1 — Select anchor points:
- file_path MUST exactly match one of the listed paths above.
- anchor_line MUST be a line number visible in the file context (number before the | separator).{spread_block}
- PREFER these anchor types:
  - A condition guarding a non-obvious edge case
  - A line changed in the diff (visible as + or modified)
  - Error handling whose absence causes hard-to-notice failures
  - A design decision point where another approach was equally plausible
  - A line introducing a constraint, limit, or side effect not apparent from its name
- AVOID: import statements, obvious variable declarations, blank lines, comment-only lines, closing braces

Step 2 — Assign question types (one per anchor, chosen during anchor selection):
- Available types: intent, behavior, tradeoff, vulnerability, test, performance, architecture, improvement
- {author_hint}
- Choose the type that best fits what the anchor's code reveals — do NOT assign by position.
- Avoid duplicate types across questions. Aim for variety.
- For "improvement" type: ask for a concrete alternative design or refactoring approach, not just identification of a problem.
- For "architecture" type: ask how this code fits into or affects the overall system structure.

Step 3 — Create questions:
- Questions must require reasoning, not line-matching.
- expected_answer must be grounded in the provided diff and file context only.
- For "improvement" type questions, expected_answer should describe a concrete better approach with rationale.

Return ONLY a raw JSON array:
[
  {{
    "id": "q1",
    "file_path": "<exact path from the list above>",
    "anchor_line": <line number from file context>,
    "anchor_snippet": "1-2 lines at that location",
    "question": "질문 내용 (한국어)",
    "expected_answer": "모범 답안 2-4문장 (한국어)",
    "question_type": "intent | behavior | tradeoff | vulnerability | test | performance | architecture | improvement"
  }}
]

Respond with ONLY JSON.
""".strip()


def build_inline_question_prompt(
    *,
    commit_sha: str,
    commit_subject: str,
    diff_text: str,
    file_context_text: str,
    anchor_candidates_json: str,
    count: int,
    user_request: str = "",
) -> str:
    request_block = (
        f"\nAdditional request:\n{user_request.strip()}\n" if user_request.strip() else ""
    )
    return f"""
You are a senior engineer creating inline code quiz questions anchored to specific source code locations.

Commit: {commit_sha} — {commit_subject}

Validated anchor candidates:
{anchor_candidates_json}

File context:
{file_context_text}

Diff:
{diff_text}
{request_block}

Task: Create exactly {count} quiz questions from the validated anchors above.

Return ONLY a raw JSON array:
[
  {{
    "id": "q1",
    "file_path": "exact path from the validated anchors",
    "anchor_line": <exact anchor_line from the validated anchors>,
    "anchor_snippet": "1-2 lines at that location",
    "question": "질문 내용 (한국어)",
    "expected_answer": "모범 답안 2-4문장 (한국어)",
    "question_type": "intent | behavior | tradeoff | vulnerability | test | performance | architecture | improvement"
  }}
]

Rules:
- Reuse file_path and anchor_line exactly from the validated anchors.
- Use the question_type from each validated anchor as-is. If you must change a type, prefer one not already used by other questions.
- Questions should require reasoning, not line matching.
- expected_answer should stay grounded in the provided diff and file context only.
- For "improvement" type questions, expected_answer should describe a concrete better approach with rationale.
- Respond with ONLY JSON.
""".strip()

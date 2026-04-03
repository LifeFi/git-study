_TYPE_SEQUENCE = ["intent", "behavior", "tradeoff", "vulnerability"]


def _type_assignment_lines(count: int) -> str:
    return "\n".join(
        f'- Question {i + 1} (id: "q{i + 1}"): {_TYPE_SEQUENCE[i % len(_TYPE_SEQUENCE)]}'
        for i in range(count)
    )


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
) -> str:
    """앵커 선정 + 질문 생성을 단일 LLM 호출로 수행하는 통합 프롬프트."""
    paths_list = "\n".join(f"  - {path}" for path in actual_paths) or "  (경로 없음)"
    request_block = (
        f"\nAdditional request:\n{user_request.strip()}\n" if user_request.strip() else ""
    )
    spread_rule = _file_spread_rule(len(actual_paths))
    spread_block = f"\n{spread_rule}" if spread_rule else ""
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

Step 2 — Assign question types in this exact order:
{_type_assignment_lines(count)}

Step 3 — Create questions:
- Questions must require reasoning, not line-matching.
- expected_answer must be grounded in the provided diff and file context only.

Return ONLY a raw JSON array:
[
  {{
    "id": "q1",
    "file_path": "<exact path from the list above>",
    "anchor_line": <line number from file context>,
    "anchor_snippet": "1-2 lines at that location",
    "question": "질문 내용 (한국어)",
    "expected_answer": "모범 답안 2-4문장 (한국어)",
    "question_type": "intent | behavior | tradeoff | vulnerability"
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
Assign question types in this exact order:
{_type_assignment_lines(count)}

Return ONLY a raw JSON array:
[
  {{
    "id": "q1",
    "file_path": "exact path from the validated anchors",
    "anchor_line": <exact anchor_line from the validated anchors>,
    "anchor_snippet": "1-2 lines at that location",
    "question": "질문 내용 (한국어)",
    "expected_answer": "모범 답안 2-4문장 (한국어)",
    "question_type": "intent | behavior | tradeoff | vulnerability"
  }}
]

Rules:
- Reuse file_path and anchor_line exactly from the validated anchors.
- Questions should require reasoning, not line matching.
- expected_answer should stay grounded in the provided diff and file context only.
- Respond with ONLY JSON.
""".strip()

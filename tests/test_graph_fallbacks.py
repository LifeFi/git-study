from git_study.graphs.general_grade_graph import (
    finalize_grades as finalize_general_grades,
    grade_answers as grade_general_answers,
    validate_grades as validate_general_grades,
)
from git_study.graphs.inline_grade_graph import (
    finalize_grades,
    grade_answers as grade_inline_answers,
    validate_grades as validate_inline_grades,
)
from git_study.graphs.inline_quiz_graph import (
    extract_anchor_candidates,
    finalize_inline_questions,
    generate_inline_questions,
    repair_inline_questions,
    review_inline_questions,
    route_after_inline_review,
    validate_anchor_candidates,
)
from git_study.tools.code_context import get_neighbor_code_context
from git_study.graphs.commit_analysis_subgraph import analyze_change as analyze_commit_change
from git_study.graphs.quiz_graph import (
    analyze_change,
    draft_quiz,
    finalize_quiz,
    repair_quiz,
    route_after_quiz_review,
    review_quiz,
)
from git_study.graphs.read_graph import (
    analyze_change as analyze_read_change,
    repair_reading,
    review_reading,
    route_after_read_review,
)
from git_study.prompts.quiz_generation import build_quiz_generation_prompt
from git_study.prompts.quiz_review import build_quiz_review_prompt
from git_study.tui.inline_quiz import InlineQuizDock
from git_study.types import InlineQuizQuestion


def test_analyze_change_returns_fallback_output_when_diff_missing() -> None:
    result = analyze_change(
        {
            "diff_text": "",
            "commit_subject": "docs update",
            "commit_sha": "abcdef123456",
            "commit_author": "Tester",
            "commit_date": "2026-03-27T10:00:00+09:00",
        }
    )

    assert result["final_output"].startswith("최근 커밋에서 퀴즈를 만들 만한 텍스트 diff를 찾지 못했습니다.")
    assert result["analysis"]["question_plan"] == []


def test_read_analyze_change_returns_fallback_output_when_diff_missing() -> None:
    result = analyze_read_change(
        {
            "diff_text": "",
            "commit_subject": "docs update",
            "commit_sha": "abcdef123456",
            "commit_author": "Tester",
            "commit_date": "2026-03-27T10:00:00+09:00",
        }
    )

    assert result["final_output"].startswith("최근 커밋에서 읽을거리를 만들 만한 텍스트 diff를 찾지 못했습니다.")
    assert result["analysis"]["question_plan"] == []


def test_commit_analysis_subgraph_uses_output_kind_for_fallback_message() -> None:
    result = analyze_commit_change(
        {
            "output_kind": "read",
            "diff_text": "",
            "commit_subject": "docs update",
            "commit_sha": "abcdef123456",
            "commit_author": "Tester",
            "commit_date": "2026-03-27T10:00:00+09:00",
        }
    )

    assert result["final_output"].startswith("최근 커밋에서 읽을거리를 만들 만한 텍스트 diff를 찾지 못했습니다.")


def test_commit_analysis_analyze_change_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return {
                "summary_bullets": ["핵심 요약"],
                "key_files": ["src/a.py"],
                "key_snippets": [
                    {
                        "path": "src/a.py",
                        "code": "def run():\n    return 1",
                        "reason": "핵심 흐름",
                    }
                ],
                "learning_objectives": ["동작 이해"],
                "question_plan": [
                    {
                        "type": "intent",
                        "focus": "의도",
                        "path": "src/a.py",
                        "code_hint": "run",
                    },
                    {
                        "type": "behavior",
                        "focus": "동작",
                        "path": "src/a.py",
                        "code_hint": "run",
                    },
                    {
                        "type": "tradeoff",
                        "focus": "트레이드오프",
                        "path": "src/a.py",
                        "code_hint": "run",
                    },
                    {
                        "type": "vulnerability",
                        "focus": "취약점",
                        "path": "src/a.py",
                        "code_hint": "run",
                    },
                ],
                "change_risks": ["회귀 위험"],
            }

    monkeypatch.setattr(
        "git_study.graphs.commit_analysis_subgraph.LLMClient",
        StubLLMClient,
    )

    result = analyze_commit_change(
        {
            "messages": [{"role": "user", "content": "AGENTS.md는 제외해줘"}],
            "difficulty": "medium",
            "analysis_quiz_style": "mixed",
            "selected_context_note": "",
            "commit_sha": "abcdef1",
            "commit_subject": "subject",
            "commit_author": "author",
            "commit_date": "2026-03-28T12:00:00+09:00",
            "changed_files_summary": "M src/a.py",
            "diff_text": "diff --git a/src/a.py b/src/a.py",
            "file_context_text": "def run():\n    return 1",
        }
    )

    assert result["analysis"]["summary_bullets"] == ["핵심 요약"]
    assert "AGENTS.md는 제외해줘" in captured["prompt"]


def test_validate_anchor_candidates_uses_file_context_and_fallback() -> None:
    result = validate_anchor_candidates(
        {
            "count": 4,
            "actual_paths": ["src/a.py"],
            "file_context_map": {"src/a.py": "def run():\n    return 1\n\nvalue = run()\n"},
            "anchor_candidates": [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "missing()",
                    "question_type": "behavior",
                    "reason": "없음",
                }
            ],
        }
    )

    assert len(result["validated_anchors"]) == 1
    assert result["validated_anchors"][0]["file_path"] == "src/a.py"
    assert "def run()" in result["validated_anchors"][0]["anchor_snippet"]


def test_finalize_inline_questions_builds_default_question_when_generation_empty() -> None:
    result = finalize_inline_questions(
        {
            "count": 1,
            "validated_anchors": [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question_type": "intent",
                    "reason": "핵심 흐름",
                }
            ],
            "inline_questions": [],
        }
    )

    question = result["inline_questions"][0]
    assert question["id"] == "q1"
    assert question["file_path"] == "src/a.py"
    assert "맡는 역할" in question["question"]


def test_get_neighbor_code_context_returns_anchor_neighbors() -> None:
    result = get_neighbor_code_context(
        file_context_map={
            "src/a.py": "def one():\n    pass\n\ndef two():\n    return 2\n\nvalue = two()\n"
        },
        file_path="src/a.py",
        anchor_snippet="def two():\n    return 2",
        before_lines=1,
        after_lines=2,
    )

    assert "FILE: src/a.py" in result
    assert "STATUS: ok" in result
    assert "def two():" in result
    assert "value = two()" in result


def test_generate_inline_questions_uses_neighbor_context_tool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubLLMClient:
        def invoke_json_with_tools(self, prompt: str, tools, **kwargs):
            captured["prompt"] = prompt
            captured["tool_names"] = [getattr(tool, "name", "") for tool in tools]
            captured["require_tool"] = kwargs.get("require_tool")
            return [
                {
                    "id": "q1",
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question": "질문",
                    "expected_answer": "답변",
                    "question_type": "intent",
                }
            ]

    monkeypatch.setattr(
        "git_study.graphs.inline_quiz_graph.LLMClient",
        StubLLMClient,
    )

    result = generate_inline_questions(
        {
            "count": 1,
            "user_request": "",
            "commit_context": {
                "commit_sha": "abcdef1",
                "commit_subject": "subject",
                "diff_text": "diff --git a/src/a.py b/src/a.py",
                "file_context_text": "FILE: src/a.py\n```python\ndef run():\n    return 1\n```",
            },
            "file_context_map": {"src/a.py": "def run():\n    return 1\n"},
            "validated_anchors": [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question_type": "intent",
                    "reason": "핵심",
                }
            ],
        }
    )

    assert captured["tool_names"] == ["get_neighbor_code_context"]
    assert captured["require_tool"] is True
    assert "get_neighbor_code_context" in str(captured["prompt"])
    assert result["inline_questions"][0]["id"] == "q1"


def test_finalize_quiz_builds_missing_general_questions_from_analysis() -> None:
    result = finalize_quiz(
        {
            "analysis": {
                "summary_bullets": ["요약"],
                "key_files": ["src/a.py"],
                "key_snippets": [{"path": "src/a.py", "code": "def run():\n    return 1", "reason": "핵심"}],
                "learning_objectives": ["흐름 이해"],
                "question_plan": [
                    {"type": "intent", "focus": "의도", "path": "src/a.py", "code_hint": "run"},
                    {"type": "behavior", "focus": "동작", "path": "src/a.py", "code_hint": "run"},
                    {"type": "tradeoff", "focus": "트레이드오프", "path": "src/a.py", "code_hint": "run"},
                    {"type": "vulnerability", "focus": "취약점", "path": "src/a.py", "code_hint": "run"},
                ],
                "change_risks": ["회귀"],
            },
            "quiz_questions": [],
        }
    )

    assert len(result["quiz_questions"]) == 4
    assert result["quiz_questions"][0]["id"] == "q1"
    assert "## 학습 질문" in result["final_output"]


def test_finalize_grades_fills_missing_question_with_default_grade() -> None:
    questions: list[InlineQuizQuestion] = [
        {
            "id": "q1",
            "file_path": "src/a.py",
            "anchor_snippet": "line1",
            "question": "질문1",
            "expected_answer": "답1",
            "question_type": "intent",
        },
        {
            "id": "q2",
            "file_path": "src/b.py",
            "anchor_snippet": "line2",
            "question": "질문2",
            "expected_answer": "답2",
            "question_type": "behavior",
        },
    ]

    result = finalize_grades(
        {
            "questions": questions,
            "review_result": {
                "normalized_grades": [
                    {"id": "q1", "score": 85, "feedback": "좋습니다."},
                ]
            },
        }
    )

    assert result["final_grades"] == [
        {"id": "q1", "score": 85, "feedback": "좋습니다."},
        {"id": "q2", "score": 0, "feedback": "채점 결과가 누락되어 기본값으로 처리했습니다."},
    ]


def test_finalize_inline_grades_falls_back_to_question_order_when_ids_do_not_match() -> None:
    questions: list[InlineQuizQuestion] = [
        {
            "id": "q1",
            "file_path": "src/a.py",
            "anchor_snippet": "line1",
            "question": "질문1",
            "expected_answer": "답1",
            "question_type": "intent",
        },
        {
            "id": "q2",
            "file_path": "src/b.py",
            "anchor_snippet": "line2",
            "question": "질문2",
            "expected_answer": "답2",
            "question_type": "behavior",
        },
    ]

    result = finalize_grades(
        {
            "questions": questions,
            "review_result": {
                "normalized_grades": [
                    {"id": "wrong-1", "score": 85, "feedback": "좋습니다."},
                    {"id": "wrong-2", "score": 70, "feedback": "보완이 필요합니다."},
                ]
            },
        }
    )

    assert result["final_grades"] == [
        {"id": "q1", "score": 85, "feedback": "좋습니다."},
        {"id": "q2", "score": 70, "feedback": "보완이 필요합니다."},
    ]


def test_finalize_general_grades_uses_raw_grades_when_review_normalized_grades_is_empty() -> None:
    finalized = finalize_general_grades(
        {
            "questions": [
                {"id": "q1", "question": "질문1"},
                {"id": "q2", "question": "질문2"},
            ],
            "raw_grades": [
                {"id": "q1", "score": 90, "feedback": "좋음"},
                {"id": "q2", "score": 70, "feedback": "보완 필요"},
            ],
            "review_result": {
                "is_valid": False,
                "normalized_grades": [],
                "grading_summary": {},
            },
        }
    )

    assert finalized["final_grades"] == [
        {"id": "q1", "score": 90, "feedback": "좋음"},
        {"id": "q2", "score": 70, "feedback": "보완 필요"},
    ]


def test_draft_quiz_passes_last_basic_request_into_generation_prompt(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return []

    def fake_build_quiz_generation_prompt(**kwargs):
        captured["user_request"] = kwargs["user_request"]
        return "generation prompt"

    monkeypatch.setattr("git_study.graphs.quiz_graph.LLMClient", StubLLMClient)
    monkeypatch.setattr(
        "git_study.graphs.quiz_graph.build_quiz_generation_prompt",
        fake_build_quiz_generation_prompt,
    )
    monkeypatch.setattr(
        "git_study.graphs.quiz_graph.normalize_general_questions",
        lambda payload: payload,
    )

    draft_quiz(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "AGENTS.md 질문은 절대 넣지 말아줘",
                }
            ],
            "difficulty": "medium",
            "quiz_style": "mixed",
            "selected_context_note": "",
            "commit_sha": "abcdef1",
            "commit_subject": "subject",
            "commit_author": "author",
            "commit_date": "2026-03-28T12:00:00+09:00",
            "changed_files_summary": "M src/a.py",
            "diff_text": "diff --git a/src/a.py b/src/a.py",
            "file_context_text": "def run():\n    pass",
            "analysis": {"question_plan": []},
        }
    )

    assert captured["user_request"] == "AGENTS.md 질문은 절대 넣지 말아줘"
    assert captured["prompt"] == "generation prompt"


def test_review_quiz_passes_last_basic_request_into_review_prompt(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return {"is_valid": True, "issues": [], "revision_instruction": ""}

    def fake_build_quiz_review_prompt(**kwargs):
        captured["user_request"] = kwargs["user_request"]
        return "review prompt"

    monkeypatch.setattr("git_study.graphs.quiz_graph.LLMClient", StubLLMClient)
    monkeypatch.setattr(
        "git_study.graphs.quiz_graph.build_quiz_review_prompt",
        fake_build_quiz_review_prompt,
    )
    monkeypatch.setattr(
        "git_study.graphs.quiz_graph.normalize_quiz_review",
        lambda payload: payload,
    )

    review_quiz(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "AGENTS.md 질문은 절대 넣지 말아줘",
                }
            ],
            "analysis": {"question_plan": []},
            "quiz_questions": [{"id": "q1", "question": "질문"}],
        }
    )

    assert captured["user_request"] == "AGENTS.md 질문은 절대 넣지 말아줘"
    assert captured["prompt"] == "review prompt"


def test_review_reading_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return {"is_valid": True, "issues": [], "revision_instruction": ""}

    monkeypatch.setattr("git_study.graphs.read_graph.LLMClient", StubLLMClient)
    monkeypatch.setattr(
        "git_study.graphs.read_graph.normalize_quiz_review",
        lambda payload: payload,
    )

    result = review_reading(
        {
            "analysis": {"question_plan": []},
            "reading_draft": "# Reading",
        }
    )

    assert result["reading_review"]["is_valid"] is True
    assert "# Reading" in captured["prompt"]


def test_validate_general_grades_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return {
                "is_valid": True,
                "issues": [],
                "revision_instruction": "",
                "normalized_grades": [{"id": "q1", "score": 90, "feedback": "좋음"}],
                "grading_summary": {
                    "weak_points": [],
                    "weak_files": [],
                    "next_steps": [],
                    "overall_comment": "",
                },
            }

    monkeypatch.setattr("git_study.graphs.general_grade_graph.LLMClient", StubLLMClient)

    result = validate_general_grades(
        {
            "questions": [
                {
                    "id": "q1",
                    "question_type": "intent",
                    "question": "질문",
                    "code_reference": "src/a.py",
                }
            ],
            "raw_grades": [{"id": "q1", "score": 90, "feedback": "좋음"}],
        }
    )

    assert result["review_result"]["is_valid"] is True
    assert '"q1"' in captured["prompt"]


def test_grade_general_answers_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return [{"id": "q1", "score": 90, "feedback": "좋음"}]

    monkeypatch.setattr("git_study.graphs.general_grade_graph.LLMClient", StubLLMClient)

    result = grade_general_answers(
        {
            "question_blocks": "--- q1 ---",
            "user_request": "엄격하게 채점해줘",
        }
    )

    assert result["raw_grades"] == [{"id": "q1", "score": 90, "feedback": "좋음"}]
    assert "엄격하게 채점해줘" in captured["prompt"]


def test_validate_inline_grades_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return {
                "is_valid": True,
                "issues": [],
                "revision_instruction": "",
                "normalized_grades": [{"id": "q1", "score": 90, "feedback": "좋음"}],
                "grading_summary": {
                    "weak_points": [],
                    "weak_files": [],
                    "next_steps": [],
                    "overall_comment": "",
                },
            }

    monkeypatch.setattr("git_study.graphs.inline_grade_graph.LLMClient", StubLLMClient)

    result = validate_inline_grades(
        {
            "questions": [
                {
                    "id": "q1",
                    "question_type": "intent",
                    "question": "질문",
                    "file_path": "src/a.py",
                }
            ],
            "raw_grades": [{"id": "q1", "score": 90, "feedback": "좋음"}],
        }
    )

    assert result["review_result"]["is_valid"] is True
    assert '"q1"' in captured["prompt"]


def test_grade_inline_answers_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return [{"id": "q1", "score": 85, "feedback": "좋습니다."}]

    monkeypatch.setattr("git_study.graphs.inline_grade_graph.LLMClient", StubLLMClient)

    result = grade_inline_answers(
        {
            "question_blocks": "--- q1 ---",
            "user_request": "코드 정확성을 더 엄격하게 봐줘",
        }
    )

    assert result["raw_grades"] == [{"id": "q1", "score": 85, "feedback": "좋습니다."}]
    assert "코드 정확성을 더 엄격하게 봐줘" in captured["prompt"]


def test_route_after_quiz_review_sends_invalid_result_to_repair() -> None:
    assert route_after_quiz_review({"quiz_review": {"is_valid": False}}) == "repair"
    assert (
        route_after_quiz_review(
            {"quiz_review": {"is_valid": False}, "repair_attempts": 1}
        )
        == "finalize"
    )


def test_repair_quiz_uses_revision_instruction(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return []

    monkeypatch.setattr("git_study.graphs.quiz_graph.LLMClient", StubLLMClient)
    monkeypatch.setattr(
        "git_study.graphs.quiz_graph.normalize_general_questions",
        lambda payload: payload,
    )

    result = repair_quiz(
        {
            "messages": [{"role": "user", "content": "이건 꼭 빼줘"}],
            "difficulty": "medium",
            "quiz_style": "mixed",
            "selected_context_note": "",
            "commit_sha": "abcdef1",
            "commit_subject": "subject",
            "commit_author": "author",
            "commit_date": "2026-03-28T12:00:00+09:00",
            "changed_files_summary": "M src/a.py",
            "diff_text": "diff --git a/src/a.py b/src/a.py",
            "file_context_text": "def run():\n    pass",
            "analysis": {"question_plan": []},
            "quiz_review": {"revision_instruction": "AGENTS.md를 빼고 다시 작성"},
        }
    )

    assert "AGENTS.md를 빼고 다시 작성" in captured["prompt"]
    assert result["repair_attempts"] == 1


def test_extract_anchor_candidates_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question_type": "intent",
                    "reason": "핵심 흐름",
                }
            ]

    monkeypatch.setattr("git_study.graphs.inline_quiz_graph.LLMClient", StubLLMClient)

    result = extract_anchor_candidates(
        {
            "commit_context": {
                "commit_sha": "abcdef1",
                "commit_subject": "subject",
                "changed_files_summary": "M src/a.py",
                "diff_text": "diff --git a/src/a.py b/src/a.py",
                "file_context_text": "def run():\n    return 1",
            },
            "count": 4,
            "actual_paths": ["src/a.py"],
        }
    )

    assert result["anchor_candidates"][0]["file_path"] == "src/a.py"
    assert "subject" in captured["prompt"]


def test_generate_inline_questions_uses_neighbor_context_tool(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_json_with_tools(self, prompt: str, tools, **kwargs):
            captured["prompt"] = prompt
            captured["tool_name"] = getattr(tools[0], "name", "")
            return [
                {
                    "id": "q1",
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question": "이 코드의 역할은?",
                    "expected_answer": "흐름을 설명해야 합니다.",
                    "question_type": "intent",
                }
            ]

    monkeypatch.setattr("git_study.graphs.inline_quiz_graph.LLMClient", StubLLMClient)

    result = generate_inline_questions(
        {
            "commit_context": {
                "commit_sha": "abcdef1",
                "commit_subject": "subject",
                "diff_text": "diff --git a/src/a.py b/src/a.py",
                "file_context_text": "def run():\n    return 1",
            },
            "count": 4,
            "user_request": "개념 위주로 만들어줘",
            "validated_anchors": [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question_type": "intent",
                    "reason": "핵심 흐름",
                }
            ],
        }
    )

    assert result["inline_questions"][0]["id"] == "q1"
    assert "개념 위주로 만들어줘" in captured["prompt"]
    assert captured["tool_name"] == "get_neighbor_code_context"


def test_review_inline_questions_uses_structured_output(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_structured(self, prompt: str, schema, **kwargs):
            captured["prompt"] = prompt
            return {"is_valid": True, "issues": [], "revision_instruction": ""}

    monkeypatch.setattr("git_study.graphs.inline_quiz_graph.LLMClient", StubLLMClient)

    result = review_inline_questions(
        {
            "user_request": "개념 위주로 만들어줘",
            "validated_anchors": [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question_type": "intent",
                    "reason": "핵심 흐름",
                }
            ],
            "inline_questions": [
                {
                    "id": "q1",
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question": "이 코드의 역할은?",
                    "expected_answer": "흐름을 설명해야 합니다.",
                    "question_type": "intent",
                }
            ],
        }
    )

    assert result["inline_review"]["is_valid"] is True
    assert "개념 위주로 만들어줘" in captured["prompt"]


def test_route_after_inline_review_sends_invalid_result_to_repair() -> None:
    assert route_after_inline_review({"inline_review": {"is_valid": False}}) == "repair"
    assert (
        route_after_inline_review(
            {"inline_review": {"is_valid": False}, "repair_attempts": 1}
        )
        == "finalize"
    )


def test_repair_inline_questions_uses_revision_instruction(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_json_with_tools(self, prompt: str, tools, **kwargs):
            captured["prompt"] = prompt
            captured["tool_name"] = getattr(tools[0], "name", "")
            return [
                {
                    "id": "q1",
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question": "수정된 질문",
                    "expected_answer": "수정된 답",
                    "question_type": "intent",
                }
            ]

    monkeypatch.setattr("git_study.graphs.inline_quiz_graph.LLMClient", StubLLMClient)

    result = repair_inline_questions(
        {
            "commit_context": {
                "commit_sha": "abcdef1",
                "commit_subject": "subject",
                "diff_text": "diff --git a/src/a.py b/src/a.py",
                "file_context_text": "def run():\n    return 1",
            },
            "count": 4,
            "user_request": "개념 위주로 만들어줘",
            "validated_anchors": [
                {
                    "file_path": "src/a.py",
                    "anchor_snippet": "def run():\n    return 1",
                    "question_type": "intent",
                    "reason": "핵심 흐름",
                }
            ],
            "inline_review": {"revision_instruction": "질문을 더 깊게 만들어줘"},
        }
    )

    assert "질문을 더 깊게 만들어줘" in captured["prompt"]
    assert captured["tool_name"] == "get_neighbor_code_context"
    assert result["inline_questions"][0]["question"] == "수정된 질문"
    assert result["repair_attempts"] == 1


def test_quiz_generation_prompt_includes_agents_exclusion_rule() -> None:
    prompt = build_quiz_generation_prompt(
        user_request="AGENTS.md 질문은 절대 넣지 말아줘",
        difficulty="medium",
        quiz_style="mixed",
        output_mode="quiz",
        selected_context_note="",
        commit_sha="abcdef1",
        commit_subject="subject",
        commit_author="author",
        commit_date="2026-03-28T12:00:00+09:00",
        changed_files_summary="M src/a.py",
        diff_text="diff --git a/src/a.py b/src/a.py",
        file_context_text="def run():\n    pass",
        analysis_json="{}",
    )

    assert "AGENTS.md 질문은 절대 넣지 말아줘" in prompt
    assert "Treat the user request as a hard requirement" in prompt
    assert "do not reference `AGENTS.md`" in prompt


def test_quiz_review_prompt_includes_user_request_constraints() -> None:
    prompt = build_quiz_review_prompt(
        quiz_questions_json='[{"id":"q1"}]',
        analysis_json="{}",
        user_request="AGENTS.md 질문은 절대 넣지 말아줘",
    )

    assert "AGENTS.md 질문은 절대 넣지 말아줘" in prompt
    assert "Check whether the quiz obeys all hard constraints in the user request." in prompt
    assert "If the user excluded a file such as `AGENTS.md`" in prompt


def test_route_after_read_review_sends_invalid_result_to_repair() -> None:
    assert route_after_read_review({"reading_review": {"is_valid": False}}) == "repair"
    assert (
        route_after_read_review(
            {"reading_review": {"is_valid": False}, "repair_attempts": 1}
        )
        == "finalize"
    )


def test_repair_reading_uses_revision_instruction(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_text(self, prompt: str):
            captured["prompt"] = prompt
            return "repaired reading"

    monkeypatch.setattr("git_study.graphs.read_graph.LLMClient", StubLLMClient)

    result = repair_reading(
        {
            "messages": [{"role": "user", "content": "이건 꼭 고려해줘"}],
            "difficulty": "medium",
            "selected_context_note": "",
            "commit_sha": "abcdef1",
            "commit_subject": "subject",
            "commit_author": "author",
            "commit_date": "2026-03-28T12:00:00+09:00",
            "changed_files_summary": "M src/a.py",
            "diff_text": "diff --git a/src/a.py b/src/a.py",
            "file_context_text": "def run():\n    pass",
            "analysis": {"question_plan": []},
            "reading_review": {"revision_instruction": "개념 설명을 더 강화"},
        }
    )

    assert "개념 설명을 더 강화" in captured["prompt"]
    assert result["reading_draft"] == "repaired reading"
    assert result["repair_attempts"] == 1


def test_inline_quiz_resolves_same_file_hint_independently_per_anchor() -> None:
    dock = InlineQuizDock()
    dock._known_files = {
        "src/a.py": "def alpha():\n    return 1\n",
        "src/b.py": "def beta():\n    return 2\n",
    }

    first = dock._resolve_to_known("unknown.py", "def alpha():\n    return 1")
    second = dock._resolve_to_known("unknown.py", "def beta():\n    return 2")

    assert first == "src/a.py"
    assert second == "src/b.py"

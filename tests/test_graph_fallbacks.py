from git_study.graphs.inline_grade_graph import finalize_grades
from git_study.graphs.inline_quiz_graph import finalize_inline_questions, validate_anchor_candidates
from git_study.graphs.quiz_graph import (
    analyze_change,
    draft_quiz,
    finalize_quiz,
    review_quiz,
)
from git_study.graphs.read_graph import analyze_change as analyze_read_change
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


def test_draft_quiz_passes_last_basic_request_into_generation_prompt(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class StubLLMClient:
        def invoke_json(self, prompt: str):
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
        def invoke_json(self, prompt: str):
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

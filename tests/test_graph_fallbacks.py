from git_study.graphs.inline_grade_graph import finalize_grades
from git_study.graphs.inline_quiz_graph import finalize_inline_questions, validate_anchor_candidates
from git_study.graphs.quiz_graph import analyze_change
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

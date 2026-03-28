from git_study.llm.schemas import (
    normalize_general_grade_review,
    normalize_general_questions,
    normalize_inline_anchor_candidates,
    normalize_inline_grade_review,
    normalize_inline_grades,
    normalize_inline_questions,
    normalize_quiz_analysis,
    normalize_quiz_review,
)


def test_normalize_quiz_analysis_fills_missing_question_plan_types() -> None:
    payload = {
        "summary_bullets": ["a", "b", "", "c"],
        "key_files": ["src/a.py", "src/b.py"],
        "key_snippets": [
            {"path": "src/a.py", "code": "print('a')", "reason": "핵심"},
        ],
        "learning_objectives": ["의도 파악"],
        "question_plan": [
            {
                "type": "intent",
                "focus": "변경 의도",
                "path": "src/a.py",
                "code_hint": "main flow",
            }
        ],
        "change_risks": ["회귀 위험"],
    }

    normalized = normalize_quiz_analysis(payload)

    assert normalized["summary_bullets"] == ["a", "b", "c"]
    assert len(normalized["question_plan"]) == 4
    assert [item["type"] for item in normalized["question_plan"]] == [
        "intent",
        "behavior",
        "tradeoff",
        "vulnerability",
    ]


def test_normalize_quiz_review_handles_invalid_shape() -> None:
    normalized = normalize_quiz_review(["bad-shape"])

    assert normalized == {
        "is_valid": False,
        "issues": [],
        "revision_instruction": "",
    }


def test_normalize_inline_anchor_candidates_deduplicates_and_filters_types() -> None:
    payload = [
        {
            "file_path": "src/a.py",
            "anchor_snippet": "if x:\n    return y",
            "question_type": "behavior",
            "reason": "동작 변화",
        },
        {
            "file_path": "src/a.py",
            "anchor_snippet": "if x:\n    return y",
            "question_type": "behavior",
            "reason": "중복",
        },
        {
            "file_path": "src/b.py",
            "anchor_snippet": "danger()",
            "question_type": "weird",
            "reason": "이상한 타입",
        },
    ]

    normalized = normalize_inline_anchor_candidates(payload)

    assert len(normalized) == 2
    assert normalized[0]["question_type"] == "behavior"
    assert normalized[1]["question_type"] == "intent"


def test_normalize_inline_questions_generates_stable_ids() -> None:
    payload = [
        {"id": "q1", "question": "첫 질문", "expected_answer": "답", "question_type": "intent"},
        {"id": "q1", "question": "둘째 질문", "expected_answer": "답", "question_type": "behavior"},
    ]

    normalized = normalize_inline_questions(payload)

    assert normalized[0]["id"] == "q1"
    assert normalized[1]["id"] == "q2"
    assert normalized[1]["question_type"] == "behavior"


def test_normalize_inline_grades_clamps_score_and_deduplicates() -> None:
    payload = [
        {"id": "q1", "score": 120, "feedback": "좋음"},
        {"id": "q1", "score": 10, "feedback": "중복"},
        {"id": "q2", "score": "bad", "feedback": ""},
    ]

    normalized = normalize_inline_grades(payload)

    assert normalized == [
        {"id": "q1", "score": 100, "feedback": "좋음"},
        {"id": "q2", "score": 0, "feedback": ""},
    ]


def test_normalize_general_questions_filters_type_and_stabilizes_ids() -> None:
    payload = [
        {
            "id": "q1",
            "question": "첫 질문",
            "expected_answer": "답",
            "question_type": "behavior",
            "explanation": "해설",
            "code_snippet": "run()",
            "code_language": "python",
            "code_reference": "src/a.py",
            "choices": ["A", "B"],
        },
        {
            "id": "q1",
            "question": "둘째 질문",
            "expected_answer": "답",
            "question_type": "weird",
            "choices": ["", "C"],
        },
    ]

    normalized = normalize_general_questions(payload)

    assert normalized[0]["id"] == "q1"
    assert normalized[0]["question_type"] == "behavior"
    assert normalized[0]["choices"] == ["A", "B"]
    assert normalized[1]["id"] == "q2"
    assert normalized[1]["question_type"] == "intent"
    assert normalized[1]["choices"] == ["C"]


def test_normalize_inline_grade_review_normalizes_nested_grades() -> None:
    payload = {
        "is_valid": True,
        "issues": ["문제 없음"],
        "revision_instruction": "",
        "normalized_grades": [
            {"id": "q1", "score": 88, "feedback": "구체적입니다."},
            {"id": "q2", "score": -5, "feedback": "부족합니다."},
        ],
        "grading_summary": {
            "weak_points": ["의도 설명이 약함"],
            "weak_files": ["src/a.py"],
            "next_steps": ["의도 유형 다시 풀기"],
            "overall_comment": "핵심은 파악했지만 설명이 짧습니다.",
        },
    }

    normalized = normalize_inline_grade_review(payload)

    assert normalized["is_valid"] is True
    assert normalized["normalized_grades"] == [
        {"id": "q1", "score": 88, "feedback": "구체적입니다."},
        {"id": "q2", "score": 0, "feedback": "부족합니다."},
    ]
    assert normalized["grading_summary"]["weak_points"] == ["의도 설명이 약함"]


def test_normalize_general_grade_review_normalizes_summary_fields() -> None:
    payload = {
        "is_valid": True,
        "issues": [],
        "revision_instruction": "",
        "normalized_grades": [
            {"id": "q1", "score": 88, "feedback": "구체적입니다."},
        ],
        "grading_summary": {
            "weak_points": ["동작 변화 이해가 약함"],
            "weak_files": ["src/git_study/tui/app.py"],
            "next_steps": ["동작 유형 문제를 다시 풀어보세요."],
            "overall_comment": "핵심은 이해했지만 세부 흐름 설명이 부족합니다.",
        },
    }

    normalized = normalize_general_grade_review(payload)

    assert normalized["grading_summary"]["weak_files"] == ["src/git_study/tui/app.py"]
    assert normalized["grading_summary"]["next_steps"] == ["동작 유형 문제를 다시 풀어보세요."]

from git_study.services.inline_grade_service import generate_inline_quiz_grades
from git_study.services.inline_quiz_service import generate_inline_quiz_questions
from git_study.services.quiz_service import run_quiz


def test_run_quiz_wraps_final_output_into_messages(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            return {"final_output": "quiz body", "extra": "value"}

    monkeypatch.setattr("git_study.services.quiz_service.quiz_graph", StubGraph())

    result = run_quiz({"messages": [{"role": "user", "content": "hello"}]})

    assert captured["config"] == {"configurable": {"thread_id": "textual-tui-session"}}
    assert result["messages"][0].content == "quiz body"
    assert result["extra"] == "value"


def test_generate_inline_quiz_questions_passes_count_and_context(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload):
            captured["payload"] = payload
            return {"inline_questions": [{"id": "q1"}]}

    monkeypatch.setattr(
        "git_study.services.inline_quiz_service.inline_quiz_graph", StubGraph()
    )

    result = generate_inline_quiz_questions({"commit_sha": "abc"}, count=3)

    assert captured["payload"] == {"commit_context": {"commit_sha": "abc"}, "count": 3}
    assert result == [{"id": "q1"}]


def test_generate_inline_quiz_grades_passes_questions_and_answers(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload):
            captured["payload"] = payload
            return {"final_grades": [{"id": "q1", "score": 90, "feedback": "좋음"}]}

    monkeypatch.setattr(
        "git_study.services.inline_grade_service.inline_grade_graph", StubGraph()
    )

    questions = [
        {
            "id": "q1",
            "file_path": "src/a.py",
            "anchor_snippet": "x = 1",
            "question": "질문",
            "expected_answer": "답",
            "question_type": "intent",
        }
    ]
    answers = {"q1": "사용자 답변"}

    result = generate_inline_quiz_grades(questions, answers)

    assert captured["payload"] == {"questions": questions, "answers": answers}
    assert result == [{"id": "q1", "score": 90, "feedback": "좋음"}]

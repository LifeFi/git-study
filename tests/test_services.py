from git_study.services.general_grade_service import (
    generate_general_quiz_grades,
    stream_general_grade_progress,
)
from git_study.services.inline_grade_service import (
    generate_inline_quiz_grades,
    stream_inline_grade_progress,
)
from git_study.services.inline_quiz_service import (
    generate_inline_quiz_questions,
    stream_inline_quiz_progress,
)
from git_study.services.read_service import run_read, stream_read_progress
from git_study.services.quiz_service import run_quiz, stream_quiz_progress


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


def test_stream_quiz_progress_yields_node_events_and_result(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def stream(self, payload, config=None, stream_mode=None):
            captured["payload"] = payload
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            yield {"resolve_commit_context": {"commit_sha": "abc"}}
            yield {"analyze_change": {"analysis": {"summary_bullets": []}}}
            yield {"finalize_quiz": {"final_output": "quiz body"}}

    monkeypatch.setattr("git_study.services.quiz_service.quiz_graph", StubGraph())

    events = list(stream_quiz_progress({"messages": [{"role": "user", "content": "hello"}]}))

    assert captured["stream_mode"] == "updates"
    assert [event["node"] for event in events if event["type"] == "node"] == [
        "resolve_commit_context",
        "analyze_change",
        "finalize_quiz",
    ]
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["messages"][0].content == "quiz body"


def test_stream_quiz_progress_keeps_repeated_review_and_repair_nodes(monkeypatch) -> None:
    class StubGraph:
        def stream(self, payload, config=None, stream_mode=None):
            yield {"draft_quiz": {"quiz_questions": []}}
            yield {"review_quiz": {"quiz_review": {"is_valid": False}}}
            yield {"repair_quiz": {"quiz_questions": []}}
            yield {"review_quiz": {"quiz_review": {"is_valid": True}}}
            yield {"finalize_quiz": {"final_output": "quiz body"}}

    monkeypatch.setattr("git_study.services.quiz_service.quiz_graph", StubGraph())

    events = list(stream_quiz_progress({"messages": [{"role": "user", "content": "hello"}]}))

    assert [event["node"] for event in events if event["type"] == "node"] == [
        "draft_quiz",
        "review_quiz",
        "repair_quiz",
        "review_quiz",
        "finalize_quiz",
    ]


def test_run_read_wraps_final_output_into_messages(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            return {"final_output": "reading body", "extra": "value"}

    monkeypatch.setattr("git_study.services.read_service.read_graph", StubGraph())

    result = run_read({"messages": [{"role": "user", "content": "hello"}]})

    assert captured["config"] == {"configurable": {"thread_id": "textual-tui-session"}}
    assert result["messages"][0].content == "reading body"
    assert result["extra"] == "value"


def test_stream_read_progress_yields_node_events_and_result(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def stream(self, payload, config=None, stream_mode=None):
            captured["payload"] = payload
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            yield {"resolve_commit_context": {"commit_sha": "abc"}}
            yield {"draft_reading": {"reading_draft": "reading body"}}
            yield {"finalize_reading": {"final_output": "reading body"}}

    monkeypatch.setattr("git_study.services.read_service.read_graph", StubGraph())

    events = list(stream_read_progress({"messages": [{"role": "user", "content": "hello"}]}))

    assert captured["stream_mode"] == "updates"
    assert [event["node"] for event in events if event["type"] == "node"] == [
        "resolve_commit_context",
        "draft_reading",
        "finalize_reading",
    ]
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["messages"][0].content == "reading body"


def test_stream_read_progress_keeps_repeated_review_and_repair_nodes(monkeypatch) -> None:
    class StubGraph:
        def stream(self, payload, config=None, stream_mode=None):
            yield {"draft_reading": {"reading_draft": "body"}}
            yield {"review_reading": {"reading_review": {"is_valid": False}}}
            yield {"repair_reading": {"reading_draft": "body 2"}}
            yield {"review_reading": {"reading_review": {"is_valid": True}}}
            yield {"finalize_reading": {"final_output": "reading body"}}

    monkeypatch.setattr("git_study.services.read_service.read_graph", StubGraph())

    events = list(stream_read_progress({"messages": [{"role": "user", "content": "hello"}]}))

    assert [event["node"] for event in events if event["type"] == "node"] == [
        "draft_reading",
        "review_reading",
        "repair_reading",
        "review_reading",
        "finalize_reading",
    ]


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

    assert captured["payload"] == {
        "commit_context": {"commit_sha": "abc"},
        "count": 3,
        "user_request": "",
    }
    assert result == [{"id": "q1"}]


def test_stream_inline_quiz_progress_yields_node_events_and_result(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def stream(self, payload, stream_mode=None):
            captured["payload"] = payload
            captured["stream_mode"] = stream_mode
            yield {"prepare_inline_context": {"actual_paths": ["src/a.py"]}}
            yield {"generate_inline_questions": {"inline_questions": [{"id": "q1"}]}}

    monkeypatch.setattr(
        "git_study.services.inline_quiz_service.inline_quiz_graph", StubGraph()
    )

    events = list(stream_inline_quiz_progress({"commit_sha": "abc"}, count=2))

    assert captured["payload"] == {
        "commit_context": {"commit_sha": "abc"},
        "count": 2,
        "user_request": "",
    }
    assert captured["stream_mode"] == "updates"
    assert [event["node"] for event in events if event["type"] == "node"] == [
        "prepare_inline_context",
        "generate_inline_questions",
    ]
    assert events[-1] == {
        "type": "result",
        "result": {"actual_paths": ["src/a.py"], "inline_questions": [{"id": "q1"}]},
    }


def test_stream_inline_quiz_progress_keeps_repeated_review_and_repair_nodes(monkeypatch) -> None:
    class StubGraph:
        def stream(self, payload, stream_mode=None):
            yield {"generate_inline_questions": {"inline_questions": [{"id": "q1"}]}}
            yield {"review_inline_questions": {"inline_review": {"is_valid": False}}}
            yield {"repair_inline_questions": {"inline_questions": [{"id": "q1"}]}}
            yield {"review_inline_questions": {"inline_review": {"is_valid": True}}}
            yield {"finalize_inline_questions": {"inline_questions": [{"id": "q1"}]}}

    monkeypatch.setattr(
        "git_study.services.inline_quiz_service.inline_quiz_graph", StubGraph()
    )

    events = list(stream_inline_quiz_progress({"commit_sha": "abc"}))

    assert [event["node"] for event in events if event["type"] == "node"] == [
        "generate_inline_questions",
        "review_inline_questions",
        "repair_inline_questions",
        "review_inline_questions",
        "finalize_inline_questions",
    ]


def test_generate_inline_quiz_grades_passes_questions_and_answers(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload):
            captured["payload"] = payload
            return {
                "final_grades": [{"id": "q1", "score": 90, "feedback": "좋음"}],
                "grading_summary": {"weak_points": ["의도 설명 보강"]},
            }

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

    assert captured["payload"] == {
        "questions": questions,
        "answers": answers,
        "user_request": "",
    }
    assert result == [{"id": "q1", "score": 90, "feedback": "좋음"}]


def test_stream_inline_grade_progress_yields_node_events_and_result(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def stream(self, payload, stream_mode=None):
            captured["payload"] = payload
            captured["stream_mode"] = stream_mode
            yield {"prepare_grading_payload": {"question_blocks": "..."}}
            yield {"finalize_grades": {"final_grades": [{"id": "q1", "score": 77}]}}

    monkeypatch.setattr(
        "git_study.services.inline_grade_service.inline_grade_graph", StubGraph()
    )

    questions = [{"id": "q1"}]
    answers = {"q1": "답"}
    events = list(stream_inline_grade_progress(questions, answers))

    assert captured["payload"] == {
        "questions": questions,
        "answers": answers,
        "user_request": "",
    }
    assert captured["stream_mode"] == "updates"
    assert [event["node"] for event in events if event["type"] == "node"] == [
        "prepare_grading_payload",
        "finalize_grades",
    ]
    assert events[-1] == {
        "type": "result",
        "result": {"question_blocks": "...", "final_grades": [{"id": "q1", "score": 77}]},
    }


def test_generate_general_quiz_grades_passes_questions_and_answers(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload):
            captured["payload"] = payload
            return {
                "final_grades": [{"id": "q1", "score": 88, "feedback": "좋음"}],
                "grading_summary": {"next_steps": ["다시 풀기"]},
            }

    monkeypatch.setattr(
        "git_study.services.general_grade_service.general_grade_graph", StubGraph()
    )

    questions = [
        {
            "id": "q1",
            "question": "질문",
            "expected_answer": "답",
            "question_type": "intent",
            "explanation": "해설",
        }
    ]
    answers = {"q1": "사용자 답변"}

    result = generate_general_quiz_grades(questions, answers)

    assert captured["payload"] == {
        "questions": questions,
        "answers": answers,
        "user_request": "",
    }
    assert result == [{"id": "q1", "score": 88, "feedback": "좋음"}]


def test_stream_general_grade_progress_yields_node_events_and_result(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def stream(self, payload, stream_mode=None):
            captured["payload"] = payload
            captured["stream_mode"] = stream_mode
            yield {"prepare_grading_payload": {"question_blocks": "..."}}
            yield {"finalize_grades": {"final_grades": [{"id": "q1", "score": 81}]}}

    monkeypatch.setattr(
        "git_study.services.general_grade_service.general_grade_graph", StubGraph()
    )

    questions = [{"id": "q1", "question": "질문"}]
    answers = {"q1": "답"}
    events = list(stream_general_grade_progress(questions, answers))

    assert captured["payload"] == {
        "questions": questions,
        "answers": answers,
        "user_request": "",
    }
    assert captured["stream_mode"] == "updates"
    assert [event["node"] for event in events if event["type"] == "node"] == [
        "prepare_grading_payload",
        "finalize_grades",
    ]
    assert events[-1] == {
        "type": "result",
        "result": {"question_blocks": "...", "final_grades": [{"id": "q1", "score": 81}]},
    }


def test_generate_general_quiz_grades_passes_custom_user_request(monkeypatch) -> None:
    captured: dict = {}

    class StubGraph:
        def invoke(self, payload):
            captured["payload"] = payload
            return {"final_grades": []}

    monkeypatch.setattr(
        "git_study.services.general_grade_service.general_grade_graph", StubGraph()
    )

    generate_general_quiz_grades(
        [{"id": "q1", "question": "질문"}],
        {"q1": "답"},
        user_request="AGENTS.md는 빼줘",
    )

    assert captured["payload"]["user_request"] == "AGENTS.md는 빼줘"

from pathlib import Path

from git_study.tui.session_state import (
    complete_general_quiz_grading,
    complete_inline_quiz_grading,
    create_learning_session,
    make_session_id,
    mark_read_completed,
    rebuild_review_summary,
    regenerate_general_quiz,
    regenerate_inline_quiz,
    regenerate_read_step,
    retry_general_quiz,
    save_general_quiz_rendered_result,
    save_general_quiz_answer,
    save_inline_quiz_answer,
    save_learning_session,
    load_learning_session,
)
from git_study.tui.state import get_learning_session_path


def _base_session(now: str = "2026-03-27T12:00:00+0900") -> dict:
    return create_learning_session(
        session_id="range-aaa-bbb",
        repo={
            "source": "local",
            "github_repo_url": "",
            "repository_label": "/tmp/repo",
        },
        selection={
            "mode": "range",
            "commit_mode": "selected",
            "highlighted_commit_sha": "aaa",
            "start_sha": "aaa",
            "end_sha": "bbb",
            "selected_shas": ["aaa", "bbb"],
            "range_summary": "aaa ~ bbb (2 commits)",
        },
        preferences={
            "difficulty": "medium",
            "quiz_style": "mixed",
            "request_text": "최근 커밋 기반으로 퀴즈 만들어줘",
        },
        now=now,
    )


def test_make_session_id_uses_single_or_range() -> None:
    assert make_session_id(["abc123"]) == "single-abc123"
    assert make_session_id(["abc123", "def456", "ghi789"]) == "range-abc123-ghi789"


def test_regenerate_and_retry_general_quiz_clear_attempts() -> None:
    session = _base_session()

    regenerate_general_quiz(
        session,
        questions=[{"id": "q1", "question": "왜?", "expected_answer": "의도", "question_type": "intent"}],
        rendered_markdown="# Quiz",
        now="2026-03-27T12:10:00+0900",
    )
    save_general_quiz_answer(
        session,
        question_id="q1",
        answer="사용자 답변",
        current_index=0,
        now="2026-03-27T12:11:00+0900",
    )

    assert session["general_quiz"]["status"] == "in_progress"
    assert session["general_quiz"]["attempt"]["answers"] == {"q1": "사용자 답변"}

    retry_general_quiz(session, now="2026-03-27T12:12:00+0900")

    assert session["general_quiz"]["version"] == 1
    assert session["general_quiz"]["status"] == "ready"
    assert session["general_quiz"]["attempt"]["answers"] == {}
    assert session["general_quiz"]["attempt"]["grades"] == []


def test_save_general_quiz_rendered_result_updates_general_step() -> None:
    session = _base_session()

    save_general_quiz_rendered_result(
        session,
        rendered_markdown="# Quiz\n\nbody",
        questions=[{"id": "q1", "question": "질문", "expected_answer": "답", "question_type": "intent"}],
        now="2026-03-27T12:09:00+0900",
    )

    assert session["general_quiz"]["version"] == 1
    assert session["general_quiz"]["status"] == "ready"
    assert session["general_quiz"]["rendered_markdown"] == "# Quiz\n\nbody"
    assert session["general_quiz"]["questions"][0]["id"] == "q1"
    assert session["session_meta"]["current_step"] == "general_quiz"


def test_saving_same_answer_after_general_quiz_grading_preserves_graded_status() -> None:
    session = _base_session()

    regenerate_general_quiz(
        session,
        questions=[{"id": "q1", "question": "왜?", "expected_answer": "의도", "question_type": "intent"}],
        rendered_markdown="# Quiz",
        now="2026-03-27T12:07:00+0900",
    )
    save_general_quiz_answer(
        session,
        question_id="q1",
        answer="사용자 답변",
        current_index=0,
        now="2026-03-27T12:08:00+0900",
    )
    complete_general_quiz_grading(
        session,
        grades=[{"id": "q1", "score": 80, "feedback": "좋아요"}],
        score_summary={"total": 80, "max": 100},
        now="2026-03-27T12:09:00+0900",
    )

    save_general_quiz_answer(
        session,
        question_id="q1",
        answer="사용자 답변",
        current_index=0,
        now="2026-03-27T12:10:00+0900",
    )

    assert session["general_quiz"]["status"] == "graded"
    assert session["general_quiz"]["attempt"]["score_summary"] == {"total": 80, "max": 100}


def test_rebuild_review_marks_session_completed_when_all_steps_graded() -> None:
    session = _base_session()
    regenerate_read_step(
        session,
        content="읽을거리",
        summary={
            "key_files": ["src/a.py"],
            "learning_objectives": ["흐름 이해"],
            "change_risks": ["회귀 위험"],
        },
        now="2026-03-27T12:05:00+0900",
    )
    mark_read_completed(session, now="2026-03-27T12:06:00+0900")
    regenerate_general_quiz(
        session,
        questions=[{"id": "q1", "question": "왜?", "expected_answer": "의도", "question_type": "intent"}],
        rendered_markdown="# Quiz",
        now="2026-03-27T12:07:00+0900",
    )
    complete_general_quiz_grading(
        session,
        grades=[{"id": "q1", "score": 80, "feedback": "좋아요"}],
        score_summary={"total": 80, "max": 100},
        now="2026-03-27T12:08:00+0900",
    )
    regenerate_inline_quiz(
        session,
        questions=[
            {
                "id": "q1",
                "file_path": "src/a.py",
                "anchor_snippet": "run()",
                "question": "역할은?",
                "expected_answer": "핵심 흐름",
                "question_type": "intent",
            }
        ],
        known_files={"src/a.py": "def run():\n    return 1\n"},
        now="2026-03-27T12:09:00+0900",
    )
    save_inline_quiz_answer(
        session,
        question_id="q1",
        answer="핵심 흐름입니다.",
        current_index=0,
        now="2026-03-27T12:10:00+0900",
    )
    complete_inline_quiz_grading(
        session,
        grades=[{"id": "q1", "score": 90, "feedback": "정확합니다."}],
        score_summary={"total": 90, "max": 100},
        now="2026-03-27T12:11:00+0900",
    )

    rebuild_review_summary(
        session,
        weak_points=["예외 처리"],
        weak_files=["src/a.py"],
        recommended_next_step="인라인 퀴즈 다시 풀기",
        now="2026-03-27T12:12:00+0900",
    )

    assert session["review"]["available"] is True
    assert session["review"]["summary"]["general_quiz_score"] == 80
    assert session["review"]["summary"]["inline_quiz_score"] == 90
    assert session["session_meta"]["status"] == "completed"
    assert session["session_meta"]["current_step"] == "review"


def test_rebuild_review_treats_ready_read_as_completed_for_summary() -> None:
    session = _base_session()
    regenerate_read_step(
        session,
        content="읽을거리",
        summary={
            "key_files": ["src/a.py"],
            "learning_objectives": ["흐름 이해"],
            "change_risks": ["회귀 위험"],
        },
        now="2026-03-27T12:05:00+0900",
    )

    rebuild_review_summary(
        session,
        weak_points=[],
        weak_files=[],
        recommended_next_step="Basic Quiz를 진행하세요.",
        now="2026-03-27T12:06:00+0900",
    )

    assert session["review"]["summary"]["read_completed"] is True


def test_save_and_load_learning_session_follow_local_runtime_policy(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    session = _base_session()

    save_learning_session(
        session,
        repo_source="local",
        local_repo_root=str(repo_root),
    )

    saved_path = get_learning_session_path(
        session["session_id"],
        repo_source="local",
        local_repo_root=repo_root,
    )
    assert saved_path == repo_root / ".git-study" / "sessions" / "local" / "range-aaa-bbb" / "session.json"

    loaded = load_learning_session(
        session["session_id"],
        repo_source="local",
        local_repo_root=str(repo_root),
    )

    assert loaded is not None
    assert loaded["session_id"] == "range-aaa-bbb"

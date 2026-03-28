from types import SimpleNamespace

from git_study.tui.app import GitStudyApp
from git_study.tui.inline_quiz import InlineQuizWidget


def test_load_current_learning_session_returns_loaded_session_without_creating(
    monkeypatch,
) -> None:
    app = object.__new__(GitStudyApp)
    target = {
        "session_id": "single-abc123",
        "repo_source": "local",
        "github_repo_url": "",
        "local_repo_root": "/tmp/repo",
    }
    loaded_session = {"session_meta": {"status": "idle"}}

    monkeypatch.setattr(app, "_capture_session_target", lambda: target)

    def fake_load(session_id, repo_source, github_repo_url, local_repo_root):
        assert session_id == "single-abc123"
        assert repo_source == "local"
        assert github_repo_url == ""
        assert local_repo_root == "/tmp/repo"
        return loaded_session

    def fake_create(*args, **kwargs):
        raise AssertionError("load-only helper must not create a session")

    monkeypatch.setattr("git_study.tui.app.load_learning_session", fake_load)
    monkeypatch.setattr("git_study.tui.app.create_learning_session", fake_create)

    assert app._load_current_learning_session() is loaded_session


def test_load_current_learning_session_does_not_create_missing_session(monkeypatch) -> None:
    app = object.__new__(GitStudyApp)
    target = {
        "session_id": "single-missing",
        "repo_source": "local",
        "github_repo_url": "",
        "local_repo_root": "/tmp/repo",
    }

    monkeypatch.setattr(app, "_capture_session_target", lambda: target)
    monkeypatch.setattr(
        "git_study.tui.app.load_learning_session",
        lambda *args, **kwargs: None,
    )

    def fake_create(*args, **kwargs):
        raise AssertionError("load-only helper must not create a session")

    monkeypatch.setattr("git_study.tui.app.create_learning_session", fake_create)

    assert app._load_current_learning_session() is None


def test_save_inline_quiz_state_skips_when_cache_key_target_is_missing(monkeypatch) -> None:
    app = object.__new__(GitStudyApp)
    app._inline_session_targets = {}
    app._update_top_toggle_buttons = lambda: None

    called = False

    def fake_save(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(app, "_save_inline_quiz_state_for_selection", fake_save)

    app.save_inline_quiz_state("missing-cache-key", {"questions": [], "answers": {}, "grades": [], "current_index": 0, "known_files": {}})

    assert called is False


def test_current_quiz_generation_in_progress_is_tracked_per_session() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_quiz_targets = {
        "single-old": {"session_id": "single-old"},
        "single-current": {"session_id": "single-current"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_quiz_generation_in_progress() is True


def test_current_quiz_generation_in_progress_ignores_other_sessions() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_quiz_targets = {
        "single-old": {"session_id": "single-old"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_quiz_generation_in_progress() is False


def test_current_read_generation_in_progress_is_tracked_per_session() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_read_targets = {
        "single-old": {"session_id": "single-old"},
        "single-current": {"session_id": "single-current"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_read_generation_in_progress() is True


def test_current_read_generation_in_progress_ignores_other_sessions() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_read_targets = {
        "single-old": {"session_id": "single-old"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_read_generation_in_progress() is False


def test_current_inline_generation_in_progress_is_tracked_per_session() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_inline_targets = {
        "single-old": {"session_id": "single-old"},
        "single-current": {"session_id": "single-current"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_inline_generation_in_progress() is True


def test_current_inline_generation_in_progress_ignores_other_sessions() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_inline_targets = {
        "single-old": {"session_id": "single-old"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_inline_generation_in_progress() is False


def test_current_inline_grading_in_progress_is_tracked_per_session() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_inline_grade_targets = {
        "single-old": {"session_id": "single-old"},
        "single-current": {"session_id": "single-current"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_inline_grading_in_progress() is True


def test_current_inline_grading_in_progress_ignores_other_sessions() -> None:
    app = object.__new__(GitStudyApp)
    app._pending_inline_grade_targets = {
        "single-old": {"session_id": "single-old"},
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_inline_grading_in_progress() is False


def test_current_quiz_progress_and_error_are_scoped_to_current_session() -> None:
    app = object.__new__(GitStudyApp)
    app._quiz_progress_labels = {
        "single-old": "변경 분석",
        "single-current": "품질 검토",
    }
    app._quiz_error_messages = {
        "single-old": "old error",
        "single-current": "current error",
    }
    app._current_learning_session_id = lambda: "single-current"

    assert app._current_quiz_progress_label() == "품질 검토"
    assert app._current_quiz_error_message() == "current error"


def test_inline_stale_grade_result_is_persisted_before_ui_is_ignored() -> None:
    saved_calls: list[tuple[str, dict, dict | None]] = []
    finished_calls: list[str] = []

    def fake_save(cache_key: str, state: dict, grading_summary: dict | None = None) -> None:
        saved_calls.append((cache_key, state, grading_summary))

    def fake_notify(cache_key: str) -> None:
        finished_calls.append(cache_key)

    class DummyInlineQuizWidget(InlineQuizWidget):
        @property
        def app(self):
            return self._fake_app

    widget = object.__new__(DummyInlineQuizWidget)
    widget._fake_app = SimpleNamespace(
        save_inline_quiz_state=fake_save,
        notify_inline_grade_finished=fake_notify,
    )
    widget._session_serial = 2

    saved_state = {
        "questions": [{"id": "q1"}],
        "answers": {"q1": "answer"},
        "grades": [],
        "current_index": 0,
        "known_files": {"a.py": "print('x')"},
    }
    grades = [{"id": "q1", "score": 80, "feedback": "good"}]
    grading_summary = {"weak_points": ["foo"]}

    widget._apply_grades_loaded_if_current(
        1,
        "single-old",
        saved_state,
        grades,
        grading_summary,
    )

    assert saved_calls == [
        (
            "single-old",
            {
                "questions": [{"id": "q1"}],
                "answers": {"q1": "answer"},
                "grades": [{"id": "q1", "score": 80, "feedback": "good"}],
                "current_index": 0,
                "known_files": {"a.py": "print('x')"},
            },
            {"weak_points": ["foo"]},
        )
    ]
    assert finished_calls == ["single-old"]

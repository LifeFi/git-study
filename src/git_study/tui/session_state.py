import time
from typing import Literal, NotRequired, TypedDict

from ..types import (
    GeneralQuizGrade,
    GeneralQuizQuestion,
    GradingSummary,
    InlineQuizGrade,
    InlineQuizQuestion,
)
from .state import load_learning_session_file, save_learning_session_file


SessionStep = Literal["read", "general_quiz", "inline_quiz", "review"]
StepStatus = Literal["not_started", "ready", "in_progress", "completed", "graded"]
SelectionMode = Literal["single", "range"]
RepoSource = Literal["local", "github"]


class SessionRepoInfo(TypedDict):
    source: RepoSource
    github_repo_url: str
    repository_label: str


class SessionSelection(TypedDict):
    mode: SelectionMode
    commit_mode: Literal["auto", "latest", "selected"]
    highlighted_commit_sha: str
    start_sha: str
    end_sha: str
    selected_shas: list[str]
    range_summary: str


class SessionMeta(TypedDict):
    created_at: str
    updated_at: str
    current_step: SessionStep
    status: Literal["in_progress", "completed"]


class SessionPreferences(TypedDict):
    difficulty: str
    quiz_style: str
    request_text: str
    read_request_text: str
    basic_request_text: str
    inline_request_text: str
    grading_request_text: str


class ReadSummary(TypedDict):
    key_files: list[str]
    learning_objectives: list[str]
    change_risks: list[str]


class ReadStepState(TypedDict):
    version: int
    status: Literal["not_started", "ready", "completed"]
    content: str
    summary: ReadSummary
    completed_at: str | None
    updated_at: str


class QuizScoreSummary(TypedDict):
    total: int
    max: int


class GeneralQuizAttempt(TypedDict):
    answers: dict[str, str]
    submitted_at: str | None
    grades: list[GeneralQuizGrade]
    score_summary: QuizScoreSummary | None


class GeneralQuizStepState(TypedDict):
    version: int
    status: StepStatus
    questions: list[GeneralQuizQuestion]
    rendered_markdown: str
    attempt: GeneralQuizAttempt
    grading_summary: GradingSummary | None
    current_index: int
    updated_at: str


class InlineQuizAttempt(TypedDict):
    answers: dict[str, str]
    submitted_at: str | None
    grades: list[InlineQuizGrade]
    score_summary: QuizScoreSummary | None


class InlineQuizStepState(TypedDict):
    version: int
    status: StepStatus
    questions: list[InlineQuizQuestion]
    attempt: InlineQuizAttempt
    grading_summary: GradingSummary | None
    current_index: int
    known_files: dict[str, str]
    updated_at: str


class ReviewSummary(TypedDict, total=False):
    read_completed: bool
    general_quiz_score: int
    inline_quiz_score: int
    weak_points: list[str]
    weak_files: list[str]
    recommended_next_step: str


class ReviewState(TypedDict):
    available: bool
    summary: ReviewSummary | None
    updated_at: str | None


class LearningSession(TypedDict):
    schema_version: int
    session_id: str
    repo: SessionRepoInfo
    selection: SessionSelection
    session_meta: SessionMeta
    preferences: SessionPreferences
    read: ReadStepState
    general_quiz: GeneralQuizStepState
    inline_quiz: InlineQuizStepState
    review: ReviewState


def now_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def make_session_id(selected_shas: list[str]) -> str:
    cleaned = [sha.strip() for sha in selected_shas if sha and sha.strip()]
    if not cleaned:
        raise ValueError("selected_shas must not be empty")
    if len(cleaned) == 1:
        return f"single-{cleaned[0]}"
    return f"range-{cleaned[0]}-{cleaned[-1]}"


def empty_read_step(now: str) -> ReadStepState:
    return {
        "version": 0,
        "status": "not_started",
        "content": "",
        "summary": {
            "key_files": [],
            "learning_objectives": [],
            "change_risks": [],
        },
        "completed_at": None,
        "updated_at": now,
    }


def empty_general_quiz_step(now: str) -> GeneralQuizStepState:
    return {
        "version": 0,
        "status": "not_started",
        "questions": [],
        "rendered_markdown": "",
        "attempt": {
            "answers": {},
            "submitted_at": None,
            "grades": [],
            "score_summary": None,
        },
        "grading_summary": None,
        "current_index": 0,
        "updated_at": now,
    }


def empty_inline_quiz_step(now: str) -> InlineQuizStepState:
    return {
        "version": 0,
        "status": "not_started",
        "questions": [],
        "attempt": {
            "answers": {},
            "submitted_at": None,
            "grades": [],
            "score_summary": None,
        },
        "grading_summary": None,
        "current_index": 0,
        "known_files": {},
        "updated_at": now,
    }


def empty_review_state() -> ReviewState:
    return {
        "available": False,
        "summary": None,
        "updated_at": None,
    }


def create_learning_session(
    *,
    session_id: str,
    repo: SessionRepoInfo,
    selection: SessionSelection,
    preferences: SessionPreferences,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    return {
        "schema_version": 1,
        "session_id": session_id,
        "repo": repo,
        "selection": selection,
        "session_meta": {
            "created_at": stamp,
            "updated_at": stamp,
            "current_step": "read",
            "status": "in_progress",
        },
        "preferences": preferences,
        "read": empty_read_step(stamp),
        "general_quiz": empty_general_quiz_step(stamp),
        "inline_quiz": empty_inline_quiz_step(stamp),
        "review": empty_review_state(),
    }


def _touch_session(session: LearningSession, now: str) -> None:
    session["session_meta"]["updated_at"] = now


def _invalidate_review(session: LearningSession) -> None:
    session["review"] = empty_review_state()


def reset_entire_session(session: LearningSession, now: str | None = None) -> LearningSession:
    stamp = now or now_timestamp()
    session["session_meta"]["current_step"] = "read"
    session["session_meta"]["status"] = "in_progress"
    session["read"] = empty_read_step(stamp)
    session["general_quiz"] = empty_general_quiz_step(stamp)
    session["inline_quiz"] = empty_inline_quiz_step(stamp)
    session["review"] = empty_review_state()
    _touch_session(session, stamp)
    return session


def regenerate_read_step(
    session: LearningSession,
    *,
    content: str,
    summary: ReadSummary | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["read"]["version"] += 1
    session["read"]["status"] = "ready"
    session["read"]["content"] = content
    session["read"]["summary"] = summary or {
        "key_files": [],
        "learning_objectives": [],
        "change_risks": [],
    }
    session["read"]["completed_at"] = None
    session["read"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "read"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def mark_read_completed(
    session: LearningSession,
    *,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["read"]["status"] = "completed"
    session["read"]["completed_at"] = stamp
    session["read"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "general_quiz"
    _touch_session(session, stamp)
    return session


def regenerate_general_quiz(
    session: LearningSession,
    *,
    questions: list[GeneralQuizQuestion],
    rendered_markdown: str,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["general_quiz"]["version"] += 1
    session["general_quiz"]["status"] = "ready"
    session["general_quiz"]["questions"] = questions
    session["general_quiz"]["rendered_markdown"] = rendered_markdown
    session["general_quiz"]["attempt"] = {
        "answers": {},
        "submitted_at": None,
        "grades": [],
        "score_summary": None,
    }
    session["general_quiz"]["grading_summary"] = None
    session["general_quiz"]["current_index"] = 0
    session["general_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "general_quiz"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def save_general_quiz_rendered_result(
    session: LearningSession,
    *,
    rendered_markdown: str,
    questions: list[GeneralQuizQuestion] | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["general_quiz"]["version"] += 1
    session["general_quiz"]["status"] = "ready"
    if questions is not None:
        session["general_quiz"]["questions"] = questions
    session["general_quiz"]["rendered_markdown"] = rendered_markdown
    session["general_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "general_quiz"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def retry_general_quiz(
    session: LearningSession,
    *,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["general_quiz"]["attempt"] = {
        "answers": {},
        "submitted_at": None,
        "grades": [],
        "score_summary": None,
    }
    session["general_quiz"]["grading_summary"] = None
    session["general_quiz"]["current_index"] = 0
    session["general_quiz"]["status"] = (
        "ready" if session["general_quiz"]["questions"] else "not_started"
    )
    session["general_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "general_quiz"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def save_general_quiz_answer(
    session: LearningSession,
    *,
    question_id: str,
    answer: str,
    current_index: int | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    previous_answer = session["general_quiz"]["attempt"]["answers"].get(question_id, "")
    was_graded = session["general_quiz"]["status"] == "graded"
    session["general_quiz"]["attempt"]["answers"][question_id] = answer
    if current_index is not None:
        session["general_quiz"]["current_index"] = current_index
    if was_graded and answer == previous_answer:
        session["general_quiz"]["status"] = "graded"
    else:
        if was_graded:
            session["general_quiz"]["attempt"]["submitted_at"] = None
            session["general_quiz"]["attempt"]["grades"] = []
            session["general_quiz"]["attempt"]["score_summary"] = None
            session["general_quiz"]["grading_summary"] = None
            _invalidate_review(session)
        session["general_quiz"]["status"] = "in_progress"
    session["general_quiz"]["updated_at"] = stamp
    _touch_session(session, stamp)
    return session


def complete_general_quiz_grading(
    session: LearningSession,
    *,
    grades: list[GeneralQuizGrade],
    score_summary: QuizScoreSummary | None,
    grading_summary: GradingSummary | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["general_quiz"]["attempt"]["submitted_at"] = stamp
    session["general_quiz"]["attempt"]["grades"] = grades
    session["general_quiz"]["attempt"]["score_summary"] = score_summary
    session["general_quiz"]["grading_summary"] = grading_summary
    session["general_quiz"]["status"] = "graded"
    session["general_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "inline_quiz"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def regenerate_inline_quiz(
    session: LearningSession,
    *,
    questions: list[InlineQuizQuestion],
    known_files: dict[str, str] | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["inline_quiz"]["version"] += 1
    session["inline_quiz"]["status"] = "ready"
    session["inline_quiz"]["questions"] = questions
    session["inline_quiz"]["attempt"] = {
        "answers": {},
        "submitted_at": None,
        "grades": [],
        "score_summary": None,
    }
    session["inline_quiz"]["grading_summary"] = None
    session["inline_quiz"]["current_index"] = 0
    session["inline_quiz"]["known_files"] = known_files or {}
    session["inline_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "inline_quiz"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def retry_inline_quiz(
    session: LearningSession,
    *,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["inline_quiz"]["attempt"] = {
        "answers": {},
        "submitted_at": None,
        "grades": [],
        "score_summary": None,
    }
    session["inline_quiz"]["grading_summary"] = None
    session["inline_quiz"]["current_index"] = 0
    session["inline_quiz"]["status"] = (
        "ready" if session["inline_quiz"]["questions"] else "not_started"
    )
    session["inline_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "inline_quiz"
    _invalidate_review(session)
    _touch_session(session, stamp)
    return session


def save_inline_quiz_answer(
    session: LearningSession,
    *,
    question_id: str,
    answer: str,
    current_index: int | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    previous_answer = session["inline_quiz"]["attempt"]["answers"].get(question_id, "")
    was_graded = session["inline_quiz"]["status"] == "graded"
    session["inline_quiz"]["attempt"]["answers"][question_id] = answer
    if current_index is not None:
        session["inline_quiz"]["current_index"] = current_index
    if was_graded and answer == previous_answer:
        session["inline_quiz"]["status"] = "graded"
    else:
        if was_graded:
            session["inline_quiz"]["attempt"]["submitted_at"] = None
            session["inline_quiz"]["attempt"]["grades"] = []
            session["inline_quiz"]["attempt"]["score_summary"] = None
            session["inline_quiz"]["grading_summary"] = None
            _invalidate_review(session)
        session["inline_quiz"]["status"] = "in_progress"
    session["inline_quiz"]["updated_at"] = stamp
    _touch_session(session, stamp)
    return session


def complete_inline_quiz_grading(
    session: LearningSession,
    *,
    grades: list[InlineQuizGrade],
    score_summary: QuizScoreSummary | None,
    grading_summary: GradingSummary | None = None,
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    session["inline_quiz"]["attempt"]["submitted_at"] = stamp
    session["inline_quiz"]["attempt"]["grades"] = grades
    session["inline_quiz"]["attempt"]["score_summary"] = score_summary
    session["inline_quiz"]["grading_summary"] = grading_summary
    session["inline_quiz"]["status"] = "graded"
    session["inline_quiz"]["updated_at"] = stamp
    session["session_meta"]["current_step"] = "review"
    _touch_session(session, stamp)
    return session


def rebuild_review_summary(
    session: LearningSession,
    *,
    weak_points: list[str] | None = None,
    weak_files: list[str] | None = None,
    recommended_next_step: str = "",
    now: str | None = None,
) -> LearningSession:
    stamp = now or now_timestamp()
    general_score = session["general_quiz"]["attempt"]["score_summary"]
    inline_score = session["inline_quiz"]["attempt"]["score_summary"]
    read_completed = session["read"]["status"] in {"ready", "completed"}
    session["review"] = {
        "available": True,
        "summary": {
            "read_completed": read_completed,
            "general_quiz_score": general_score["total"] if general_score else 0,
            "inline_quiz_score": inline_score["total"] if inline_score else 0,
            "weak_points": weak_points or [],
            "weak_files": weak_files or [],
            "recommended_next_step": recommended_next_step,
        },
        "updated_at": stamp,
    }
    session["session_meta"]["current_step"] = "review"
    if (
        read_completed
        and session["general_quiz"]["status"] == "graded"
        and session["inline_quiz"]["status"] == "graded"
    ):
        session["session_meta"]["status"] = "completed"
    _touch_session(session, stamp)
    return session


def load_learning_session(
    session_id: str,
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: str | None = None,
) -> LearningSession | None:
    payload = load_learning_session_file(
        session_id,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        local_repo_root=local_repo_root,
    )
    return payload if payload is not None else None


def save_learning_session(
    session: LearningSession,
    *,
    repo_source: str,
    github_repo_url: str = "",
    local_repo_root: str | None = None,
) -> None:
    save_learning_session_file(
        session["session_id"],
        session,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        local_repo_root=local_repo_root,
    )

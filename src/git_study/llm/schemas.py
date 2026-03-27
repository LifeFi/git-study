from typing import Any, TypedDict


QUESTION_TYPES = ("intent", "behavior", "tradeoff", "vulnerability")
QUESTION_PLAN_TYPES = (
    "intent",
    "code_reading",
    "behavior_change",
    "risk_tradeoff",
)


class QuizAnalysisSnippet(TypedDict):
    path: str
    code: str
    reason: str


class QuizQuestionPlanItem(TypedDict):
    type: str
    focus: str
    path: str
    code_hint: str


class QuizAnalysis(TypedDict):
    summary_bullets: list[str]
    key_files: list[str]
    key_snippets: list[QuizAnalysisSnippet]
    learning_objectives: list[str]
    question_plan: list[QuizQuestionPlanItem]
    change_risks: list[str]


class QuizReviewResult(TypedDict):
    is_valid: bool
    issues: list[str]
    revision_instruction: str


class InlineGradeReviewResult(TypedDict):
    is_valid: bool
    issues: list[str]
    revision_instruction: str
    normalized_grades: list["InlineGradePayload"]


class InlineAnchorCandidate(TypedDict):
    file_path: str
    anchor_snippet: str
    question_type: str
    reason: str


class InlineQuestionPayload(TypedDict):
    id: str
    file_path: str
    anchor_snippet: str
    question: str
    expected_answer: str
    question_type: str


class InlineGradePayload(TypedDict):
    id: str
    score: int
    feedback: str


def _string_list(value: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit] if limit is not None else items


def normalize_quiz_analysis(payload: Any) -> QuizAnalysis:
    data = payload if isinstance(payload, dict) else {}
    summary_bullets = _string_list(data.get("summary_bullets"), limit=5)
    key_files = _string_list(data.get("key_files"), limit=5)
    learning_objectives = _string_list(data.get("learning_objectives"), limit=6)
    change_risks = _string_list(data.get("change_risks"), limit=6)

    raw_snippets = data.get("key_snippets")
    key_snippets: list[QuizAnalysisSnippet] = []
    if isinstance(raw_snippets, list):
        for item in raw_snippets:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            code = str(item.get("code", "")).strip()
            reason = str(item.get("reason", "")).strip()
            if not (path or code or reason):
                continue
            key_snippets.append({"path": path, "code": code, "reason": reason})
            if len(key_snippets) >= 4:
                break

    raw_plan = data.get("question_plan")
    question_plan: list[QuizQuestionPlanItem] = []
    seen_types: set[str] = set()
    if isinstance(raw_plan, list):
        for item in raw_plan:
            if not isinstance(item, dict):
                continue
            plan_type = str(item.get("type", "")).strip()
            if plan_type not in QUESTION_PLAN_TYPES or plan_type in seen_types:
                continue
            seen_types.add(plan_type)
            question_plan.append(
                {
                    "type": plan_type,
                    "focus": str(item.get("focus", "")).strip(),
                    "path": str(item.get("path", "")).strip(),
                    "code_hint": str(item.get("code_hint", "")).strip(),
                }
            )
            if len(question_plan) >= 4:
                break

    for required_type in QUESTION_PLAN_TYPES:
        if required_type not in seen_types:
            question_plan.append(
                {
                    "type": required_type,
                    "focus": "",
                    "path": "",
                    "code_hint": "",
                }
            )

    return {
        "summary_bullets": summary_bullets,
        "key_files": key_files,
        "key_snippets": key_snippets,
        "learning_objectives": learning_objectives,
        "question_plan": question_plan[:4],
        "change_risks": change_risks,
    }


def normalize_quiz_review(payload: Any) -> QuizReviewResult:
    data = payload if isinstance(payload, dict) else {}
    return {
        "is_valid": bool(data.get("is_valid", False)),
        "issues": _string_list(data.get("issues"), limit=8),
        "revision_instruction": str(data.get("revision_instruction", "")).strip(),
    }


def normalize_inline_grade_review(payload: Any) -> InlineGradeReviewResult:
    data = payload if isinstance(payload, dict) else {}
    return {
        "is_valid": bool(data.get("is_valid", False)),
        "issues": _string_list(data.get("issues"), limit=8),
        "revision_instruction": str(data.get("revision_instruction", "")).strip(),
        "normalized_grades": normalize_inline_grades(data.get("normalized_grades")),
    }


def normalize_inline_anchor_candidates(payload: Any) -> list[InlineAnchorCandidate]:
    items = payload if isinstance(payload, list) else []
    anchors: list[InlineAnchorCandidate] = []
    seen_pairs: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("file_path", "")).strip()
        anchor_snippet = str(item.get("anchor_snippet", "")).strip()
        question_type = str(item.get("question_type", "intent")).strip() or "intent"
        if question_type not in QUESTION_TYPES:
            question_type = "intent"
        pair = (file_path, anchor_snippet)
        if not file_path or not anchor_snippet or pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        anchors.append(
            {
                "file_path": file_path,
                "anchor_snippet": anchor_snippet,
                "question_type": question_type,
                "reason": str(item.get("reason", "")).strip(),
            }
        )
    return anchors


def normalize_inline_questions(payload: Any) -> list[InlineQuestionPayload]:
    items = payload if isinstance(payload, list) else []
    questions: list[InlineQuestionPayload] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("id", f"q{index}")).strip() or f"q{index}"
        if question_id in seen_ids:
            question_id = f"q{index}"
        seen_ids.add(question_id)
        question_type = str(item.get("question_type", "intent")).strip() or "intent"
        if question_type not in QUESTION_TYPES:
            question_type = "intent"
        questions.append(
            {
                "id": question_id,
                "file_path": str(item.get("file_path", "")).strip(),
                "anchor_snippet": str(item.get("anchor_snippet", "")).strip(),
                "question": str(item.get("question", "")).strip(),
                "expected_answer": str(item.get("expected_answer", "")).strip(),
                "question_type": question_type,
            }
        )
    return questions


def normalize_inline_grades(payload: Any) -> list[InlineGradePayload]:
    items = payload if isinstance(payload, list) else []
    grades: list[InlineGradePayload] = []
    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        grade_id = str(item.get("id", "")).strip()
        if not grade_id or grade_id in seen_ids:
            continue
        seen_ids.add(grade_id)
        try:
            score = int(item.get("score", 0))
        except Exception:
            score = 0
        grades.append(
            {
                "id": grade_id,
                "score": max(0, min(100, score)),
                "feedback": str(item.get("feedback", "")).strip(),
            }
        )
    return grades

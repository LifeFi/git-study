import json
from typing import TypedDict

from pydantic import BaseModel, Field, RootModel
from langgraph.graph import END, START, StateGraph

from ..llm.client import LLMClient
from ..llm.schemas import normalize_inline_grade_review, normalize_inline_grades
from ..prompts.inline_grade import build_inline_grade_prompt
from ..prompts.inline_grade_review import build_inline_grade_review_prompt
from ..types import InlineQuizGrade, InlineQuizQuestion


class InlineGradeGraphState(TypedDict, total=False):
    questions: list[InlineQuizQuestion]
    answers: dict[str, str]
    user_request: str
    question_blocks: str
    raw_grades: list[dict]
    review_result: dict
    final_grades: list[InlineQuizGrade]
    grading_summary: dict


class InlineGradeStructuredOutput(BaseModel):
    id: str = Field(default="")
    score: int = Field(default=0)
    feedback: str = Field(default="")


class InlineGradeListStructuredOutput(RootModel[list[InlineGradeStructuredOutput]]):
    root: list[InlineGradeStructuredOutput] = Field(default_factory=list)


class GradingSummaryStructuredOutput(BaseModel):
    weak_points: list[str] = Field(default_factory=list)
    weak_files: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    overall_comment: str = Field(default="")


class InlineGradeReviewStructuredOutput(BaseModel):
    is_valid: bool = Field(default=False)
    issues: list[str] = Field(default_factory=list)
    revision_instruction: str = Field(default="")
    normalized_grades: list[InlineGradeStructuredOutput] = Field(default_factory=list)
    grading_summary: GradingSummaryStructuredOutput = Field(
        default_factory=GradingSummaryStructuredOutput
    )


def prepare_grading_payload(state: InlineGradeGraphState) -> InlineGradeGraphState:
    blocks: list[str] = []
    for question in state.get("questions", []):
        answer = state.get("answers", {}).get(question["id"], "").strip()
        blocks.append(
            f"\n--- {question['id']} [{question['question_type']}] ---\n"
            f"코드:\n{question['anchor_snippet'][:300]}\n"
            f"질문: {question['question']}\n"
            f"모범 답안: {question['expected_answer']}\n"
            f"사용자 답변: {answer or '(답변 없음)'}\n"
        )
    return {"question_blocks": "".join(blocks)}


def grade_answers(state: InlineGradeGraphState) -> InlineGradeGraphState:
    grades_payload = LLMClient().invoke_structured(
        build_inline_grade_prompt(
            question_blocks=state.get("question_blocks", ""),
            user_request=str(state.get("user_request", "")).strip(),
        ),
        InlineGradeListStructuredOutput,
    )
    grades = normalize_inline_grades(
        grades_payload.model_dump()
        if hasattr(grades_payload, "model_dump")
        else grades_payload
    )
    return {"raw_grades": grades}


def validate_grades(state: InlineGradeGraphState) -> InlineGradeGraphState:
    expected_ids = [question["id"] for question in state.get("questions", [])]
    question_metadata = [
        {
            "id": question["id"],
            "question_type": question["question_type"],
            "question": question["question"],
            "file_path": question["file_path"],
        }
        for question in state.get("questions", [])
    ]
    review_payload = LLMClient().invoke_structured(
        build_inline_grade_review_prompt(
            grades_json=json.dumps(
                state.get("raw_grades", []), ensure_ascii=False, indent=2
            ),
            question_ids_json=json.dumps(expected_ids, ensure_ascii=False),
            questions_json=json.dumps(question_metadata, ensure_ascii=False, indent=2),
        ),
        InlineGradeReviewStructuredOutput,
    )
    review_result = normalize_inline_grade_review(
        review_payload.model_dump()
        if hasattr(review_payload, "model_dump")
        else review_payload
    )
    return {"review_result": review_result}


def finalize_grades(state: InlineGradeGraphState) -> InlineGradeGraphState:
    review = state.get("review_result", {})
    normalized_grades = review.get("normalized_grades")
    source = (
        normalized_grades
        if isinstance(normalized_grades, list) and normalized_grades
        else state.get("raw_grades", [])
    )
    final_grades: list[InlineQuizGrade] = []
    expected_ids = [question["id"] for question in state.get("questions", [])]
    seen_ids: set[str] = set()
    source_items = list(source) if isinstance(source, list) else []

    for item in source_items:
        grade_id = str(item.get("id", "")).strip()
        if not grade_id or grade_id in seen_ids or grade_id not in expected_ids:
            continue
        seen_ids.add(grade_id)
        try:
            score = int(item.get("score", 0))
        except Exception:
            score = 0
        score = max(0, min(100, score))
        feedback = str(item.get("feedback", "")).strip() or "피드백이 충분히 생성되지 않았습니다."
        final_grades.append(
            InlineQuizGrade(
                id=grade_id,
                score=score,
                feedback=feedback,
            )
        )

    if not final_grades and source_items:
        for question, item in zip(state.get("questions", []), source_items):
            try:
                score = int(item.get("score", 0))
            except Exception:
                score = 0
            feedback = (
                str(item.get("feedback", "")).strip()
                or "피드백이 충분히 생성되지 않았습니다."
            )
            final_grades.append(
                InlineQuizGrade(
                    id=question["id"],
                    score=max(0, min(100, score)),
                    feedback=feedback,
                )
            )

    grade_map = {grade["id"]: grade for grade in final_grades}
    completed: list[InlineQuizGrade] = []
    for question in state.get("questions", []):
        grade = grade_map.get(question["id"])
        if grade is not None:
            completed.append(grade)
            continue
        completed.append(
            InlineQuizGrade(
                id=question["id"],
                score=0,
                feedback="채점 결과가 누락되어 기본값으로 처리했습니다.",
            )
        )
    return {
        "final_grades": completed,
        "grading_summary": dict(review.get("grading_summary", {})),
    }


inline_grade_graph_builder = StateGraph(InlineGradeGraphState)
inline_grade_graph_builder.add_node("prepare_grading_payload", prepare_grading_payload)
inline_grade_graph_builder.add_node("grade_answers", grade_answers)
inline_grade_graph_builder.add_node("validate_grades", validate_grades)
inline_grade_graph_builder.add_node("finalize_grades", finalize_grades)

inline_grade_graph_builder.add_edge(START, "prepare_grading_payload")
inline_grade_graph_builder.add_edge("prepare_grading_payload", "grade_answers")
inline_grade_graph_builder.add_edge("grade_answers", "validate_grades")
inline_grade_graph_builder.add_edge("validate_grades", "finalize_grades")
inline_grade_graph_builder.add_edge("finalize_grades", END)

inline_grade_graph = inline_grade_graph_builder.compile(name="inline_quiz_grading_v2")

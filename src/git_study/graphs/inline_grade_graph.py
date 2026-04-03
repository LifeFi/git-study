import json
from typing import TypedDict

from pydantic import BaseModel, Field, RootModel
from langgraph.graph import END, START, StateGraph

from ..llm.client import LLMClient
from ..llm.schemas import normalize_inline_grade_review, normalize_inline_grades
from ..prompts.inline_grade import build_inline_grade_prompt
from ..prompts.inline_grade_review import build_inline_grade_review_prompt
from ..types import InlineQuizGrade, InlineQuizQuestion
from .grade_utils import build_final_grades


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
            f"코드 위치: Line {question.get('anchor_line', '?')}\n"
            f"{question.get('anchor_snippet', '')[:300]}\n"
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
    questions = state.get("questions", [])
    expected_ids = [question["id"] for question in questions]
    source_items = list(source) if isinstance(source, list) else []

    raw_grades = build_final_grades(source_items, expected_ids, questions)
    completed: list[InlineQuizGrade] = [
        InlineQuizGrade(id=g["id"], score=g["score"], feedback=g["feedback"])
        for g in raw_grades
    ]
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

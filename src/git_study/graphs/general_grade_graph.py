import json
from typing import TypedDict

from pydantic import BaseModel, Field, RootModel
from langgraph.graph import END, START, StateGraph

from ..llm.client import LLMClient
from ..llm.schemas import normalize_general_grade_review, normalize_general_grades
from ..prompts.general_grade import build_general_grade_prompt
from ..prompts.general_grade_review import build_general_grade_review_prompt
from ..types import GeneralQuizGrade, GeneralQuizQuestion
from .grade_utils import build_final_grades


class GeneralGradeGraphState(TypedDict, total=False):
    questions: list[GeneralQuizQuestion]
    answers: dict[str, str]
    user_request: str
    question_blocks: str
    raw_grades: list[dict]
    review_result: dict
    final_grades: list[GeneralQuizGrade]
    grading_summary: dict


class GeneralGradeStructuredOutput(BaseModel):
    id: str = Field(default="")
    score: int = Field(default=0)
    feedback: str = Field(default="")


class GeneralGradeListStructuredOutput(RootModel[list[GeneralGradeStructuredOutput]]):
    root: list[GeneralGradeStructuredOutput] = Field(default_factory=list)


class GradingSummaryStructuredOutput(BaseModel):
    weak_points: list[str] = Field(default_factory=list)
    weak_files: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    overall_comment: str = Field(default="")


class GeneralGradeReviewStructuredOutput(BaseModel):
    is_valid: bool = Field(default=False)
    issues: list[str] = Field(default_factory=list)
    revision_instruction: str = Field(default="")
    normalized_grades: list[GeneralGradeStructuredOutput] = Field(default_factory=list)
    grading_summary: GradingSummaryStructuredOutput = Field(
        default_factory=GradingSummaryStructuredOutput
    )


def prepare_grading_payload(state: GeneralGradeGraphState) -> GeneralGradeGraphState:
    blocks: list[str] = []
    for question in state.get("questions", []):
        answer = state.get("answers", {}).get(str(question.get("id", "")), "").strip()
        blocks.append(
            f"\n--- {question.get('id', '')} [{question.get('question_type', 'intent')}] ---\n"
            f"질문: {question.get('question', '')}\n"
            f"모범 답안: {question.get('expected_answer', '')}\n"
            f"해설: {question.get('explanation', '')}\n"
            f"사용자 답변: {answer or '(답변 없음)'}\n"
        )
    return {"question_blocks": "".join(blocks)}


def grade_answers(state: GeneralGradeGraphState) -> GeneralGradeGraphState:
    grades_payload = LLMClient().invoke_structured(
        build_general_grade_prompt(
            question_blocks=state.get("question_blocks", ""),
            user_request=str(state.get("user_request", "")).strip(),
        ),
        GeneralGradeListStructuredOutput,
    )
    grades = normalize_general_grades(
        grades_payload.model_dump()
        if hasattr(grades_payload, "model_dump")
        else grades_payload
    )
    return {"raw_grades": grades}


def validate_grades(state: GeneralGradeGraphState) -> GeneralGradeGraphState:
    expected_ids = [str(question.get("id", "")) for question in state.get("questions", [])]
    question_metadata = [
        {
            "id": str(question.get("id", "")),
            "question_type": str(question.get("question_type", "")),
            "question": str(question.get("question", "")),
            "code_reference": str(question.get("code_reference", "")),
        }
        for question in state.get("questions", [])
    ]
    review_payload = LLMClient().invoke_structured(
        build_general_grade_review_prompt(
            grades_json=json.dumps(
                state.get("raw_grades", []), ensure_ascii=False, indent=2
            ),
            question_ids_json=json.dumps(expected_ids, ensure_ascii=False),
            questions_json=json.dumps(question_metadata, ensure_ascii=False, indent=2),
        ),
        GeneralGradeReviewStructuredOutput,
    )
    review_result = normalize_general_grade_review(
        review_payload.model_dump()
        if hasattr(review_payload, "model_dump")
        else review_payload
    )
    return {"review_result": review_result}


def finalize_grades(state: GeneralGradeGraphState) -> GeneralGradeGraphState:
    review = state.get("review_result", {})
    normalized_grades = review.get("normalized_grades")
    source = (
        normalized_grades
        if isinstance(normalized_grades, list) and normalized_grades
        else state.get("raw_grades", [])
    )
    questions = state.get("questions", [])
    expected_ids = [str(question.get("id", "")) for question in questions]
    source_items = list(source) if isinstance(source, list) else []

    completed = build_final_grades(source_items, expected_ids, questions)
    return {
        "final_grades": completed,
        "grading_summary": dict(review.get("grading_summary", {})),
    }


general_grade_graph_builder = StateGraph(GeneralGradeGraphState)
general_grade_graph_builder.add_node("prepare_grading_payload", prepare_grading_payload)
general_grade_graph_builder.add_node("grade_answers", grade_answers)
general_grade_graph_builder.add_node("validate_grades", validate_grades)
general_grade_graph_builder.add_node("finalize_grades", finalize_grades)

general_grade_graph_builder.add_edge(START, "prepare_grading_payload")
general_grade_graph_builder.add_edge("prepare_grading_payload", "grade_answers")
general_grade_graph_builder.add_edge("grade_answers", "validate_grades")
general_grade_graph_builder.add_edge("validate_grades", "finalize_grades")
general_grade_graph_builder.add_edge("finalize_grades", END)

general_grade_graph = general_grade_graph_builder.compile(name="general_quiz_grading_v1")

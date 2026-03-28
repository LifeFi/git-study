import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..llm.client import LLMClient
from ..llm.schemas import normalize_general_grade_review, normalize_general_grades
from ..prompts.general_grade import build_general_grade_prompt
from ..prompts.general_grade_review import build_general_grade_review_prompt
from ..types import GeneralQuizGrade, GeneralQuizQuestion


class GeneralGradeGraphState(TypedDict, total=False):
    questions: list[GeneralQuizQuestion]
    answers: dict[str, str]
    user_request: str
    question_blocks: str
    raw_grades: list[dict]
    review_result: dict
    final_grades: list[GeneralQuizGrade]
    grading_summary: dict


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
    grades = normalize_general_grades(
        LLMClient().invoke_json(
            build_general_grade_prompt(
                question_blocks=state.get("question_blocks", ""),
                user_request=str(state.get("user_request", "")).strip(),
            )
        )
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
    review_result = normalize_general_grade_review(
        LLMClient().invoke_json(
            build_general_grade_review_prompt(
                grades_json=json.dumps(
                    state.get("raw_grades", []), ensure_ascii=False, indent=2
                ),
                question_ids_json=json.dumps(expected_ids, ensure_ascii=False),
                questions_json=json.dumps(question_metadata, ensure_ascii=False, indent=2),
            )
        )
    )
    return {"review_result": review_result}


def finalize_grades(state: GeneralGradeGraphState) -> GeneralGradeGraphState:
    review = state.get("review_result", {})
    source = review.get("normalized_grades", state.get("raw_grades", []))
    final_grades: list[dict] = []
    expected_ids = [str(question.get("id", "")) for question in state.get("questions", [])]
    seen_ids: set[str] = set()

    for item in source:
        grade_id = str(item.get("id", "")).strip()
        if not grade_id or grade_id in seen_ids or grade_id not in expected_ids:
            continue
        seen_ids.add(grade_id)
        try:
            score = int(item.get("score", 0))
        except Exception:
            score = 0
        feedback = str(item.get("feedback", "")).strip() or "피드백이 충분히 생성되지 않았습니다."
        final_grades.append(
            {
                "id": grade_id,
                "score": max(0, min(100, score)),
                "feedback": feedback,
            }
        )

    grade_map = {grade["id"]: grade for grade in final_grades}
    completed: list[dict] = []
    for question in state.get("questions", []):
        question_id = str(question.get("id", ""))
        grade = grade_map.get(question_id)
        if grade is not None:
            completed.append(grade)
            continue
        completed.append(
            {
                "id": question_id,
                "score": 0,
                "feedback": "채점 결과가 누락되어 기본값으로 처리했습니다.",
            }
        )
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

from collections.abc import Iterator

from ..graphs.inline_grade_graph import inline_grade_graph
from ..llm.token_tracker import stream_graph_with_usage
from ..types import InlineQuizGrade, InlineQuizQuestion


INLINE_GRADE_NODE_LABELS = {
    "prepare_grading_payload": "채점 입력 준비",
    "grade_answers": "답변 채점",
    "validate_grades": "채점 검증",
    "finalize_grades": "결과 정리",
}


def stream_inline_grade_progress(
    questions: list[InlineQuizQuestion],
    answers: dict[str, str],
    user_request: str = "",
    model_override: str = "",
) -> Iterator[dict]:
    yield from stream_graph_with_usage(
        inline_grade_graph,
        {
            "questions": questions,
            "answers": answers,
            "user_request": user_request,
        },
        INLINE_GRADE_NODE_LABELS,
        deduplicate_nodes=True,
        model_override=model_override,
    )


def generate_inline_quiz_grades(
    questions: list[InlineQuizQuestion],
    answers: dict[str, str],
    user_request: str = "",
) -> list[InlineQuizGrade]:
    result = generate_inline_quiz_grade_result(
        questions,
        answers,
        user_request=user_request,
    )
    return result.get("final_grades", [])


def generate_inline_quiz_grade_result(
    questions: list[InlineQuizQuestion],
    answers: dict[str, str],
    user_request: str = "",
) -> dict:
    result = inline_grade_graph.invoke(
        {
            "questions": questions,
            "answers": answers,
            "user_request": user_request,
        }
    )
    return result

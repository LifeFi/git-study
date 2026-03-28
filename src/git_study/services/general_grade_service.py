from collections.abc import Iterator

from ..graphs.general_grade_graph import general_grade_graph


GENERAL_GRADE_NODE_LABELS = {
    "prepare_grading_payload": "채점 입력 준비",
    "grade_answers": "답변 채점",
    "validate_grades": "채점 검증",
    "finalize_grades": "결과 정리",
}


def stream_general_grade_progress(
    questions: list[dict],
    answers: dict[str, str],
    user_request: str = "",
) -> Iterator[dict]:
    merged_result: dict = {}
    seen_nodes: set[str] = set()

    for chunk in general_grade_graph.stream(
        {
            "questions": questions,
            "answers": answers,
            "user_request": user_request,
        },
        stream_mode="updates",
    ):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            if node_name not in seen_nodes:
                seen_nodes.add(node_name)
                yield {
                    "type": "node",
                    "node": node_name,
                    "label": GENERAL_GRADE_NODE_LABELS.get(node_name, node_name),
                }
            if isinstance(update, dict):
                merged_result.update(update)

    yield {"type": "result", "result": merged_result}


def generate_general_quiz_grades(
    questions: list[dict],
    answers: dict[str, str],
    user_request: str = "",
) -> list[dict]:
    result = generate_general_quiz_grade_result(
        questions,
        answers,
        user_request=user_request,
    )
    return result.get("final_grades", [])


def generate_general_quiz_grade_result(
    questions: list[dict],
    answers: dict[str, str],
    user_request: str = "",
) -> dict:
    result = general_grade_graph.invoke(
        {
            "questions": questions,
            "answers": answers,
            "user_request": user_request,
        }
    )
    return result

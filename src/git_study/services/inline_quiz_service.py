from collections.abc import Iterator

from ..graphs.inline_quiz_graph import inline_quiz_graph
from ..types import InlineQuizQuestion


INLINE_QUIZ_NODE_LABELS = {
    "prepare_inline_context": "파일 문맥 준비",
    "extract_anchor_candidates": "앵커 후보 추출",
    "validate_anchor_candidates": "앵커 검증",
    "generate_inline_questions": "질문 생성",
    "review_inline_questions": "품질 검토",
    "repair_inline_questions": "초안 수정",
    "finalize_inline_questions": "결과 정리",
}


def stream_inline_quiz_progress(
    commit_context: dict,
    count: int = 4,
    user_request: str = "",
) -> Iterator[dict]:
    merged_result: dict = {}

    for chunk in inline_quiz_graph.stream(
        {
            "commit_context": commit_context,
            "count": count,
            "user_request": user_request,
        },
        stream_mode="updates",
    ):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            yield {
                "type": "node",
                "node": node_name,
                "label": INLINE_QUIZ_NODE_LABELS.get(node_name, node_name),
            }
            if isinstance(update, dict):
                merged_result.update(update)

    yield {"type": "result", "result": merged_result}


def generate_inline_quiz_questions(
    commit_context: dict,
    count: int = 4,
    user_request: str = "",
) -> list[InlineQuizQuestion]:
    result = inline_quiz_graph.invoke(
        {
            "commit_context": commit_context,
            "count": count,
            "user_request": user_request,
        }
    )
    return result.get("inline_questions", [])

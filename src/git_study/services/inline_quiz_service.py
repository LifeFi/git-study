from collections.abc import Iterator

from ..graphs.inline_quiz_graph import inline_quiz_graph
from ..llm.token_tracker import stream_graph_with_usage
from ..types import InlineQuizQuestion


INLINE_QUIZ_NODE_LABELS = {
    "prepare_inline_context": "파일 문맥 준비",
    "generate_with_anchor": "앵커 선정 + 질문 생성",
    "review_inline_questions": "품질 검토",
    "repair_inline_questions": "초안 수정",
    "finalize_inline_questions": "결과 정리",
}


def stream_inline_quiz_progress(
    commit_context: dict,
    count: int = 4,
    user_request: str = "",
    full_file_map: dict | None = None,
    author_context: str = "self",
    model_override: str = "",
) -> Iterator[dict]:
    graph_input: dict = {
        "commit_context": commit_context,
        "count": count,
        "user_request": user_request,
        "author_context": author_context,
    }
    if full_file_map:
        graph_input["full_file_map"] = full_file_map

    yield from stream_graph_with_usage(
        inline_quiz_graph,
        graph_input,
        INLINE_QUIZ_NODE_LABELS,
        model_override=model_override,
    )


def generate_inline_quiz_questions(
    commit_context: dict,
    count: int = 4,
    user_request: str = "",
    full_file_map: dict | None = None,
) -> list[InlineQuizQuestion]:
    graph_input: dict = {
        "commit_context": commit_context,
        "count": count,
        "user_request": user_request,
    }
    if full_file_map:
        graph_input["full_file_map"] = full_file_map
    result = inline_quiz_graph.invoke(graph_input)
    return result.get("inline_questions", [])

from ..graphs.inline_quiz_graph import inline_quiz_graph
from ..types import InlineQuizQuestion


def generate_inline_quiz_questions(
    commit_context: dict,
    count: int = 4,
) -> list[InlineQuizQuestion]:
    result = inline_quiz_graph.invoke(
        {
            "commit_context": commit_context,
            "count": count,
        }
    )
    return result.get("inline_questions", [])

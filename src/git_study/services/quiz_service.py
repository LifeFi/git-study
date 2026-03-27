from typing import Any

from langchain_core.messages import AIMessage

from ..graphs.quiz_graph import quiz_graph


def run_quiz(payload: dict[str, Any]) -> dict[str, Any]:
    result = quiz_graph.invoke(
        payload,
        config={"configurable": {"thread_id": "textual-tui-session"}},
    )
    final_output = str(result.get("final_output", ""))
    result["messages"] = [AIMessage(content=final_output)]
    return result

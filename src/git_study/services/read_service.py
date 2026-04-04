from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessage

from ..graphs.read_graph import read_graph
from ..llm.token_tracker import stream_graph_with_usage


READ_NODE_LABELS = {
    "resolve_commit_context": "컨텍스트 준비",
    "analyze_change": "변경 분석",
    "draft_reading": "읽을거리 초안 생성",
    "review_reading": "품질 검토",
    "repair_reading": "초안 수정",
    "finalize_reading": "결과 정리",
}


def _read_config() -> dict[str, Any]:
    return {"configurable": {"thread_id": "textual-tui-session"}}


def stream_read_progress(payload: dict[str, Any], model_override: str = "") -> Iterator[dict[str, Any]]:
    for event in stream_graph_with_usage(
        read_graph,
        payload,
        READ_NODE_LABELS,
        extra_config=_read_config(),
        model_override=model_override,
    ):
        if event["type"] == "result":
            result = event["result"]
            final_output = str(result.get("final_output", ""))
            result["messages"] = [AIMessage(content=final_output)]
        yield event


def run_read(payload: dict[str, Any]) -> dict[str, Any]:
    result = read_graph.invoke(
        payload,
        config=_read_config(),
    )
    final_output = str(result.get("final_output", ""))
    result["messages"] = [AIMessage(content=final_output)]
    return result

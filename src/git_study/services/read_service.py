from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessage

from ..graphs.read_graph import read_graph


READ_NODE_LABELS = {
    "resolve_commit_context": "컨텍스트 준비",
    "analyze_change": "변경 분석",
    "draft_reading": "읽을거리 초안 생성",
    "review_reading": "품질 검토",
    "finalize_reading": "결과 정리",
}


def _read_config() -> dict[str, Any]:
    return {"configurable": {"thread_id": "textual-tui-session"}}


def stream_read_progress(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    merged_result: dict[str, Any] = {}
    seen_nodes: set[str] = set()

    for chunk in read_graph.stream(
        payload,
        config=_read_config(),
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
                    "label": READ_NODE_LABELS.get(node_name, node_name),
                }
            if isinstance(update, dict):
                merged_result.update(update)

    final_output = str(merged_result.get("final_output", ""))
    merged_result["messages"] = [AIMessage(content=final_output)]
    yield {"type": "result", "result": merged_result}


def run_read(payload: dict[str, Any]) -> dict[str, Any]:
    result = read_graph.invoke(
        payload,
        config=_read_config(),
    )
    final_output = str(result.get("final_output", ""))
    result["messages"] = [AIMessage(content=final_output)]
    return result

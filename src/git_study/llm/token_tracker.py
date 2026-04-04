"""Token usage tracking for LangChain/LangGraph LLM calls."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .client import model_override_var


class TokenUsageCallback(BaseCallbackHandler):
    """Accumulates prompt/completion token counts across multiple LLM calls."""

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.model_name: str = ""

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        llm_output = response.llm_output or {}
        usage = llm_output.get("token_usage", {})
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        if not self.model_name:
            self.model_name = llm_output.get("model_name", "")


def stream_graph_with_usage(
    graph: Any,
    graph_input: dict[str, Any],
    node_labels: dict[str, str],
    extra_config: dict[str, Any] | None = None,
    *,
    deduplicate_nodes: bool = False,
    model_override: str = "",
) -> Iterator[dict[str, Any]]:
    """LangGraph graph.stream() 래퍼: node / usage / result 이벤트를 yield.

    Yields:
        {"type": "node",  "node": str, "label": str}
        {"type": "usage", "input_tokens": int, "output_tokens": int}
        {"type": "result", "result": dict}
    """
    tracker = TokenUsageCallback()
    cv_token = model_override_var.set(model_override) if model_override else None
    try:
        config: dict[str, Any] = {"callbacks": [tracker]}
        if extra_config:
            for k, v in extra_config.items():
                if k == "callbacks":
                    config["callbacks"] = list(config["callbacks"]) + list(v)
                else:
                    config[k] = v

        merged: dict[str, Any] = {}
        seen_nodes: set[str] = set()

        for chunk in graph.stream(graph_input, config=config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue
            for node_name, update in chunk.items():
                if not deduplicate_nodes or node_name not in seen_nodes:
                    seen_nodes.add(node_name)
                    yield {
                        "type": "node",
                        "node": node_name,
                        "label": node_labels.get(node_name, node_name),
                    }
                if isinstance(update, dict):
                    merged.update(update)

        yield {
            "type": "usage",
            "input_tokens": tracker.input_tokens,
            "output_tokens": tracker.output_tokens,
            "model_name": tracker.model_name,
        }
        yield {"type": "result", "result": merged}
    finally:
        if cv_token is not None:
            model_override_var.reset(cv_token)

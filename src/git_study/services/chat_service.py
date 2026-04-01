"""Chat service: multi-turn LLM streaming with LangGraph SqliteSaver."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import HumanMessage, SystemMessage


ROUTE_LABELS: dict[str, str] = {
    "commit_question": "코드 리뷰어",
    "quiz_question": "퀴즈 튜터",
    "general": "일반 대화",
}


def _get_chat_db_path(
    repo_source: str,
    github_repo_url: str,
    local_repo_root: Path | None,
) -> Path:
    """chat.db 경로 결정."""
    from ..tui.state import get_chat_db_path
    return get_chat_db_path(
        repo_source=repo_source,
        github_repo_url=github_repo_url or "",
        local_repo_root=local_repo_root,
    )


def stream_chat(
    *,
    thread_id: str,
    user_text: str,
    commit_context: str = "",
    quiz_context: str = "",
    commit_diff_context: str = "",
    oldest_sha: str = "",
    newest_sha: str = "",
    local_repo_root: Path | None = None,
    repo_source: str = "local",
    github_repo_url: str = "",
) -> Iterator[dict]:
    """
    LangGraph SqliteSaver를 사용해 멀티 에이전트 채팅 스트리밍.

    yields:
      {"type": "route",      "route": "...", "label": "..."}  - 라우팅 결과
      {"type": "token",       "content": "..."}               - AI 응답 토큰
      {"type": "tool_call",   "name": "...", "args": {}}      - tool 호출 시작
      {"type": "tool_result", "content": "..."}               - tool 실행 결과
      {"type": "done",        "full_content": "..."}          - 완료
      {"type": "error",       "content": "..."}               - 에러
    """
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langchain_core.messages import AIMessageChunk, ToolMessage, AIMessage

    from ..graphs.chat_graph import build_chat_graph, make_tools

    db_path = _get_chat_db_path(repo_source, github_repo_url, local_repo_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    tools = make_tools(
        oldest_sha=oldest_sha,
        newest_sha=newest_sha,
        local_repo_root=str(local_repo_root) if local_repo_root else "",
    )

    try:
        graph_builder = build_chat_graph(
            tools,
            quiz_context=quiz_context,
            commit_diff_context=commit_diff_context,
        )
    except Exception as exc:
        yield {"type": "error", "content": str(exc)}
        return

    _MAX_RETRIES = 3

    for attempt in range(_MAX_RETRIES + 1):
        yielded_any = False
        try:
            with SqliteSaver.from_conn_string(str(db_path)) as checkpointer:
                app = graph_builder.compile(checkpointer=checkpointer)
                config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

                # 신규 thread인지 확인 → 커밋 컨텍스트 주입
                existing = checkpointer.get(config)
                if existing is None and commit_context:
                    input_messages = [
                        SystemMessage(content=commit_context),
                        HumanMessage(content=user_text),
                    ]
                else:
                    input_messages = [HumanMessage(content=user_text)]

                full_content = ""
                route_emitted = False

                # stream_mode 리스트 사용 시 모든 이벤트가 (mode, data) 튜플로 옴
                for mode, data in app.stream(
                    {"messages": input_messages},
                    config=config,
                    stream_mode=["messages", "updates"],
                ):
                    if mode == "updates":
                        # supervisor 노드 완료 → route 이벤트 yield
                        if isinstance(data, dict) and "supervisor" in data:
                            supervisor_output = data["supervisor"]
                            route = supervisor_output.get("route", "general")
                            if not route_emitted:
                                event = {
                                    "type": "route",
                                    "route": route,
                                    "label": ROUTE_LABELS.get(route, route),
                                }
                                yielded_any = True
                                yield event
                                route_emitted = True

                    elif mode == "messages":
                        # data = (chunk, metadata)
                        chunk, metadata = data
                        # supervisor 노드 출력은 route JSON이므로 스킵
                        if metadata.get("langgraph_node") == "supervisor":
                            continue
                        if isinstance(chunk, AIMessageChunk):
                            if chunk.content:
                                full_content += str(chunk.content)
                                yielded_any = True
                                yield {"type": "token", "content": str(chunk.content)}
                        elif isinstance(chunk, AIMessage):
                            for tc in (chunk.tool_calls or []):
                                yielded_any = True
                                yield {
                                    "type": "tool_call",
                                    "name": tc.get("name", ""),
                                    "args": tc.get("args", {}),
                                }
                        elif isinstance(chunk, ToolMessage):
                            yield {"type": "tool_result", "content": str(chunk.content)}

                yield {"type": "done", "full_content": full_content}
                return

        except Exception as exc:
            exc_str = str(exc)
            is_rate_limit = "429" in exc_str or "rate_limit_exceeded" in exc_str.lower()
            is_ctx_exceeded = "context_length_exceeded" in exc_str or (
                "400" in exc_str and "maximum context length" in exc_str
            )
            if is_rate_limit and not yielded_any and attempt < _MAX_RETRIES:
                wait = min(0.5 * (2 ** attempt), 5.0)  # 0.5s → 1s → 2s, 최대 5s
                time.sleep(wait)
                continue
            if is_rate_limit:
                yield {"type": "error", "content": "API 요청 한도 초과. 잠시 후 다시 시도해 주세요."}
            elif is_ctx_exceeded:
                yield {"type": "error", "content": "대화 기록이 너무 깁니다. /clear 로 새 대화를 시작해 주세요."}
            else:
                yield {"type": "error", "content": exc_str}
            return


def get_chat_history(
    *,
    thread_id: str,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | None = None,
) -> list[dict]:
    """SqliteSaver에서 thread의 메시지 히스토리를 반환."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

    db_path = _get_chat_db_path(repo_source, github_repo_url, local_repo_root)
    if not db_path.exists():
        return []

    try:
        with SqliteSaver.from_conn_string(str(db_path)) as checkpointer:
            config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
            state = checkpointer.get(config)
            if state is None:
                return []
            messages = state.get("channel_values", {}).get("messages", [])
            result = []
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    result.append({"role": "human", "content": str(msg.content)})
                elif isinstance(msg, AIMessage):
                    result.append({
                        "role": "ai",
                        "content": str(msg.content),
                        "tool_calls": msg.tool_calls or [],
                    })
                elif isinstance(msg, SystemMessage):
                    pass  # 시스템 메시지는 표시 불필요
                elif isinstance(msg, ToolMessage):
                    result.append({
                        "role": "tool",
                        "name": msg.name or "",
                        "content": str(msg.content),
                    })
            return result
    except Exception:
        return []

"""Chat graph: multi-turn LLM conversation with supervisor-based multi-agent routing."""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class MultiAgentChatState(TypedDict):
    messages: Annotated[list, add_messages]
    route: str  # "commit_question" | "quiz_question" | "general"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SUPERVISOR_PROMPT = """사용자 메시지를 다음 세 가지 중 하나로 분류하세요.

분류 기준:
- commit_question: 현재 Git 커밋 또는 코드 변경사항에 대한 질문
  예) "왜 이렇게 바꿨나요?", "이 코드가 뭐예요?", "이 함수는 어떤 역할이에요?", "변경된 파일이 뭐예요?"
- quiz_question: 퀴즈 문항 자체에 대한 질문 (힌트, 설명 요청 등)
  예) "3번 문제 힌트 주세요", "이 질문이 무슨 말인지 모르겠어요", "expected answer가 왜 그래요?"
- general: 그 외 일반적인 대화
  예) "LangGraph가 뭐예요?", "안녕", "고마워요"

반드시 route 필드 하나만 반환하세요."""


def _code_reviewer_system_prompt(commit_diff_context: str) -> str:
    lines = [
        "당신은 숙련된 코드 리뷰어입니다.",
        "",
        "역할:",
        "- 커밋 변경사항의 의도와 목적을 명확하게 설명",
        "- 사용된 패턴, 아키텍처, 설계 결정을 분석",
        "- 코드 품질과 주의할 점 설명",
        "- 필요하면 get_file_content 도구로 전체 파일 내용 확인",
        "",
        "한국어로 답변. 코드 스니펫은 마크다운 코드 블록 사용.",
    ]
    if commit_diff_context:
        lines += ["", "[커밋 컨텍스트]", commit_diff_context]
    return "\n".join(lines)


def _quiz_explainer_system_prompt(quiz_context: str) -> str:
    lines = [
        "당신은 코드 학습 튜터입니다.",
        "",
        "역할:",
        "- 퀴즈 질문의 의도와 핵심 개념을 친절하게 설명",
        "- 힌트 제공 (정답을 직접 알려주지 않고 이해를 돕는 방향으로)",
        "- 관련 코드 개념 보충 설명",
        "- 필요하면 get_file_content 도구로 관련 코드 확인",
        "",
        "한국어로 답변. 격려하는 튜터 톤.",
    ]
    if quiz_context:
        lines += ["", "[현재 퀴즈 문항]", quiz_context]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def make_tools(oldest_sha: str = "", newest_sha: str = "", local_repo_root: str = "") -> list:
    """현재 커밋 범위 컨텍스트를 가진 tool 목록 반환."""

    @tool
    def list_changed_files() -> str:
        """현재 선택된 커밋 범위에서 변경된 파일 목록을 반환한다."""
        if not oldest_sha or not local_repo_root:
            return "커밋 또는 저장소가 선택되지 않았습니다."
        try:
            from git import Repo
            from ..domain.code_context import get_range_changed_file_paths
            repo = Repo(local_repo_root)
            target_sha = newest_sha or oldest_sha
            target = repo.commit(target_sha)
            oldest = repo.commit(oldest_sha)
            parent = oldest.parents[0] if oldest.parents else None
            files = get_range_changed_file_paths(parent, target) if parent else list(target.stats.files.keys())
            return "\n".join(files) if files else "변경된 파일 없음"
        except Exception as exc:
            return f"파일 목록을 가져올 수 없습니다: {exc}"

    @tool
    def get_file_content(file_path: str) -> str:
        """주어진 파일 경로의 내용을 현재 커밋 기준으로 반환한다."""
        if not oldest_sha:
            return "커밋이 선택되지 않아 파일을 읽을 수 없습니다."
        if not local_repo_root:
            return "로컬 저장소 경로가 설정되지 않아 파일을 읽을 수 없습니다."
        try:
            from git import Repo
            from ..domain.code_context import get_file_content_at_commit_or_empty
            repo = Repo(local_repo_root)
            return get_file_content_at_commit_or_empty(repo, oldest_sha, file_path)
        except Exception as exc:
            return f"파일을 읽을 수 없습니다: {exc}"

    return [list_changed_files, get_file_content]


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_chat_graph(
    tools: list,
    quiz_context: str = "",
    commit_diff_context: str = "",
):
    """멀티 에이전트 chat graph를 빌드한다 (compile 제외).

    에이전트:
      - supervisor: 질문 유형 분류 (commit_question / quiz_question / general)
      - code_reviewer: 커밋/코드 변경 전문 리뷰어
      - quiz_explainer: 퀴즈 문항 설명 튜터
      - general_chat: 일반 대화
    """
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel
    from ..secrets import get_openai_api_key
    from ..settings import DEFAULT_MODEL, load_settings

    settings = load_settings()
    api_key, source = get_openai_api_key(settings.get("openai_api_key_mode"))
    if not api_key:
        raise ValueError("OpenAI API key가 설정되지 않았습니다.")
    model_name = settings.get("model", DEFAULT_MODEL)
    llm = init_chat_model(model_name, model_provider="openai", api_key=api_key)

    tool_node = ToolNode(tools)
    bound_llm = llm.bind_tools(tools)

    # --- Supervisor ---

    class RouteDecision(BaseModel):
        route: Literal["commit_question", "quiz_question", "general"]

    classifier_llm = llm.with_structured_output(RouteDecision)

    def supervisor_node(state: MultiAgentChatState) -> dict:
        human_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        if not human_messages:
            return {"route": "general"}
        last_user_msg = human_messages[-1]

        # 퀴즈가 없으면 quiz_question 라우팅 금지
        prompt = SUPERVISOR_PROMPT
        if not quiz_context:
            prompt += "\n\n주의: 현재 퀴즈가 없으므로 quiz_question은 사용하지 마세요."

        try:
            decision = classifier_llm.invoke([
                SystemMessage(content=prompt),
                HumanMessage(content=str(last_user_msg.content)),
            ])
            route = decision.route
            if route == "quiz_question" and not quiz_context:
                route = "general"
        except Exception:
            route = "general"

        return {"route": route}

    # --- Code Reviewer ---

    reviewer_system = _code_reviewer_system_prompt(commit_diff_context)

    def code_reviewer_node(state: MultiAgentChatState) -> dict:
        messages = list(state["messages"])
        # 시스템 메시지가 없으면 앞에 삽입
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=reviewer_system)] + messages
        elif isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=reviewer_system)
        response = bound_llm.invoke(messages)
        return {"messages": [response]}

    # --- Quiz Explainer ---

    explainer_system = _quiz_explainer_system_prompt(quiz_context)

    def quiz_explainer_node(state: MultiAgentChatState) -> dict:
        messages = list(state["messages"])
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=explainer_system)] + messages
        elif isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=explainer_system)
        response = bound_llm.invoke(messages)
        return {"messages": [response]}

    # --- General Chat ---

    def general_chat_node(state: MultiAgentChatState) -> dict:
        response = bound_llm.invoke(state["messages"])
        return {"messages": [response]}

    # --- Tool 복귀 라우팅 ---

    def should_continue_agent(state: MultiAgentChatState) -> str:
        """현재 에이전트에서 tool 호출 여부 확인."""
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "__end__"

    def route_after_tools(state: MultiAgentChatState) -> str:
        """tools 실행 후 state["route"]에 따라 복귀 에이전트 결정."""
        route = state.get("route", "general")
        if route == "commit_question":
            return "code_reviewer"
        elif route == "quiz_question":
            return "quiz_explainer"
        return "general_chat"

    # --- Graph 배선 ---

    builder = StateGraph(MultiAgentChatState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("code_reviewer", code_reviewer_node)
    builder.add_node("quiz_explainer", quiz_explainer_node)
    builder.add_node("general_chat", general_chat_node)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda s: s.get("route", "general"),
        {
            "commit_question": "code_reviewer",
            "quiz_question": "quiz_explainer",
            "general": "general_chat",
        },
    )

    for agent in ("code_reviewer", "quiz_explainer", "general_chat"):
        builder.add_conditional_edges(
            agent,
            should_continue_agent,
            {"tools": "tools", "__end__": END},
        )

    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "code_reviewer": "code_reviewer",
            "quiz_explainer": "quiz_explainer",
            "general_chat": "general_chat",
        },
    )

    return builder

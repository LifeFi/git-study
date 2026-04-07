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
    route: str  # "commit_question" | "quiz_question" | "learning_path" | "general"
    actions: list  # ["quiz", "map"] 등
    actions_args: list  # ["HEAD~3 5", "--full"] 등 (actions와 같은 길이)
    has_chat_question: bool  # 액션 외에 채팅 질문도 포함된 경우 True
    chat_question: str  # has_chat_question=True일 때 채팅 질문 텍스트만 추출


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SUPERVISOR_PROMPT = """사용자 메시지를 분류하세요. route와 actions, actions_args 필드를 반환합니다.

## actions 분류 (리스트, 여러 개 가능)
사용자가 요청한 모든 기능 실행을 순서대로 추출하세요.
각 항목은 다음 중 하나: "quiz" | "review" | "grade" | "map" | "none"
액션이 없으면 ["none"], args는 [""]

- "quiz": 퀴즈 생성 요청. 예) "퀴즈 만들어줘", "퀴즈 3개", "문제 내줘", "퀴즈 시작"
  주의: "퀴즈 2번 힌트", "퀴즈2에 대해", "N번 문제 설명" 등 특정 번호 언급은 생성 요청이 아님 → actions: ["none"]
- "review": 커밋 해설·리뷰 요청. 예) "리뷰해줘", "해설해줘", "커밋 설명해줘", "코드 리뷰 해줘"
- "grade": 채점 요청. 예) "채점해줘", "점수 매겨줘", "답변 평가해줘", "채점 시작"
- "map": 파일 구조·맵 요청. 예) "맵 만들어줘", "파일 구조 보여줘", "프로젝트 맵"
- "none": 위 요청이 아닌 경우 (질문, 대화 등)

actions에 "none"이 아닌 항목이 있으면 route는 "general"로 설정하세요.

## actions_args 추출 (actions와 같은 길이의 리스트)
각 action에 대응하는 인자 문자열을 순서대로 추출:
- 숫자(개수): "3개" → "3", "10문제" → "10"
- 범위: "이전 커밋" → "HEAD~1", "최근 3개 커밋" → "HEAD~3"
- 저자 옵션: "AI 코드" → "--ai", "남이 짠 코드" → "--others"
- map 옵션: "전체 프로젝트 맵" → "--full", "캐시 무시" → "--refresh"
- 인자 없으면 ""

예시:
- "퀴즈 3개 만들어줘" → actions: ["quiz"], actions_args: ["3"]
- "맵 만들고 퀴즈 10개" → actions: ["map", "quiz"], actions_args: ["", "10"]
- "리뷰하고 채점해줘" → actions: ["review", "grade"], actions_args: ["", ""]
- "전체 맵 만들고 퀴즈 5개 만들어줘" → actions: ["map", "quiz"], actions_args: ["--full", "5"]
- "이 코드 어때?" → actions: ["none"], actions_args: [""]

## route 분류 (actions가 ["none"]인 경우만 의미 있음)
- commit_question: 현재 Git 커밋 또는 코드 변경사항에 대한 질문
  예) "왜 이렇게 바꿨나요?", "이 코드가 뭐예요?", "이 함수는 어떤 역할이에요?", "변경된 파일이 뭐예요?"
- quiz_question: 퀴즈 문항 자체에 대한 질문 (힌트, 설명 요청 등)
  예) "3번 문제 힌트 주세요", "퀴즈2 힌트 부탁해", "퀴즈 2번 설명해줘", "이 질문이 무슨 말인지 모르겠어요", "expected answer가 왜 그래요?"
- learning_path: 학습 방향, 약점, 다음에 뭘 공부할지에 대한 질문
  예) "내 약점이 뭐야?", "다음에 뭘 공부해야 해?", "어느 부분이 부족해?", "피드백 요약해줘"
- general: 그 외 일반적인 대화
  예) "LangGraph가 뭐예요?", "안녕", "고마워요"

## has_chat_question
actions에 "none"이 아닌 항목이 있을 때, 액션 요청 외에 별도 질문이나 대화가 포함되어 있으면 True.
- "퀴즈 만들어줘." → False (순수 액션)
- "퀴즈 만들어줘. 그리고 textal이 뭐야?" → True (액션 + 질문)
- "리뷰하고 이 코드 설명해줘" → True (액션 + 질문)
- actions가 ["none"]이면 항상 False.

## chat_question
has_chat_question=True일 때, 액션 요청 부분을 제외한 순수 채팅 질문 텍스트만 추출.
- "퀴즈 만들어줘. 그리고 textal이 뭐야?" → "textal이 뭐야?"
- "리뷰하고 이 코드 설명해줘" → "이 코드 설명해줘"
- "맵 만들고, 오늘 날씨 어때?" → "오늘 날씨 어때?"
- has_chat_question=False이면 "" (빈 문자열).

반드시 route, actions, actions_args, has_chat_question, chat_question 다섯 필드를 반환하세요."""


def _code_reviewer_system_prompt(
    commit_diff_context: str,
    mentioned_snippets: dict | None = None,
) -> str:
    lines = [
        "당신은 숙련된 코드 리뷰어입니다.",
        "",
        "역할:",
        "- 커밋 변경사항의 의도와 목적을 명확하게 설명",
        "- 사용된 패턴, 아키텍처, 설계 결정을 분석",
        "- 코드 품질과 주의할 점 설명",
        "- 필요하면 get_file_content 또는 list_all_files 도구로 파일 내용 확인",
        "",
        "한국어로 답변. 코드 스니펫은 마크다운 코드 블록 사용.",
    ]
    if commit_diff_context:
        lines += ["", "[커밋 컨텍스트]", commit_diff_context]
    if mentioned_snippets:
        lines += ["", "[사용자가 첨부한 코드]"]
        for ref, content in mentioned_snippets.items():
            # 언어 감지: foo.py[43-80] → foo.py
            base_path = ref.split("[")[0] if "[" in ref else ref
            from pathlib import PurePath
            suffix = PurePath(base_path).suffix.lower()
            lang_map = {
                ".py": "python", ".js": "javascript", ".ts": "typescript",
                ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
            }
            lang = lang_map.get(suffix, "")
            lines += [
                f"@{ref}:",
                f"```{lang}",
                content[:3000],  # 너무 길면 자름
                "```",
            ]
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


def _study_advisor_system_prompt(grade_context: dict | None = None) -> str:
    lines = [
        "당신은 Git 커밋 학습을 돕는 스터디 어드바이저입니다.",
        "",
        "역할:",
        "- 코드와 Git에 관한 일반적인 질문에 친절하게 답변",
        "- 학습 관련 개념(LangGraph, 디자인 패턴, 아키텍처 등)을 쉽게 설명",
        "- 격려하고 다음 학습 단계를 안내",
        "",
        "한국어로 답변.",
    ]
    if grade_context:
        overall = grade_context.get("overall_comment", "")
        if overall:
            lines += ["", f"[최근 채점 총평] {overall}"]
    return "\n".join(lines)


def _learning_advisor_system_prompt(grade_context: dict | None = None) -> str:
    lines = [
        "당신은 Git 커밋 학습의 러닝 어드바이저입니다.",
        "",
        "역할:",
        "- 학습자의 약점과 강점을 분석하여 구체적인 학습 방향 제시",
        "- 다음에 무엇을 공부하면 좋을지 안내",
        "- 격려하며 동기 부여",
        "",
        "한국어로 답변.",
    ]
    if grade_context:
        weak_points = grade_context.get("weak_points", [])
        weak_files = grade_context.get("weak_files", [])
        next_steps = grade_context.get("next_steps", [])
        overall = grade_context.get("overall_comment", "")
        if overall:
            lines += ["", f"[총평] {overall}"]
        if weak_points:
            lines += ["", "[약점]"] + [f"- {wp}" for wp in weak_points]
        if weak_files:
            lines += ["", "[취약 파일]"] + [f"- {wf}" for wf in weak_files]
        if next_steps:
            lines += ["", "[추천 학습 단계]"] + [f"- {ns}" for ns in next_steps]
    else:
        lines += [
            "",
            "채점 결과가 아직 없습니다. 먼저 /quiz → 답변 → /grade 순서로 진행하세요.",
        ]
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
        """주어진 파일 경로의 내용을 반환한다. 커밋이 선택된 경우 해당 시점 기준, 아니면 현재 파일시스템 기준."""
        if not local_repo_root:
            return "로컬 저장소 경로가 설정되지 않아 파일을 읽을 수 없습니다."
        try:
            if oldest_sha or newest_sha:
                from git import Repo
                from ..domain.code_context import get_file_content_at_commit_or_empty
                repo = Repo(local_repo_root)
                target_sha = newest_sha or oldest_sha
                return get_file_content_at_commit_or_empty(repo, target_sha, file_path)
            else:
                from pathlib import Path
                full_path = Path(local_repo_root) / file_path
                if not full_path.exists():
                    return f"파일을 찾을 수 없습니다: {file_path}"
                return full_path.read_text(errors="replace")
        except Exception as exc:
            return f"파일을 읽을 수 없습니다: {exc}"

    @tool
    def list_all_files() -> str:
        """저장소의 전체 파일 목록을 반환한다. 커밋이 선택된 경우 해당 시점 기준, 아니면 현재 파일시스템 기준."""
        if not local_repo_root:
            return "로컬 저장소 경로가 설정되지 않았습니다."
        try:
            if newest_sha or oldest_sha:
                from git import Repo
                from ..domain.code_context import list_commit_tree_files
                repo = Repo(local_repo_root)
                target_sha = newest_sha or oldest_sha
                files = list_commit_tree_files(repo, target_sha)
            else:
                from pathlib import Path
                root = Path(local_repo_root)
                files = [
                    str(p.relative_to(root))
                    for p in sorted(root.rglob("*"))
                    if p.is_file() and ".git" not in p.parts
                ]
            return "\n".join(files) if files else "파일 없음"
        except Exception as exc:
            return f"파일 목록을 가져올 수 없습니다: {exc}"

    return [list_changed_files, get_file_content, list_all_files]


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_chat_graph(
    tools: list,
    quiz_context: str = "",
    commit_diff_context: str = "",
    mentioned_snippets: dict | None = None,
    grade_context: dict | None = None,
):
    """멀티 에이전트 chat graph를 빌드한다 (compile 제외).

    에이전트:
      - supervisor: 질문 유형 분류 (commit_question / quiz_question / learning_path / general)
      - code_reviewer: 커밋/코드 변경 전문 리뷰어
      - quiz_explainer: 퀴즈 문항 설명 튜터
      - learning_advisor: 학습 방향 안내 (채점 결과 기반)
      - study_advisor: 일반 대화 (시스템 프롬프트 포함)
    """
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel
    from ..secrets import get_openai_api_key
    from ..settings import DEFAULT_MODEL, load_settings

    settings = load_settings()
    api_key, _ = get_openai_api_key()
    if not api_key:
        raise ValueError("OpenAI API key가 설정되지 않았습니다.")
    model_name = settings.get("model", DEFAULT_MODEL)
    llm = init_chat_model(model_name, model_provider="openai", api_key=api_key, streaming=True)

    tool_node = ToolNode(tools)
    bound_llm = llm.bind_tools(tools)

    # --- Supervisor ---

    class RouteDecision(BaseModel):
        route: Literal["commit_question", "quiz_question", "learning_path", "general"]
        actions: list[Literal["quiz", "review", "grade", "map", "none"]]
        actions_args: list[str]
        has_chat_question: bool  # 액션 외에 별도 채팅 질문이 있으면 True
        chat_question: str  # has_chat_question=True일 때 채팅 질문 텍스트만 추출 (액션 제외)

    classifier_llm = llm.with_structured_output(RouteDecision)

    def supervisor_node(state: MultiAgentChatState) -> dict:
        human_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        if not human_messages:
            return {"route": "general", "actions": ["none"], "actions_args": [""]}
        last_user_msg = human_messages[-1]

        # 퀴즈가 없으면 quiz_question 라우팅 금지
        prompt = SUPERVISOR_PROMPT
        if not quiz_context:
            prompt += "\n\n주의: 현재 퀴즈가 없으므로 quiz_question은 사용하지 마세요."
        if not grade_context:
            prompt += "\n\n주의: 현재 채점 결과가 없으므로 learning_path는 사용하지 마세요."

        try:
            decision = classifier_llm.invoke([
                SystemMessage(content=prompt),
                HumanMessage(content=str(last_user_msg.content)),
            ])
            route = decision.route
            actions = decision.actions or ["none"]
            actions_args = decision.actions_args or [""] * len(actions)
            has_chat_question = decision.has_chat_question
            chat_question = decision.chat_question if has_chat_question else ""
            if route == "quiz_question" and not quiz_context:
                route = "general"
            if route == "learning_path" and not grade_context:
                route = "general"
        except Exception:
            route = "general"
            actions = ["none"]
            actions_args = [""]
            has_chat_question = False
            chat_question = ""

        return {"route": route, "actions": actions, "actions_args": actions_args, "has_chat_question": has_chat_question, "chat_question": chat_question}

    # --- Code Reviewer ---

    reviewer_system = _code_reviewer_system_prompt(commit_diff_context, mentioned_snippets)

    def _replace_last_human_msg(messages: list, text: str) -> list:
        """chat_question이 있을 때 마지막 HumanMessage를 해당 텍스트로 대체."""
        messages = list(messages)
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                messages[i] = HumanMessage(content=text)
                break
        return messages

    def code_reviewer_node(state: MultiAgentChatState) -> dict:
        messages = list(state["messages"])
        chat_q = state.get("chat_question", "")
        if chat_q:
            messages = _replace_last_human_msg(messages, chat_q)
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
        chat_q = state.get("chat_question", "")
        if chat_q:
            messages = _replace_last_human_msg(messages, chat_q)
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=explainer_system)] + messages
        elif isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=explainer_system)
        response = bound_llm.invoke(messages)
        return {"messages": [response]}

    # --- General Assistant (formerly general_chat / study_advisor) ---

    advisor_system = _study_advisor_system_prompt(grade_context)

    def general_assistant_node(state: MultiAgentChatState) -> dict:
        messages = list(state["messages"])
        chat_q = state.get("chat_question", "")
        if chat_q:
            messages = _replace_last_human_msg(messages, chat_q)
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=advisor_system)] + messages
        elif isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=advisor_system)
        response = bound_llm.invoke(messages)
        return {"messages": [response]}

    # --- Learning Advisor ---

    learning_system = _learning_advisor_system_prompt(grade_context)

    def learning_advisor_node(state: MultiAgentChatState) -> dict:
        messages = list(state["messages"])
        chat_q = state.get("chat_question", "")
        if chat_q:
            messages = _replace_last_human_msg(messages, chat_q)
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=learning_system)] + messages
        elif isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=learning_system)
        # pure LLM — no tools
        response = llm.invoke(messages)
        return {"messages": [response]}

    # --- Action Responder ---

    _ACTION_LABELS: dict[str, str] = {
        "quiz": "퀴즈 생성",
        "review": "커밋 해설",
        "grade": "채점",
        "map": "파일 구조 맵 생성",
    }
    _ACTION_COMMANDS: dict[str, str] = {
        "quiz": "/quiz",
        "review": "/review",
        "grade": "/grade",
        "map": "/map",
    }

    def action_responder_node(state: MultiAgentChatState) -> dict:
        actions = state.get("actions", ["none"])
        actions_args = state.get("actions_args", [])
        parts = []
        for action, args in zip(actions, actions_args):
            if action == "none":
                continue
            label = _ACTION_LABELS.get(action, action)
            cmd = _ACTION_COMMANDS.get(action, f"/{action}")
            cmd_with_args = f"{cmd} {args}".strip()
            parts.append(f"{label} ( {cmd_with_args} )")
        msg = " → ".join(parts) + "을 시작할게요!" if parts else "알겠습니다!"
        from langchain_core.messages import AIMessage
        return {"messages": [AIMessage(content=msg)]}

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
        return "general_assistant"

    # --- Graph 배선 ---

    builder = StateGraph(MultiAgentChatState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("action_responder", action_responder_node)
    builder.add_node("code_reviewer", code_reviewer_node)
    builder.add_node("quiz_explainer", quiz_explainer_node)
    builder.add_node("general_assistant", general_assistant_node)
    builder.add_node("learning_advisor", learning_advisor_node)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "supervisor")

    def _supervisor_router(state: MultiAgentChatState) -> str:
        actions = state.get("actions", ["none"])
        if actions and actions[0] != "none":
            return "action_responder"
        return state.get("route", "general")

    builder.add_conditional_edges(
        "supervisor",
        _supervisor_router,
        {
            "action_responder": "action_responder",
            "commit_question": "code_reviewer",
            "quiz_question": "quiz_explainer",
            "learning_path": "learning_advisor",
            "general": "general_assistant",
        },
    )
    def _route_after_action_responder(state: MultiAgentChatState) -> str:
        """action 확인 메시지 후, 채팅 질문도 있으면 해당 에이전트로 계속 진행."""
        if not state.get("has_chat_question", False):
            return "__end__"
        route = state.get("route", "general")
        if route == "commit_question":
            return "code_reviewer"
        if route == "quiz_question":
            return "quiz_explainer"
        if route == "learning_path":
            return "learning_advisor"
        return "general_assistant"

    builder.add_conditional_edges(
        "action_responder",
        _route_after_action_responder,
        {
            "code_reviewer": "code_reviewer",
            "quiz_explainer": "quiz_explainer",
            "learning_advisor": "learning_advisor",
            "general_assistant": "general_assistant",
            "__end__": END,
        },
    )

    # learning_advisor has no tools — goes directly to END
    builder.add_edge("learning_advisor", END)

    for agent in ("code_reviewer", "quiz_explainer", "general_assistant"):
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
            "general_assistant": "general_assistant",
        },
    )

    return builder

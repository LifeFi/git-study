from typing import Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from ..domain.repo_context import get_latest_commit_context
from ..llm.client import LLMClient
from ..llm.schemas import normalize_quiz_analysis
from ..prompts.quiz_analysis import build_quiz_analysis_prompt


class CommitAnalysisState(TypedDict, total=False):
    messages: list[BaseMessage]
    repo_source: NotRequired[Literal["local", "github"]]
    github_repo_url: NotRequired[str]
    commit_mode: NotRequired[Literal["auto", "latest", "selected"]]
    requested_commit_sha: NotRequired[str]
    requested_commit_shas: NotRequired[list[str]]
    difficulty: NotRequired[str]
    analysis_quiz_style: NotRequired[str]
    output_kind: NotRequired[Literal["quiz", "read"]]
    commit_sha: str
    commit_subject: str
    commit_author: str
    commit_date: str
    changed_files_summary: str
    diff_text: str
    file_context_text: str
    selected_reason: str
    selected_context_note: str
    analysis: dict
    final_output: str


class QuizAnalysisSnippetStructuredOutput(BaseModel):
    path: str = Field(default="")
    code: str = Field(default="")
    reason: str = Field(default="")


class QuizQuestionPlanItemStructuredOutput(BaseModel):
    type: str = Field(default="")
    focus: str = Field(default="")
    path: str = Field(default="")
    code_hint: str = Field(default="")


class QuizAnalysisStructuredOutput(BaseModel):
    summary_bullets: list[str] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)
    key_snippets: list[QuizAnalysisSnippetStructuredOutput] = Field(default_factory=list)
    learning_objectives: list[str] = Field(default_factory=list)
    question_plan: list[QuizQuestionPlanItemStructuredOutput] = Field(
        default_factory=list
    )
    change_risks: list[str] = Field(default_factory=list)


def last_user_request(messages: list[BaseMessage] | list[dict] | None) -> str:
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, dict):
        return str(last.get("content", ""))
    return str(getattr(last, "content", ""))


def selected_context_note(selected_reason: str, output_kind: str) -> str:
    noun = "읽을거리" if output_kind == "read" else "퀴즈"
    if selected_reason == "fallback_recent_text_commit":
        return (
            "참고: 가장 최근 커밋에는 텍스트 diff가 없어, "
            f"가장 가까운 이전 텍스트 커밋을 기준으로 {noun}를 생성합니다.\n\n"
        )
    if selected_reason == "selected_commit":
        return f"참고: 사용자가 선택한 특정 커밋을 기준으로 {noun}를 생성합니다.\n\n"
    if selected_reason == "selected_commits":
        return f"참고: 사용자가 선택한 여러 커밋의 흐름을 합쳐 {noun}를 생성합니다.\n\n"
    return ""


def resolve_commit_context(state: CommitAnalysisState) -> CommitAnalysisState:
    output_kind = str(state.get("output_kind", "quiz"))
    context = get_latest_commit_context(
        state.get("commit_mode", "auto"),
        state.get("requested_commit_sha"),
        state.get("requested_commit_shas"),
        state.get("repo_source", "local"),
        state.get("github_repo_url"),
    )
    context["selected_context_note"] = selected_context_note(
        context["selected_reason"],
        output_kind,
    )
    return context


def analyze_change(state: CommitAnalysisState) -> CommitAnalysisState:
    output_kind = str(state.get("output_kind", "quiz"))
    if not state["diff_text"]:
        if output_kind == "read":
            message = (
                "최근 커밋에서 읽을거리를 만들 만한 텍스트 diff를 찾지 못했습니다.\n\n"
                f"- 커밋: `{state['commit_subject']}` ({state['commit_sha'][:7]})\n"
                f"- 작성자: {state['commit_author']}\n"
                f"- 날짜: {state['commit_date']}\n\n"
                "현재 변경은 바이너리 파일만 포함하거나 코드 hunk가 없는 상태로 보입니다. "
                "텍스트 코드 변경이 있는 커밋을 지정하거나, 직전 몇 개 커밋을 함께 선택하면 더 유용한 읽을거리를 만들 수 있습니다."
            )
        else:
            message = (
                "최근 커밋에서 퀴즈를 만들 만한 텍스트 diff를 찾지 못했습니다.\n\n"
                f"- 커밋: `{state['commit_subject']}` ({state['commit_sha'][:7]})\n"
                f"- 작성자: {state['commit_author']}\n"
                f"- 날짜: {state['commit_date']}\n\n"
                "현재 변경은 바이너리 파일만 포함하거나 코드 hunk가 없는 상태로 보입니다. "
                "텍스트 코드 변경이 있는 커밋을 지정하거나, 직전 몇 개 커밋을 합쳐서 문제를 만들도록 그래프를 확장하면 더 유용해집니다."
            )
        response = AIMessage(content=message)
        return {
            "analysis": {
                "summary_bullets": [],
                "key_files": [],
                "key_snippets": [],
                "learning_objectives": [],
                "question_plan": [],
                "change_risks": [],
            },
            "final_output": str(response.content),
        }

    analysis_quiz_style = str(
        state.get(
            "analysis_quiz_style",
            "study_session" if output_kind == "read" else "mixed",
        )
    )
    prompt = build_quiz_analysis_prompt(
        user_request=last_user_request(state.get("messages")),
        difficulty=state.get("difficulty", "medium"),
        quiz_style=analysis_quiz_style,
        selected_context_note=state.get("selected_context_note", ""),
        commit_sha=state["commit_sha"],
        commit_subject=state["commit_subject"],
        commit_author=state["commit_author"],
        commit_date=state["commit_date"],
        changed_files_summary=state["changed_files_summary"],
        diff_text=state["diff_text"],
        file_context_text=state["file_context_text"],
    )
    analysis_payload = LLMClient().invoke_structured(prompt, QuizAnalysisStructuredOutput)
    analysis = normalize_quiz_analysis(
        analysis_payload.model_dump()
        if hasattr(analysis_payload, "model_dump")
        else analysis_payload
    )
    return {"analysis": analysis}


commit_analysis_builder = StateGraph(CommitAnalysisState)
commit_analysis_builder.add_node("resolve_commit_context", resolve_commit_context)
commit_analysis_builder.add_node("analyze_change", analyze_change)
commit_analysis_builder.add_edge(START, "resolve_commit_context")
commit_analysis_builder.add_edge("resolve_commit_context", "analyze_change")
commit_analysis_builder.add_edge("analyze_change", END)

commit_analysis_subgraph = commit_analysis_builder.compile(
    name="commit_analysis_subgraph_v1"
)

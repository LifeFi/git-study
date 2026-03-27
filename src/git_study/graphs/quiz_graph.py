import json
from typing import Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph

from ..domain.repo_context import get_latest_commit_context
from ..llm.client import LLMClient
from ..llm.schemas import normalize_quiz_analysis, normalize_quiz_review
from ..prompts.quiz_analysis import build_quiz_analysis_prompt
from ..prompts.quiz_generation import build_quiz_generation_prompt
from ..prompts.quiz_review import build_quiz_review_prompt


class QuizGraphState(TypedDict, total=False):
    messages: list[BaseMessage]
    repo_source: NotRequired[Literal["local", "github"]]
    github_repo_url: NotRequired[str]
    commit_mode: NotRequired[Literal["auto", "latest", "selected"]]
    requested_commit_sha: NotRequired[str]
    requested_commit_shas: NotRequired[list[str]]
    difficulty: NotRequired[str]
    quiz_style: NotRequired[str]
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
    quiz_draft: str
    quiz_review: dict
    final_output: str


def _last_user_request(messages: list[BaseMessage] | list[dict] | None) -> str:
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, dict):
        return str(last.get("content", ""))
    return str(getattr(last, "content", ""))


def _selected_context_note(selected_reason: str) -> str:
    if selected_reason == "fallback_recent_text_commit":
        return (
            "참고: 가장 최근 커밋에는 텍스트 diff가 없어, "
            "가장 가까운 이전 텍스트 커밋을 기준으로 퀴즈를 생성합니다.\n\n"
        )
    if selected_reason == "selected_commit":
        return "참고: 사용자가 선택한 특정 커밋을 기준으로 퀴즈를 생성합니다.\n\n"
    if selected_reason == "selected_commits":
        return "참고: 사용자가 선택한 여러 커밋의 흐름을 합쳐 퀴즈를 생성합니다.\n\n"
    return ""


def resolve_commit_context(state: QuizGraphState) -> QuizGraphState:
    context = get_latest_commit_context(
        state.get("commit_mode", "auto"),
        state.get("requested_commit_sha"),
        state.get("requested_commit_shas"),
        state.get("repo_source", "local"),
        state.get("github_repo_url"),
    )
    context["selected_context_note"] = _selected_context_note(context["selected_reason"])
    return context


def analyze_change(state: QuizGraphState) -> QuizGraphState:
    if not state["diff_text"]:
        response = AIMessage(
            content=(
                "최근 커밋에서 퀴즈를 만들 만한 텍스트 diff를 찾지 못했습니다.\n\n"
                f"- 커밋: `{state['commit_subject']}` ({state['commit_sha'][:7]})\n"
                f"- 작성자: {state['commit_author']}\n"
                f"- 날짜: {state['commit_date']}\n\n"
                "현재 변경은 바이너리 파일만 포함하거나 코드 hunk가 없는 상태로 보입니다. "
                "텍스트 코드 변경이 있는 커밋을 지정하거나, 직전 몇 개 커밋을 합쳐서 문제를 만들도록 그래프를 확장하면 더 유용해집니다."
            )
        )
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

    user_request = _last_user_request(state.get("messages"))
    prompt = build_quiz_analysis_prompt(
        user_request=user_request,
        difficulty=state.get("difficulty", "medium"),
        quiz_style=state.get("quiz_style", "mixed"),
        selected_context_note=state.get("selected_context_note", ""),
        commit_sha=state["commit_sha"],
        commit_subject=state["commit_subject"],
        commit_author=state["commit_author"],
        commit_date=state["commit_date"],
        changed_files_summary=state["changed_files_summary"],
        diff_text=state["diff_text"],
        file_context_text=state["file_context_text"],
    )
    analysis = normalize_quiz_analysis(LLMClient().invoke_json(prompt))
    return {"analysis": analysis}


def draft_quiz(state: QuizGraphState) -> QuizGraphState:
    if state.get("final_output"):
        return {}

    user_request = _last_user_request(state.get("messages"))
    quiz_style = state.get("quiz_style", "mixed")
    output_mode = "study_session" if quiz_style == "study_session" else "quiz"
    prompt = build_quiz_generation_prompt(
        user_request=user_request,
        difficulty=state.get("difficulty", "medium"),
        quiz_style=quiz_style,
        output_mode=output_mode,
        selected_context_note=state.get("selected_context_note", ""),
        commit_sha=state["commit_sha"],
        commit_subject=state["commit_subject"],
        commit_author=state["commit_author"],
        commit_date=state["commit_date"],
        changed_files_summary=state["changed_files_summary"],
        diff_text=state["diff_text"],
        file_context_text=state["file_context_text"],
        analysis_json=json.dumps(state["analysis"], ensure_ascii=False, indent=2),
    )
    return {"quiz_draft": LLMClient().invoke_text(prompt)}


def review_quiz(state: QuizGraphState) -> QuizGraphState:
    if state.get("final_output"):
        return {
            "quiz_review": normalize_quiz_review(
                {"is_valid": True, "issues": [], "revision_instruction": ""}
            )
        }

    review = normalize_quiz_review(
        LLMClient().invoke_json(
            build_quiz_review_prompt(
                quiz_markdown=state["quiz_draft"],
                analysis_json=json.dumps(state["analysis"], ensure_ascii=False, indent=2),
            )
        )
    )
    if review.get("is_valid", True):
        return {"quiz_review": review}

    repaired_prompt = (
        build_quiz_generation_prompt(
            user_request=_last_user_request(state.get("messages")),
            difficulty=state.get("difficulty", "medium"),
            quiz_style=state.get("quiz_style", "mixed"),
            output_mode=(
                "study_session"
                if state.get("quiz_style", "mixed") == "study_session"
                else "quiz"
            ),
            selected_context_note=state.get("selected_context_note", ""),
            commit_sha=state["commit_sha"],
            commit_subject=state["commit_subject"],
            commit_author=state["commit_author"],
            commit_date=state["commit_date"],
            changed_files_summary=state["changed_files_summary"],
            diff_text=state["diff_text"],
            file_context_text=state["file_context_text"],
            analysis_json=json.dumps(state["analysis"], ensure_ascii=False, indent=2),
        )
        + "\n\nAdditional revision instruction:\n"
        + str(review.get("revision_instruction", "")).strip()
    )
    repaired = LLMClient().invoke_text(repaired_prompt)
    return {"quiz_review": review, "quiz_draft": repaired}


def finalize_quiz(state: QuizGraphState) -> QuizGraphState:
    return {"final_output": state.get("final_output") or state.get("quiz_draft", "")}


quiz_graph_builder = StateGraph(QuizGraphState)
quiz_graph_builder.add_node("resolve_commit_context", resolve_commit_context)
quiz_graph_builder.add_node("analyze_change", analyze_change)
quiz_graph_builder.add_node("draft_quiz", draft_quiz)
quiz_graph_builder.add_node("review_quiz", review_quiz)
quiz_graph_builder.add_node("finalize_quiz", finalize_quiz)

quiz_graph_builder.add_edge(START, "resolve_commit_context")
quiz_graph_builder.add_edge("resolve_commit_context", "analyze_change")
quiz_graph_builder.add_edge("analyze_change", "draft_quiz")
quiz_graph_builder.add_edge("draft_quiz", "review_quiz")
quiz_graph_builder.add_edge("review_quiz", "finalize_quiz")
quiz_graph_builder.add_edge("finalize_quiz", END)

quiz_graph = quiz_graph_builder.compile(name="commit_diff_quiz_v2")

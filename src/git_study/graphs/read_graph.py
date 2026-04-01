import json
from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph

from .commit_analysis_subgraph import (
    analyze_change as analyze_commit_change,
    last_user_request,
    resolve_commit_context as resolve_shared_commit_context,
    selected_context_note,
)
from ..llm.client import LLMClient
from ..llm.schemas import normalize_quiz_review
from ..prompts.read_generation import build_read_generation_prompt
from ..prompts.read_review import build_read_review_prompt


class ReadGraphState(TypedDict, total=False):
    messages: list[BaseMessage]
    repo_source: NotRequired[Literal["local", "github"]]
    github_repo_url: NotRequired[str]
    commit_mode: NotRequired[Literal["auto", "latest", "selected"]]
    requested_commit_sha: NotRequired[str]
    requested_commit_shas: NotRequired[list[str]]
    difficulty: NotRequired[str]
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
    reading_draft: str
    reading_review: dict
    final_output: str


class ReadReviewStructuredOutput(BaseModel):
    is_valid: bool = Field(default=False)
    issues: list[str] = Field(default_factory=list)
    revision_instruction: str = Field(default="")


def resolve_commit_context(state: ReadGraphState) -> ReadGraphState:
    # 이미 컨텍스트가 주입된 경우(diff_text 존재) → 외부 fetch 건너뜀
    if state.get("diff_text"):
        return {
            "selected_context_note": selected_context_note(
                state.get("selected_reason", ""),
                "read",
            )
        }
    return resolve_shared_commit_context(
        {
            **state,
            "output_kind": "read",
            "analysis_quiz_style": "study_session",
        }
    )


def analyze_change(state: ReadGraphState) -> ReadGraphState:
    return analyze_commit_change(
        {
            **state,
            "output_kind": "read",
            "analysis_quiz_style": "study_session",
        }
    )


def draft_reading(state: ReadGraphState) -> ReadGraphState:
    if state.get("final_output"):
        return {}

    prompt = build_read_generation_prompt(
        user_request=last_user_request(state.get("messages")),
        difficulty=state.get("difficulty", "medium"),
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
    return {"reading_draft": LLMClient().invoke_text(prompt)}


def review_reading(state: ReadGraphState) -> ReadGraphState:
    if state.get("final_output"):
        return {
            "reading_review": normalize_quiz_review(
                {"is_valid": True, "issues": [], "revision_instruction": ""}
            )
        }

    review_payload = LLMClient().invoke_structured(
        build_read_review_prompt(
            reading_markdown=state["reading_draft"],
            analysis_json=json.dumps(state["analysis"], ensure_ascii=False, indent=2),
        ),
        ReadReviewStructuredOutput,
    )
    review = normalize_quiz_review(
        review_payload.model_dump()
        if hasattr(review_payload, "model_dump")
        else review_payload
    )
    return {"reading_review": review}


def repair_reading(state: ReadGraphState) -> ReadGraphState:
    if state.get("final_output"):
        return {}
    review = state.get("reading_review", {})
    repaired_prompt = (
        build_read_generation_prompt(
            user_request=last_user_request(state.get("messages")),
            difficulty=state.get("difficulty", "medium"),
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
    return {
        "reading_draft": repaired,
        "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
    }


def route_after_read_review(state: ReadGraphState) -> str:
    if state.get("final_output"):
        return "finalize"
    review = state.get("reading_review", {})
    if review.get("is_valid", True):
        return "finalize"
    if int(state.get("repair_attempts", 0)) >= 1:
        return "finalize"
    return "repair"


def finalize_reading(state: ReadGraphState) -> ReadGraphState:
    return {"final_output": state.get("final_output") or state.get("reading_draft", "")}


read_graph_builder = StateGraph(ReadGraphState)
read_graph_builder.add_node("resolve_commit_context", resolve_commit_context)
read_graph_builder.add_node("analyze_change", analyze_change)
read_graph_builder.add_node("draft_reading", draft_reading)
read_graph_builder.add_node("review_reading", review_reading)
read_graph_builder.add_node("repair_reading", repair_reading)
read_graph_builder.add_node("finalize_reading", finalize_reading)

read_graph_builder.add_edge(START, "resolve_commit_context")
read_graph_builder.add_edge("resolve_commit_context", "analyze_change")
read_graph_builder.add_edge("analyze_change", "draft_reading")
read_graph_builder.add_edge("draft_reading", "review_reading")
read_graph_builder.add_conditional_edges(
    "review_reading",
    route_after_read_review,
    {
        "repair": "repair_reading",
        "finalize": "finalize_reading",
    },
)
read_graph_builder.add_edge("repair_reading", "review_reading")
read_graph_builder.add_edge("finalize_reading", END)

read_graph = read_graph_builder.compile(name="commit_diff_reading_v1")

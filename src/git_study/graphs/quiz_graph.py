import json
from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field, RootModel
from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph

from .commit_analysis_subgraph import (
    analyze_change as analyze_commit_change,
    last_user_request,
    resolve_commit_context as resolve_shared_commit_context,
)
from ..domain.general_quiz import (
    complete_general_quiz_questions,
    render_general_quiz_markdown,
)
from ..llm.client import LLMClient
from ..llm.schemas import (
    normalize_general_questions,
    normalize_quiz_review,
)
from ..prompts.quiz_generation import build_quiz_generation_prompt
from ..prompts.quiz_review import build_quiz_review_prompt
from ..types import GeneralQuizQuestion


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
    quiz_questions: list[GeneralQuizQuestion]
    quiz_review: dict
    final_output: str


class QuizReviewStructuredOutput(BaseModel):
    is_valid: bool = Field(default=False)
    issues: list[str] = Field(default_factory=list)
    revision_instruction: str = Field(default="")


class GeneralQuestionStructuredOutput(BaseModel):
    id: str = Field(default="")
    question: str = Field(default="")
    expected_answer: str = Field(default="")
    question_type: str = Field(default="intent")
    explanation: str = Field(default="")
    code_snippet: str = Field(default="")
    code_language: str = Field(default="text")
    code_reference: str = Field(default="")
    choices: list[str] = Field(default_factory=list)


class GeneralQuestionListStructuredOutput(RootModel[list[GeneralQuestionStructuredOutput]]):
    root: list[GeneralQuestionStructuredOutput] = Field(default_factory=list)


def resolve_commit_context(state: QuizGraphState) -> QuizGraphState:
    return resolve_shared_commit_context(
        {
            **state,
            "output_kind": "quiz",
            "analysis_quiz_style": state.get("quiz_style", "mixed"),
        }
    )


def analyze_change(state: QuizGraphState) -> QuizGraphState:
    return analyze_commit_change(
        {
            **state,
            "output_kind": "quiz",
            "analysis_quiz_style": state.get("quiz_style", "mixed"),
        }
    )


def draft_quiz(state: QuizGraphState) -> QuizGraphState:
    if state.get("final_output"):
        return {}

    user_request = last_user_request(state.get("messages"))
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
    questions_payload = LLMClient().invoke_structured(
        prompt,
        GeneralQuestionListStructuredOutput,
    )
    questions = normalize_general_questions(
        questions_payload.model_dump()
        if hasattr(questions_payload, "model_dump")
        else questions_payload
    )
    return {"quiz_questions": questions}


def review_quiz(state: QuizGraphState) -> QuizGraphState:
    if state.get("final_output"):
        return {
            "quiz_review": normalize_quiz_review(
                {"is_valid": True, "issues": [], "revision_instruction": ""}
            )
        }

    review_prompt = build_quiz_review_prompt(
        quiz_questions_json=json.dumps(
            state.get("quiz_questions", []), ensure_ascii=False, indent=2
        ),
        analysis_json=json.dumps(state["analysis"], ensure_ascii=False, indent=2),
        user_request=last_user_request(state.get("messages")),
    )
    review_payload = LLMClient().invoke_structured(
        review_prompt,
        QuizReviewStructuredOutput,
    )
    review = normalize_quiz_review(
        review_payload.model_dump()
        if hasattr(review_payload, "model_dump")
        else review_payload
    )
    return {"quiz_review": review}


def repair_quiz(state: QuizGraphState) -> QuizGraphState:
    if state.get("final_output"):
        return {}
    review = state.get("quiz_review", {})
    repaired_prompt = (
        build_quiz_generation_prompt(
            user_request=last_user_request(state.get("messages")),
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
    repaired_payload = LLMClient().invoke_structured(
        repaired_prompt,
        GeneralQuestionListStructuredOutput,
    )
    repaired = normalize_general_questions(
        repaired_payload.model_dump()
        if hasattr(repaired_payload, "model_dump")
        else repaired_payload
    )
    return {
        "quiz_questions": repaired,
        "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
    }


def route_after_quiz_review(state: QuizGraphState) -> str:
    if state.get("final_output"):
        return "finalize"
    review = state.get("quiz_review", {})
    if review.get("is_valid", True):
        return "finalize"
    if int(state.get("repair_attempts", 0)) >= 1:
        return "finalize"
    return "repair"


def finalize_quiz(state: QuizGraphState) -> QuizGraphState:
    if state.get("final_output"):
        return {"final_output": state["final_output"]}
    completed_questions = complete_general_quiz_questions(
        analysis=state.get("analysis", {}),
        questions=list(state.get("quiz_questions", [])),
    )
    return {
        "quiz_questions": completed_questions,
        "final_output": render_general_quiz_markdown(
            analysis=state.get("analysis", {}),
            questions=completed_questions,
        ),
    }


quiz_graph_builder = StateGraph(QuizGraphState)
quiz_graph_builder.add_node("resolve_commit_context", resolve_commit_context)
quiz_graph_builder.add_node("analyze_change", analyze_change)
quiz_graph_builder.add_node("draft_quiz", draft_quiz)
quiz_graph_builder.add_node("review_quiz", review_quiz)
quiz_graph_builder.add_node("repair_quiz", repair_quiz)
quiz_graph_builder.add_node("finalize_quiz", finalize_quiz)

quiz_graph_builder.add_edge(START, "resolve_commit_context")
quiz_graph_builder.add_edge("resolve_commit_context", "analyze_change")
quiz_graph_builder.add_edge("analyze_change", "draft_quiz")
quiz_graph_builder.add_edge("draft_quiz", "review_quiz")
quiz_graph_builder.add_conditional_edges(
    "review_quiz",
    route_after_quiz_review,
    {
        "repair": "repair_quiz",
        "finalize": "finalize_quiz",
    },
)
quiz_graph_builder.add_edge("repair_quiz", "review_quiz")
quiz_graph_builder.add_edge("finalize_quiz", END)

quiz_graph = quiz_graph_builder.compile(name="commit_diff_quiz_v2")

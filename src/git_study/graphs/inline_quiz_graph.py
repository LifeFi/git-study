import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, RootModel

from ..domain.inline_anchor import (
    extract_file_paths_from_summary,
    parse_file_context_blocks,
)
from ..llm.client import LLMClient
from ..llm.schemas import (
    normalize_inline_anchor_candidates,
    normalize_inline_questions,
    normalize_quiz_review,
)
from ..prompts.inline_anchor import build_inline_anchor_prompt
from ..prompts.inline_question import build_inline_question_prompt
from ..prompts.inline_question_review import build_inline_question_review_prompt
from ..tools.code_context import build_get_neighbor_code_context_tool
from ..types import InlineQuizQuestion


class InlineQuizGraphState(TypedDict, total=False):
    commit_context: dict[str, Any]
    count: int
    user_request: str
    actual_paths: list[str]
    file_context_map: dict[str, str]
    anchor_candidates: list[dict[str, str]]
    validated_anchors: list[dict[str, str]]
    inline_questions: list[InlineQuizQuestion]
    inline_review: dict
    repair_attempts: int


class InlineAnchorCandidateStructuredOutput(BaseModel):
    file_path: str = Field(default="")
    anchor_line: int = Field(default=0)
    anchor_snippet: str = Field(default="")
    question_type: str = Field(default="intent")
    reason: str = Field(default="")


class InlineAnchorCandidateListStructuredOutput(
    RootModel[list[InlineAnchorCandidateStructuredOutput]]
):
    root: list[InlineAnchorCandidateStructuredOutput] = Field(default_factory=list)


class InlineQuestionStructuredOutput(BaseModel):
    id: str = Field(default="")
    file_path: str = Field(default="")
    anchor_line: int = Field(default=0)
    anchor_snippet: str = Field(default="")
    question: str = Field(default="")
    expected_answer: str = Field(default="")
    question_type: str = Field(default="intent")


class InlineQuestionListStructuredOutput(RootModel[list[InlineQuestionStructuredOutput]]):
    root: list[InlineQuestionStructuredOutput] = Field(default_factory=list)


class InlineQuestionReviewStructuredOutput(BaseModel):
    is_valid: bool = Field(default=False)
    issues: list[str] = Field(default_factory=list)
    revision_instruction: str = Field(default="")


TOOL_USAGE_SUFFIX = """

Tooling:
- You have access to `get_neighbor_code_context`.
- Before returning the final JSON, call `get_neighbor_code_context` at least once for one of the validated anchors.
- Pass the anchor_line from a validated anchor to inspect surrounding code.
- Use the returned neighboring code only to improve reasoning quality. Final `file_path` and `anchor_line` must still match the validated anchors exactly.
""".strip()


def prepare_inline_context(state: InlineQuizGraphState) -> InlineQuizGraphState:
    commit_context = state["commit_context"]
    actual_paths = extract_file_paths_from_summary(
        commit_context.get("changed_files_summary", "")
    )
    file_context_map = parse_file_context_blocks(commit_context.get("file_context_text", ""))
    return {
        "actual_paths": actual_paths,
        "file_context_map": file_context_map,
    }


def extract_anchor_candidates(state: InlineQuizGraphState) -> InlineQuizGraphState:
    commit_context = state["commit_context"]
    prompt = build_inline_anchor_prompt(
        commit_sha=str(commit_context.get("commit_sha", ""))[:7],
        commit_subject=commit_context.get("commit_subject", ""),
        changed_files_summary=commit_context.get("changed_files_summary", ""),
        diff_text=commit_context.get("diff_text", ""),
        file_context_text=commit_context.get("file_context_text", ""),
        count=state.get("count", 4),
        actual_paths=state.get("actual_paths", []),
    )
    anchor_payload = LLMClient().invoke_structured(
        prompt,
        InlineAnchorCandidateListStructuredOutput,
    )
    anchor_candidates = normalize_inline_anchor_candidates(
        anchor_payload.model_dump()
        if hasattr(anchor_payload, "model_dump")
        else anchor_payload
    )
    return {"anchor_candidates": anchor_candidates}


def validate_anchor_candidates(state: InlineQuizGraphState) -> InlineQuizGraphState:
    actual_paths = set(state.get("actual_paths", []))
    file_context_map = state.get("file_context_map", {})
    validated: list[dict] = []
    seen_pairs: set[tuple[str, int]] = set()

    for candidate in state.get("anchor_candidates", []):
        file_path = str(candidate.get("file_path", "")).strip()
        try:
            anchor_line = int(candidate.get("anchor_line", 0))
        except (TypeError, ValueError):
            continue
        question_type = str(candidate.get("question_type", "intent")).strip() or "intent"
        reason = str(candidate.get("reason", "")).strip()
        if file_path not in actual_paths:
            continue
        content = file_context_map.get(file_path, "")
        line_count = len(content.splitlines()) if content else 0
        if anchor_line < 1 or anchor_line > line_count:
            continue
        pair = (file_path, anchor_line)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        validated.append(
            {
                "file_path": file_path,
                "anchor_line": anchor_line,
                "anchor_snippet": str(candidate.get("anchor_snippet", "")).strip(),
                "question_type": question_type,
                "reason": reason,
            }
        )
        if len(validated) >= max(state.get("count", 4), 4):
            break

    if not validated:
        for file_path in state.get("actual_paths", []):
            content = file_context_map.get(file_path, "")
            if not content or len(content.splitlines()) < 3:
                continue
            validated.append(
                {
                    "file_path": file_path,
                    "anchor_line": 1,
                    "anchor_snippet": "",
                    "question_type": "intent",
                    "reason": "파일 상단을 fallback anchor로 사용",
                }
            )
            if len(validated) >= max(state.get("count", 4), 4):
                break

    return {"validated_anchors": validated}


def generate_inline_questions(state: InlineQuizGraphState) -> InlineQuizGraphState:
    commit_context = state["commit_context"]
    count = state.get("count", 4)
    prompt = build_inline_question_prompt(
        commit_sha=str(commit_context.get("commit_sha", ""))[:7],
        commit_subject=commit_context.get("commit_subject", ""),
        diff_text=commit_context.get("diff_text", ""),
        file_context_text=commit_context.get("file_context_text", ""),
        anchor_candidates_json=json.dumps(
            state.get("validated_anchors", []), ensure_ascii=False, indent=2
        ),
        count=count,
        user_request=str(state.get("user_request", "")).strip(),
    )
    tool = build_get_neighbor_code_context_tool(state.get("file_context_map", {}))
    items_payload = LLMClient().invoke_json_with_tools(
        f"{prompt}\n\n{TOOL_USAGE_SUFFIX}",
        [tool],
        require_tool=True,
    )
    items = normalize_inline_questions(
        items_payload.model_dump()
        if hasattr(items_payload, "model_dump")
        else items_payload
    )
    questions: list[InlineQuizQuestion] = []
    validated_pairs = {
        (item["file_path"], int(item.get("anchor_line", 0))): item
        for item in state.get("validated_anchors", [])
    }
    for index, item in enumerate(items):
        file_path = str(item.get("file_path", ""))
        try:
            anchor_line = int(item.get("anchor_line", 0))
        except (TypeError, ValueError):
            anchor_line = 0
        fallback = validated_pairs.get((file_path, anchor_line))
        if fallback is None:
            if index < len(state.get("validated_anchors", [])):
                fallback = state["validated_anchors"][index]
            elif state.get("validated_anchors"):
                fallback = state["validated_anchors"][0]
        if fallback is None:
            continue
        questions.append(
            InlineQuizQuestion(
                id=str(item.get("id", f"q{len(questions) + 1}")),
                file_path=fallback["file_path"],
                anchor_line=int(fallback.get("anchor_line", 1)),
                anchor_snippet=fallback.get("anchor_snippet", ""),
                question=str(item.get("question", "")).strip(),
                expected_answer=str(item.get("expected_answer", "")).strip(),
                question_type=str(
                    item.get("question_type", fallback.get("question_type", "intent"))
                ).strip()
                or "intent",
            )
        )
        if len(questions) >= count:
            break
    return {"inline_questions": questions}


def review_inline_questions(state: InlineQuizGraphState) -> InlineQuizGraphState:
    review_payload = LLMClient().invoke_structured(
        build_inline_question_review_prompt(
            inline_questions_json=json.dumps(
                state.get("inline_questions", []), ensure_ascii=False, indent=2
            ),
            validated_anchors_json=json.dumps(
                state.get("validated_anchors", []), ensure_ascii=False, indent=2
            ),
            user_request=str(state.get("user_request", "")).strip(),
        ),
        InlineQuestionReviewStructuredOutput,
    )
    review_result = normalize_quiz_review(
        review_payload.model_dump()
        if hasattr(review_payload, "model_dump")
        else review_payload
    )
    return {"inline_review": review_result}


def repair_inline_questions(state: InlineQuizGraphState) -> InlineQuizGraphState:
    commit_context = state["commit_context"]
    count = state.get("count", 4)
    repaired_prompt = (
        build_inline_question_prompt(
            commit_sha=str(commit_context.get("commit_sha", ""))[:7],
            commit_subject=commit_context.get("commit_subject", ""),
            diff_text=commit_context.get("diff_text", ""),
            file_context_text=commit_context.get("file_context_text", ""),
            anchor_candidates_json=json.dumps(
                state.get("validated_anchors", []), ensure_ascii=False, indent=2
            ),
            count=count,
            user_request=str(state.get("user_request", "")).strip(),
        )
        + "\n\nAdditional revision instruction:\n"
        + str(state.get("inline_review", {}).get("revision_instruction", "")).strip()
    )
    tool = build_get_neighbor_code_context_tool(state.get("file_context_map", {}))
    items_payload = LLMClient().invoke_json_with_tools(
        f"{repaired_prompt}\n\n{TOOL_USAGE_SUFFIX}",
        [tool],
        require_tool=True,
    )
    items = normalize_inline_questions(
        items_payload.model_dump()
        if hasattr(items_payload, "model_dump")
        else items_payload
    )
    questions: list[InlineQuizQuestion] = []
    validated_pairs = {
        (item["file_path"], int(item.get("anchor_line", 0))): item
        for item in state.get("validated_anchors", [])
    }
    for index, item in enumerate(items):
        file_path = str(item.get("file_path", ""))
        try:
            anchor_line = int(item.get("anchor_line", 0))
        except (TypeError, ValueError):
            anchor_line = 0
        fallback = validated_pairs.get((file_path, anchor_line))
        if fallback is None:
            if index < len(state.get("validated_anchors", [])):
                fallback = state["validated_anchors"][index]
            elif state.get("validated_anchors"):
                fallback = state["validated_anchors"][0]
        if fallback is None:
            continue
        questions.append(
            InlineQuizQuestion(
                id=str(item.get("id", f"q{len(questions) + 1}")),
                file_path=fallback["file_path"],
                anchor_line=int(fallback.get("anchor_line", 1)),
                anchor_snippet=fallback.get("anchor_snippet", ""),
                question=str(item.get("question", "")).strip(),
                expected_answer=str(item.get("expected_answer", "")).strip(),
                question_type=str(
                    item.get("question_type", fallback.get("question_type", "intent"))
                ).strip()
                or "intent",
            )
        )
        if len(questions) >= count:
            break
    return {
        "inline_questions": questions,
        "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
    }


def route_after_inline_review(state: InlineQuizGraphState) -> str:
    review = state.get("inline_review", {})
    if review.get("is_valid", True):
        return "finalize"
    if int(state.get("repair_attempts", 0)) >= 1:
        return "finalize"
    return "repair"


def finalize_inline_questions(state: InlineQuizGraphState) -> InlineQuizGraphState:
    questions = state.get("inline_questions", [])
    if questions:
        return {"inline_questions": questions}

    fallback_questions: list[InlineQuizQuestion] = []
    for index, item in enumerate(state.get("validated_anchors", []), start=1):
        fallback_questions.append(
            InlineQuizQuestion(
                id=f"q{index}",
                file_path=item["file_path"],
                anchor_line=int(item.get("anchor_line", 1)),
                anchor_snippet=item.get("anchor_snippet", ""),
                question="이 코드 조각이 이번 변경에서 맡는 역할을 설명해 주세요.",
                expected_answer="이 코드가 변경의 의도와 동작에 어떤 영향을 주는지, 주변 흐름과 함께 설명해야 합니다.",
                question_type=item.get("question_type", "intent"),
            )
        )
        if len(fallback_questions) >= state.get("count", 4):
            break
    return {"inline_questions": fallback_questions}


inline_quiz_graph_builder = StateGraph(InlineQuizGraphState)
inline_quiz_graph_builder.add_node("prepare_inline_context", prepare_inline_context)
inline_quiz_graph_builder.add_node("extract_anchor_candidates", extract_anchor_candidates)
inline_quiz_graph_builder.add_node("validate_anchor_candidates", validate_anchor_candidates)
inline_quiz_graph_builder.add_node("generate_inline_questions", generate_inline_questions)
inline_quiz_graph_builder.add_node("review_inline_questions", review_inline_questions)
inline_quiz_graph_builder.add_node("repair_inline_questions", repair_inline_questions)
inline_quiz_graph_builder.add_node("finalize_inline_questions", finalize_inline_questions)

inline_quiz_graph_builder.add_edge(START, "prepare_inline_context")
inline_quiz_graph_builder.add_edge("prepare_inline_context", "extract_anchor_candidates")
inline_quiz_graph_builder.add_edge("extract_anchor_candidates", "validate_anchor_candidates")
inline_quiz_graph_builder.add_edge("validate_anchor_candidates", "generate_inline_questions")
inline_quiz_graph_builder.add_edge("generate_inline_questions", "review_inline_questions")
inline_quiz_graph_builder.add_conditional_edges(
    "review_inline_questions",
    route_after_inline_review,
    {
        "repair": "repair_inline_questions",
        "finalize": "finalize_inline_questions",
    },
)
inline_quiz_graph_builder.add_edge("repair_inline_questions", "review_inline_questions")
inline_quiz_graph_builder.add_edge("finalize_inline_questions", END)

inline_quiz_graph = inline_quiz_graph_builder.compile(name="inline_quiz_questions_v2")

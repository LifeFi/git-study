import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..domain.inline_anchor import (
    extract_file_paths_from_summary,
    parse_file_context_blocks,
    snippet_exists_in_content,
)
from ..llm.client import LLMClient
from ..llm.schemas import normalize_inline_anchor_candidates, normalize_inline_questions
from ..prompts.inline_anchor import build_inline_anchor_prompt
from ..prompts.inline_question import build_inline_question_prompt
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
    anchor_candidates = normalize_inline_anchor_candidates(LLMClient().invoke_json(prompt))
    return {"anchor_candidates": anchor_candidates}


def validate_anchor_candidates(state: InlineQuizGraphState) -> InlineQuizGraphState:
    actual_paths = set(state.get("actual_paths", []))
    file_context_map = state.get("file_context_map", {})
    validated: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for candidate in state.get("anchor_candidates", []):
        file_path = str(candidate.get("file_path", "")).strip()
        anchor_snippet = str(candidate.get("anchor_snippet", "")).strip()
        question_type = str(candidate.get("question_type", "intent")).strip() or "intent"
        reason = str(candidate.get("reason", "")).strip()
        if file_path not in actual_paths:
            continue
        content = file_context_map.get(file_path, "")
        if not content or not snippet_exists_in_content(content, anchor_snippet):
            continue
        pair = (file_path, anchor_snippet)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        validated.append(
            {
                "file_path": file_path,
                "anchor_snippet": anchor_snippet,
                "question_type": question_type,
                "reason": reason,
            }
        )
        if len(validated) >= max(state.get("count", 4), 4):
            break

    if not validated:
        for file_path in state.get("actual_paths", []):
            content = file_context_map.get(file_path, "")
            lines = [line for line in content.splitlines() if line.strip()]
            if len(lines) < 3:
                continue
            snippet = "\n".join(lines[: min(5, len(lines))])
            validated.append(
                {
                    "file_path": file_path,
                    "anchor_snippet": snippet,
                    "question_type": "intent",
                    "reason": "파일 상단의 핵심 문맥을 fallback anchor로 사용",
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
    items = normalize_inline_questions(LLMClient().invoke_json(prompt))
    questions: list[InlineQuizQuestion] = []
    validated_pairs = {
        (item["file_path"], item["anchor_snippet"]): item
        for item in state.get("validated_anchors", [])
    }
    for index, item in enumerate(items):
        file_path = str(item.get("file_path", ""))
        anchor_snippet = str(item.get("anchor_snippet", ""))
        fallback = validated_pairs.get((file_path, anchor_snippet))
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
                anchor_snippet=fallback["anchor_snippet"],
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
                anchor_snippet=item["anchor_snippet"],
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
inline_quiz_graph_builder.add_node("finalize_inline_questions", finalize_inline_questions)

inline_quiz_graph_builder.add_edge(START, "prepare_inline_context")
inline_quiz_graph_builder.add_edge("prepare_inline_context", "extract_anchor_candidates")
inline_quiz_graph_builder.add_edge("extract_anchor_candidates", "validate_anchor_candidates")
inline_quiz_graph_builder.add_edge("validate_anchor_candidates", "generate_inline_questions")
inline_quiz_graph_builder.add_edge("generate_inline_questions", "finalize_inline_questions")
inline_quiz_graph_builder.add_edge("finalize_inline_questions", END)

inline_quiz_graph = inline_quiz_graph_builder.compile(name="inline_quiz_questions_v2")

from ..types import GeneralQuizQuestion


QUESTION_TYPE_LABELS = {
    "intent": "의도",
    "behavior": "동작",
    "tradeoff": "트레이드오프",
    "vulnerability": "취약점/위험",
}


def render_general_quiz_markdown(
    *,
    analysis: dict,
    questions: list[GeneralQuizQuestion],
) -> str:
    lines = ["## 변경 개요", ""]
    summary_bullets = list(analysis.get("summary_bullets", []))
    if summary_bullets:
        lines.extend(f"- {bullet}" for bullet in summary_bullets[:5])
    else:
        lines.append("- 이번 변경의 전체 흐름을 먼저 요약해 보세요.")

    lines.extend(["", "## 먼저 볼 코드", ""])
    snippets = list(analysis.get("key_snippets", []))
    if snippets:
        for snippet in snippets[:4]:
            path = str(snippet.get("path", "")).strip()
            reason = str(snippet.get("reason", "")).strip()
            code = str(snippet.get("code", "")).strip()
            lines.append(f"### `{path or 'reference'}`")
            if reason:
                lines.append(reason)
            lines.append("```")
            lines.append(code or "// snippet unavailable")
            lines.append("```")
            lines.append("")
    else:
        lines.extend(
            [
                "핵심 코드 스니펫을 자동으로 추리지 못했습니다.",
                "",
            ]
        )

    lines.extend(["## 학습 질문", ""])
    for index, question in enumerate(questions, start=1):
        question_type = str(question.get("question_type", "intent"))
        type_label = QUESTION_TYPE_LABELS.get(question_type, question_type)
        lines.append(f"### 질문 {index}")
        lines.append("")
        lines.append(f"_유형: {type_label}_")
        lines.append("")
        lines.append("핵심 코드")
        lines.append(f"```{str(question.get('code_language', 'text')).strip() or 'text'}")
        lines.append(str(question.get("code_snippet", "")).strip() or "// code unavailable")
        lines.append("```")
        if question.get("choices"):
            lines.append("선택지")
            lines.extend(f"- {choice}" for choice in question["choices"])
        lines.append("질문")
        lines.append(str(question.get("question", "")).strip())
        lines.append("정답")
        lines.append(str(question.get("expected_answer", "")).strip())
        lines.append("해설")
        lines.append(str(question.get("explanation", "")).strip())
        lines.append("코드 근거")
        lines.append(str(question.get("code_reference", "")).strip() or "-")
        lines.append("")

    lines.extend(["## 이 변화에서 배울 점", ""])
    learning_points = list(analysis.get("learning_objectives", [])) or list(
        analysis.get("change_risks", [])
    )
    if learning_points:
        lines.extend(f"- {item}" for item in learning_points[:3])
    else:
        lines.append("- 변경 의도, 실제 동작 변화, 회귀 위험을 함께 연결해 보세요.")

    return "\n".join(lines).strip()


def complete_general_quiz_questions(
    *,
    analysis: dict,
    questions: list[GeneralQuizQuestion],
) -> list[GeneralQuizQuestion]:
    completed: list[GeneralQuizQuestion] = []
    seen_types: set[str] = set()

    for question in questions:
        question_type = str(question.get("question_type", "intent")).strip() or "intent"
        if question_type in seen_types and len(completed) >= 4:
            continue
        seen_types.add(question_type)
        completed.append(question)
        if len(completed) >= 4:
            return completed

    plan_by_type = {
        str(item.get("type", "")).strip(): item
        for item in analysis.get("question_plan", [])
        if isinstance(item, dict)
    }
    snippets = list(analysis.get("key_snippets", []))

    for question_type in ("intent", "behavior", "tradeoff", "vulnerability"):
        if question_type in seen_types:
            continue
        plan = plan_by_type.get(question_type, {})
        snippet = next(
            (
                item
                for item in snippets
                if str(item.get("path", "")).strip() == str(plan.get("path", "")).strip()
            ),
            snippets[0] if snippets else {},
        )
        focus = str(plan.get("focus", "")).strip()
        code_hint = str(plan.get("code_hint", "")).strip()
        path = str(plan.get("path", "")).strip() or str(snippet.get("path", "")).strip()
        completed.append(
            {
                "id": f"q{len(completed) + 1}",
                "question_type": question_type,
                "question": _fallback_question_text(question_type, focus),
                "expected_answer": _fallback_expected_answer(question_type, focus),
                "explanation": _fallback_explanation(question_type, focus),
                "code_snippet": str(snippet.get("code", "")).strip() or code_hint,
                "code_language": "text",
                "code_reference": path,
                "choices": [],
            }
        )
        if len(completed) >= 4:
            break

    for index, question in enumerate(completed, start=1):
        question["id"] = f"q{index}"
    return completed[:4]


def _fallback_question_text(question_type: str, focus: str) -> str:
    if question_type == "behavior":
        return f"이번 수정 이후 동작이 이전과 어떻게 달라졌는지 설명해 주세요. {focus}".strip()
    if question_type == "tradeoff":
        return f"이번 변경의 주요 위험이나 트레이드오프는 무엇인가요? {focus}".strip()
    if question_type == "vulnerability":
        return f"이번 변경에서 주의해야 할 취약점이나 실패 가능성은 무엇인가요? {focus}".strip()
    return f"이번 변경의 핵심 의도는 무엇인가요? {focus}".strip()


def _fallback_expected_answer(question_type: str, focus: str) -> str:
    if question_type == "behavior":
        return "변경 전후의 실행 결과나 호출 흐름 차이를 구체적으로 설명해야 합니다."
    if question_type == "tradeoff":
        return "회귀 위험, 복잡도 증가, 설계 상충점 중 중요한 지점을 코드 근거와 함께 설명해야 합니다."
    if question_type == "vulnerability":
        return "입력 경계, 예외 처리, 보안 취약점, 실패 시나리오 중 핵심 위험을 코드 근거와 함께 설명해야 합니다."
    return "변경이 해결하려는 문제와 설계 의도를 코드 근거와 함께 설명해야 합니다."


def _fallback_explanation(question_type: str, focus: str) -> str:
    base = {
        "behavior": "동작 질문은 실제 결과가 무엇이 달라졌는지까지 연결해야 합니다.",
        "tradeoff": "트레이드오프 질문은 장점뿐 아니라 숨은 비용이나 복잡도 증가까지 봐야 합니다.",
        "vulnerability": "취약점 질문은 실패 조건과 방어가 충분한지 함께 확인해야 합니다.",
    }.get(question_type, "의도 질문은 왜 이 설계가 필요한지까지 설명해야 합니다.")
    return f"{base} {focus}".strip()

import re


QUESTION_SECTION_RE = re.compile(
    r"^###\s*질문\s*(?P<number>\d+)\s*$", re.MULTILINE
)


def parse_quiz_markdown_questions(markdown: str) -> list[dict[str, str]]:
    matches = list(QUESTION_SECTION_RE.finditer(markdown))
    questions: list[dict[str, str]] = []
    for index, match in enumerate(matches, start=1):
        start = match.end()
        end = matches[index].start() if index < len(matches) else len(markdown)
        block = markdown[start:end].strip()
        if not block:
            continue

        question_text = _extract_section_value(block, "질문")
        expected_answer = _extract_section_value(block, "정답")
        explanation = _extract_section_value(block, "해설")
        if not question_text:
            continue

        questions.append(
            {
                "id": f"q{index}",
                "question": question_text,
                "expected_answer": expected_answer,
                "question_type": _infer_question_type(question_text),
                "explanation": explanation,
            }
        )
    return questions


def _extract_section_value(block: str, heading: str) -> str:
    lines = block.splitlines()
    capture = False
    captured: list[str] = []
    stop_headings = {"핵심 코드", "질문", "정답", "해설", "코드 근거"}

    for line in lines:
        stripped = line.strip()
        if capture and stripped in stop_headings:
            break
        if capture:
            captured.append(line)
            continue
        if stripped == heading:
            capture = True

    return "\n".join(captured).strip()


def _infer_question_type(question_text: str) -> str:
    lowered = question_text.lower()
    if any(token in lowered for token in ("위험", "리스크", "트레이드오프", "trade-off")):
        return "tradeoff"
    if any(token in lowered for token in ("취약", "보안", "예외", "실패", "vulnerability")):
        return "vulnerability"
    if any(token in lowered for token in ("동작", "변화", "영향", "behavior")):
        return "behavior"
    return "intent"

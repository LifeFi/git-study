from git_study.domain.quiz_parsing import parse_quiz_markdown_questions


def test_parse_quiz_markdown_questions_extracts_question_answer_and_explanation() -> None:
    markdown = """
## 학습 질문

### 질문 1
핵심 코드
```py
def run():
    return 1
```
질문
이 함수가 이번 변경에서 맡는 역할은 무엇인가요?
정답
핵심 흐름을 시작하고 결과를 반환합니다.
해설
변경 이후 호출 경로의 시작점이라 중요합니다.
코드 근거
run()이 직접 호출됩니다.

### 질문 2
질문
이 변경의 주요 위험은 무엇인가요?
정답
예외 처리가 누락되면 회귀 위험이 있습니다.
해설
에러 케이스가 추가되었기 때문입니다.
""".strip()

    questions = parse_quiz_markdown_questions(markdown)

    assert len(questions) == 2
    assert questions[0]["id"] == "q1"
    assert "맡는 역할" in questions[0]["question"]
    assert "핵심 흐름" in questions[0]["expected_answer"]
    assert "호출 경로" in questions[0]["explanation"]
    assert questions[0]["question_type"] == "intent"
    assert questions[1]["question_type"] == "tradeoff"

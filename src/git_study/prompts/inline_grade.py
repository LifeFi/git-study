def build_inline_grade_prompt(*, question_blocks: str, user_request: str = "") -> str:
    request_block = (
        f"\n추가 요청:\n{user_request.strip()}\n" if user_request.strip() else ""
    )
    return f"""다음 코드 퀴즈 답변들을 채점해주세요.

{question_blocks}
{request_block}

각 답변에 대해 0-100점 채점과 한국어 피드백을 작성해주세요.
피드백은 모범 답안과 비교해서 잘한 점과 부족한 점을 구체적으로 써주세요 (2-4문장).
"improvement" 타입 질문은 단일 정답이 없으므로, 제안한 대안의 구체성과 근거의 타당성을 중심으로 채점해주세요.

ONLY respond with a raw JSON array (no markdown):
[
  {{"id": "q1", "score": 80, "feedback": "피드백 내용"}},
  ...
]
""".strip()

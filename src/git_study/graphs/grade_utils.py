"""Shared utility for grade finalization across inline and general grade graphs."""

from __future__ import annotations


def build_final_grades(
    source_items: list[dict],
    expected_ids: list[str],
    questions: list[dict],
    default_feedback: str = "채점 결과가 누락되어 기본값으로 처리했습니다.",
) -> list[dict]:
    """source_items(raw 또는 normalized grades)를 expected_ids 순서로 정리.

    Returns plain dicts with keys: id, score, feedback.
    Caller is responsible for converting to typed dicts (e.g. InlineQuizGrade).
    """
    seen_ids: set[str] = set()
    final: list[dict] = []

    for item in source_items:
        grade_id = str(item.get("id", "")).strip()
        if not grade_id or grade_id in seen_ids or grade_id not in expected_ids:
            continue
        seen_ids.add(grade_id)
        try:
            score = int(item.get("score", 0))
        except Exception:
            score = 0
        feedback = str(item.get("feedback", "")).strip() or "피드백이 충분히 생성되지 않았습니다."
        final.append({"id": grade_id, "score": max(0, min(100, score)), "feedback": feedback})

    # 매핑 실패 시 질문 순서로 fallback
    if not final and source_items:
        for question, item in zip(questions, source_items):
            q_id = str(question.get("id", ""))
            try:
                score = int(item.get("score", 0))
            except Exception:
                score = 0
            feedback = str(item.get("feedback", "")).strip() or "피드백이 충분히 생성되지 않았습니다."
            final.append({"id": q_id, "score": max(0, min(100, score)), "feedback": feedback})

    # expected_ids 순서로 정렬 + 누락 채우기
    grade_map = {g["id"]: g for g in final}
    completed: list[dict] = []
    for question in questions:
        q_id = str(question.get("id", ""))
        grade = grade_map.get(q_id)
        completed.append(grade if grade is not None else {"id": q_id, "score": 0, "feedback": default_feedback})
    return completed

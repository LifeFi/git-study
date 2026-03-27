from ..graphs.inline_grade_graph import inline_grade_graph
from ..types import InlineQuizGrade, InlineQuizQuestion


def generate_inline_quiz_grades(
    questions: list[InlineQuizQuestion],
    answers: dict[str, str],
) -> list[InlineQuizGrade]:
    result = inline_grade_graph.invoke(
        {
            "questions": questions,
            "answers": answers,
        }
    )
    return result.get("final_grades", [])

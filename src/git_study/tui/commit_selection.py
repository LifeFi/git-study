from dataclasses import dataclass


@dataclass(frozen=True)
class CommitSelection:
    start_index: int | None = None
    end_index: int | None = None


def selected_commit_indices(selection: CommitSelection) -> set[int]:
    if selection.start_index is None:
        return set()
    if selection.end_index is None:
        return {selection.start_index}
    start = min(selection.start_index, selection.end_index)
    end = max(selection.start_index, selection.end_index)
    return set(range(start, end + 1))


def update_selection_for_index(selection: CommitSelection, index: int) -> CommitSelection:
    if selection.start_index is None:
        return CommitSelection(start_index=index, end_index=None)
    if index == selection.start_index:
        return CommitSelection()
    return CommitSelection(start_index=selection.start_index, end_index=index)


def selection_prefix(index: int, selection: CommitSelection) -> str:
    if index == selection.start_index:
        return "start"
    if index == selection.end_index:
        return "end"
    if index in selected_commit_indices(selection):
        return "inside"
    return "none"


def selection_help_text(selection: CommitSelection, selected_count: int) -> str:
    if selection.start_index is None:
        return "Space로 시작 커밋 선택"
    if selection.end_index is None:
        return "Space로 끝 커밋 선택"
    return f"범위 선택됨 ({selected_count} commits)"

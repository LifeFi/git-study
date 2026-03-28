from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from ..domain.inline_anchor import normalize_anchor_snippet


class NeighborCodeContextInput(BaseModel):
    file_path: str = Field(description="Path of the file to inspect.")
    anchor_snippet: str = Field(description="Exact anchor snippet to locate in the file.")
    before_lines: int = Field(default=8, ge=0, le=40)
    after_lines: int = Field(default=8, ge=0, le=40)


def _find_anchor_line_range(content: str, anchor_snippet: str) -> tuple[int, int] | None:
    normalized_content = normalize_anchor_snippet(content)
    normalized_snippet = normalize_anchor_snippet(anchor_snippet)
    if not normalized_content or not normalized_snippet:
        return None
    index = normalized_content.find(normalized_snippet)
    if index < 0:
        return None
    start_line = normalized_content[:index].count("\n")
    end_line = start_line + normalized_snippet.count("\n")
    return (start_line, end_line)


def get_neighbor_code_context(
    *,
    file_context_map: dict[str, str],
    file_path: str,
    anchor_snippet: str,
    before_lines: int = 8,
    after_lines: int = 8,
) -> str:
    content = str(file_context_map.get(file_path, ""))
    if not content.strip():
        return f"FILE: {file_path}\nSTATUS: missing\n"

    lines = content.splitlines()
    located_range = _find_anchor_line_range(content, anchor_snippet)
    if located_range is None:
        start_line = 0
        end_line = min(len(lines), max(before_lines + after_lines + 1, 12)) - 1
        status = "anchor_not_found"
    else:
        anchor_start, anchor_end = located_range
        start_line = max(0, anchor_start - before_lines)
        end_line = min(len(lines) - 1, anchor_end + after_lines)
        status = "ok"

    snippet = "\n".join(lines[start_line : end_line + 1])
    return (
        f"FILE: {file_path}\n"
        f"STATUS: {status}\n"
        f"LINES: {start_line + 1}-{end_line + 1}\n"
        "```text\n"
        f"{snippet}\n"
        "```"
    )


def build_get_neighbor_code_context_tool(
    file_context_map: dict[str, str],
) -> StructuredTool:
    def _tool(
        file_path: str,
        anchor_snippet: str,
        before_lines: int = 8,
        after_lines: int = 8,
    ) -> str:
        return get_neighbor_code_context(
            file_context_map=file_context_map,
            file_path=file_path,
            anchor_snippet=anchor_snippet,
            before_lines=before_lines,
            after_lines=after_lines,
        )

    return StructuredTool.from_function(
        func=_tool,
        name="get_neighbor_code_context",
        description=(
            "Return a narrow code snippet around an anchor in a changed file. "
            "Use this when you need nearby lines before writing an inline quiz question."
        ),
        args_schema=NeighborCodeContextInput,
    )

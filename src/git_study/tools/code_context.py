from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool


class NeighborCodeContextInput(BaseModel):
    file_path: str = Field(description="Path of the file to inspect.")
    anchor_line: int = Field(description="1-based line number of the anchor.")
    before_lines: int = Field(default=8, ge=0, le=40)
    after_lines: int = Field(default=8, ge=0, le=40)


def get_neighbor_code_context(
    *,
    file_context_map: dict[str, str],
    file_path: str,
    anchor_line: int,
    before_lines: int = 8,
    after_lines: int = 8,
) -> str:
    content = str(file_context_map.get(file_path, ""))
    if not content.strip():
        return f"FILE: {file_path}\nSTATUS: missing\n"

    lines = content.splitlines()
    if anchor_line < 1 or anchor_line > len(lines):
        return f"FILE: {file_path}\nSTATUS: line_out_of_range (requested {anchor_line}, file has {len(lines)} lines)\n"

    start = max(0, anchor_line - 1 - before_lines)
    end = min(len(lines), anchor_line + after_lines)
    snippet = "\n".join(
        f"{i + 1:4d} | {lines[i]}" for i in range(start, end)
    )
    return (
        f"FILE: {file_path}\n"
        f"STATUS: ok\n"
        f"LINES: {start + 1}-{end}\n"
        "```text\n"
        f"{snippet}\n"
        "```"
    )


def build_get_neighbor_code_context_tool(
    file_context_map: dict[str, str],
) -> StructuredTool:
    def _tool(
        file_path: str,
        anchor_line: int,
        before_lines: int = 8,
        after_lines: int = 8,
    ) -> str:
        return get_neighbor_code_context(
            file_context_map=file_context_map,
            file_path=file_path,
            anchor_line=anchor_line,
            before_lines=before_lines,
            after_lines=after_lines,
        )

    return StructuredTool.from_function(
        func=_tool,
        name="get_neighbor_code_context",
        description=(
            "Return a narrow code snippet around an anchor line in a changed file. "
            "Use this when you need nearby lines before writing an inline quiz question."
        ),
        args_schema=NeighborCodeContextInput,
    )

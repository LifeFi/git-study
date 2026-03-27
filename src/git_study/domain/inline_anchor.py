import re


def extract_file_paths_from_summary(changed_files_summary: str) -> list[str]:
    paths: list[str] = []
    for line in changed_files_summary.strip().splitlines():
        if "|" in line:
            path = line.split("|")[0].strip()
            if path:
                paths.append(path)
    return paths


def parse_file_context_blocks(file_context_text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    pattern = re.compile(
        r"FILE:\s+(?P<path>[^\n]+)\n```[^\n]*\n(?P<content>[\s\S]*?)\n```"
    )
    for match in pattern.finditer(file_context_text):
        path = match.group("path").strip()
        content = match.group("content")
        if path and content:
            blocks[path] = content
    return blocks


def normalize_anchor_snippet(snippet: str) -> str:
    lines = [line.rstrip() for line in snippet.splitlines()]
    return "\n".join(lines).strip()


def snippet_exists_in_content(content: str, snippet: str) -> bool:
    normalized_snippet = normalize_anchor_snippet(snippet)
    if not normalized_snippet:
        return False
    normalized_content = normalize_anchor_snippet(content)
    return normalized_snippet in normalized_content

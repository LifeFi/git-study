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
    # FILE: 줄 기준으로 세그먼트 분리
    segments = re.split(r"(?=^FILE:\s+)", file_context_text, flags=re.MULTILINE)
    for segment in segments:
        m = re.match(r"FILE:\s+(?P<path>[^\n]+)\n```[^\n]*\n", segment)
        if not m:
            continue
        path = m.group("path").strip()
        rest = segment[m.end():]
        # 세그먼트 내 마지막 ``` 줄을 닫는 펜스로 사용 (중첩 펜스 대응)
        lines = rest.split("\n")
        closing_idx = None
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                closing_idx = i
                break
        if closing_idx is not None:
            content = "\n".join(lines[:closing_idx])
        else:
            content = rest.rstrip()
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

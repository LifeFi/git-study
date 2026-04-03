import re
from pathlib import PurePath

from git import NULL_TREE, Repo


MAX_DIFF_CHARS = 12_000
MAX_FILE_CONTEXT_CHARS = 24_000
MAX_FILE_CONTEXT_FILES = 10
MAX_FILE_SNIPPET_CHARS = 3_000
HUNK_CONTEXT_LINES = 30


def sanitize_diff(raw_diff: str) -> str:
    if not raw_diff.strip():
        return ""

    sections = raw_diff.split("diff --git ")
    cleaned_sections: list[str] = []

    for index, section in enumerate(sections):
        if not section.strip():
            continue

        normalized = section if index == 0 else f"diff --git {section}"
        if "GIT binary patch" in normalized:
            continue
        if "Binary files " in normalized:
            continue
        if "@@" not in normalized:
            continue
        cleaned_sections.append(normalized.strip())

    cleaned = "\n\n".join(cleaned_sections)
    return cleaned[:MAX_DIFF_CHARS].strip()


def get_file_content_at_commit(repo: Repo, commit_sha: str, path: str) -> str:
    commit = repo.commit(commit_sha)
    blob = commit.tree / path
    return blob.data_stream.read().decode("utf-8", errors="replace")


def get_file_content_at_commit_or_empty(repo: Repo, commit_sha: str | None, path: str) -> str:
    if not commit_sha:
        return ""
    try:
        return get_file_content_at_commit(repo, commit_sha, path)
    except Exception:
        return ""


def list_commit_tree_files(repo: Repo, commit_sha: str) -> list[str]:
    commit = repo.commit(commit_sha)
    paths: list[str] = []
    for item in commit.tree.traverse():
        if item.type == "blob":
            paths.append(item.path)
    return sorted(paths)


def get_commit_parent_sha(repo: Repo, commit_sha: str) -> str | None:
    commit = repo.commit(commit_sha)
    if not commit.parents:
        return None
    return commit.parents[0].hexsha


def detect_code_language(path: str) -> str:
    suffix = PurePath(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".java": "java",
        ".kt": "kotlin",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".scala": "scala",
        ".sql": "sql",
        ".sh": "bash",
        ".zsh": "bash",
        ".md": "markdown",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".xml": "xml",
    }.get(suffix, "")


def format_file_context_block(path: str, content: str, max_chars: int | None = None) -> str:
    language = detect_code_language(path)
    snippet = (content[:max_chars] if max_chars is not None else content).rstrip()
    numbered = "\n".join(
        f"{i:4d} | {line}"
        for i, line in enumerate(snippet.splitlines(), start=1)
    )
    return "\n".join([f"FILE: {path}", f"```{language}", numbered, "```"])


def extract_patch_text(commit) -> str:
    if commit.parents:
        diff_index = commit.parents[0].diff(commit, create_patch=True)
    else:
        diff_index = commit.diff(NULL_TREE, create_patch=True)
    return build_patch_text_from_diff_index(diff_index)


def build_patch_text_from_diff_index(diff_index) -> str:
    patches: list[str] = []

    for diff in diff_index:
        patch_bytes = diff.diff
        if not patch_bytes:
            continue
        if isinstance(patch_bytes, bytes):
            patch_text = patch_bytes.decode("utf-8", errors="replace")
        else:
            patch_text = str(patch_bytes)

        old_path = diff.a_path or "/dev/null"
        new_path = diff.b_path or "/dev/null"
        patches.append(
            "\n".join(
                [f"diff --git a/{old_path} b/{new_path}", patch_text.strip()]
            ).strip()
        )

    return "\n\n".join(patches)


def extract_range_patch_text(base_commit, target_commit) -> str:
    diff_index = (
        target_commit.diff(NULL_TREE, create_patch=True)
        if base_commit is NULL_TREE
        else base_commit.diff(target_commit, create_patch=True)
    )
    return build_patch_text_from_diff_index(diff_index)


def get_changed_file_paths(commit) -> list[str]:
    if commit.parents:
        diff_index = commit.parents[0].diff(commit, create_patch=False)
    else:
        diff_index = commit.diff(NULL_TREE, create_patch=False)
    return get_changed_file_paths_from_diff_index(diff_index)


def get_changed_file_paths_from_diff_index(diff_index) -> list[str]:
    paths: list[str] = []
    for diff in diff_index:
        path = diff.b_path or diff.a_path
        if not path:
            continue
        if path not in paths:
            paths.append(path)
    return paths


def get_range_changed_file_paths(base_commit, target_commit) -> list[str]:
    diff_index = (
        target_commit.diff(NULL_TREE, create_patch=False)
        if base_commit is NULL_TREE
        else base_commit.diff(target_commit, create_patch=False)
    )
    return get_changed_file_paths_from_diff_index(diff_index)


def parse_diff_hunk_ranges(raw_diff: str, file_path: str) -> list[tuple[int, int]]:
    """diff에서 특정 파일의 변경 hunk 라인 범위(new file 기준) 반환."""
    hunks: list[tuple[int, int]] = []
    in_file = False
    for line in raw_diff.splitlines():
        if line.startswith("diff --git"):
            in_file = file_path in line
        elif in_file:
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start = int(m.group(1))
                if start == 0:  # 파일 삭제 케이스
                    continue
                count = int(m.group(2)) if m.group(2) is not None else 1
                hunks.append((start, max(start, start + count - 1)))
    return hunks


def _merge_ranges(
    ranges: list[tuple[int, int]], context: int, max_line: int
) -> list[tuple[int, int]]:
    """context 여유분 추가 후 겹치는 구간 병합."""
    expanded = [(max(1, s - context), min(max_line, e + context)) for s, e in ranges]
    expanded.sort()
    merged: list[tuple[int, int]] = []
    for s, e in expanded:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def format_hunk_file_context_block(
    path: str, content: str, hunk_ranges: list[tuple[int, int]]
) -> str:
    """hunk 주변 ±HUNK_CONTEXT_LINES 라인만 포함한 파일 컨텍스트 블록."""
    lines = content.splitlines()
    language = detect_code_language(path)
    merged = _merge_ranges(hunk_ranges, HUNK_CONTEXT_LINES, len(lines))

    parts: list[str] = []
    prev_end = 0
    for start, end in merged:
        if prev_end > 0 and start > prev_end + 1:
            parts.append(f"   … | ({start - prev_end - 1}줄 생략)")
        for i in range(start, min(end, len(lines)) + 1):
            parts.append(f"{i:4d} | {lines[i - 1]}")
        prev_end = end

    numbered = "\n".join(parts)
    return "\n".join([f"FILE: {path}", f"```{language}", numbered, "```"])


def build_file_context_text(commit, repo: Repo, *, raw_diff: str = "") -> str:
    paths = get_changed_file_paths(commit)[:MAX_FILE_CONTEXT_FILES]
    if not paths:
        return ""
    file_contexts: list[str] = []

    for path in paths:
        try:
            content = get_file_content_at_commit(repo, commit.hexsha, path)
        except Exception:
            continue
        hunk_ranges = parse_diff_hunk_ranges(raw_diff, path) if raw_diff else []
        if hunk_ranges:
            file_contexts.append(format_hunk_file_context_block(path, content, hunk_ranges))
        else:
            file_contexts.append(format_file_context_block(path, content, max_chars=MAX_FILE_CONTEXT_CHARS // len(paths)))

    combined = "\n\n".join(file_contexts)
    return combined[:MAX_FILE_CONTEXT_CHARS].strip()


def build_range_file_context_text(
    base_commit, target_commit, repo: Repo, *, raw_diff: str = ""
) -> str:
    paths = get_range_changed_file_paths(base_commit, target_commit)[:MAX_FILE_CONTEXT_FILES]
    if not paths:
        return ""
    file_contexts: list[str] = []

    for path in paths:
        try:
            content = get_file_content_at_commit(repo, target_commit.hexsha, path)
        except Exception:
            continue
        hunk_ranges = parse_diff_hunk_ranges(raw_diff, path) if raw_diff else []
        if hunk_ranges:
            file_contexts.append(format_hunk_file_context_block(path, content, hunk_ranges))
        else:
            file_contexts.append(format_file_context_block(path, content, max_chars=MAX_FILE_CONTEXT_CHARS // len(paths)))

    combined = "\n\n".join(file_contexts)
    return combined[:MAX_FILE_CONTEXT_CHARS].strip()


def build_full_file_map(commit, repo: Repo) -> dict[str, str]:
    """변경된 파일들의 전체 내용 dict 반환 (잘리지 않음, 라인번호 없음).
    get_neighbor_code_context tool에서 원본 파일 탐색 시 사용."""
    full_map: dict[str, str] = {}
    for path in get_changed_file_paths(commit)[:MAX_FILE_CONTEXT_FILES]:
        try:
            full_map[path] = get_file_content_at_commit(repo, commit.hexsha, path)
        except Exception:
            pass
    return full_map


def build_range_full_file_map(base_commit, target_commit, repo: Repo) -> dict[str, str]:
    """범위 커밋의 변경 파일 전체 내용 dict 반환 (잘리지 않음, 라인번호 없음)."""
    full_map: dict[str, str] = {}
    for path in get_range_changed_file_paths(base_commit, target_commit)[:MAX_FILE_CONTEXT_FILES]:
        try:
            full_map[path] = get_file_content_at_commit(repo, target_commit.hexsha, path)
        except Exception:
            pass
    return full_map

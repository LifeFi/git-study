from pathlib import PurePath

from git import NULL_TREE, Repo


MAX_DIFF_CHARS = 12_000
MAX_FILE_CONTEXT_CHARS = 12_000
MAX_FILE_CONTEXT_FILES = 5
MAX_FILE_SNIPPET_CHARS = 3_000


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


def format_file_context_block(path: str, content: str) -> str:
    language = detect_code_language(path)
    snippet = content[:MAX_FILE_SNIPPET_CHARS].rstrip()
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


def build_file_context_text(commit, repo: Repo) -> str:
    file_contexts: list[str] = []

    for path in get_changed_file_paths(commit)[:MAX_FILE_CONTEXT_FILES]:
        try:
            content = get_file_content_at_commit(repo, commit.hexsha, path)
        except Exception:
            continue

        file_contexts.append(format_file_context_block(path, content))

    combined = "\n\n".join(file_contexts)
    return combined[:MAX_FILE_CONTEXT_CHARS].strip()


def build_range_file_context_text(base_commit, target_commit, repo: Repo) -> str:
    file_contexts: list[str] = []

    for path in get_range_changed_file_paths(base_commit, target_commit)[:MAX_FILE_CONTEXT_FILES]:
        try:
            content = get_file_content_at_commit(repo, target_commit.hexsha, path)
        except Exception:
            continue

        file_contexts.append(format_file_context_block(path, content))

    combined = "\n\n".join(file_contexts)
    return combined[:MAX_FILE_CONTEXT_CHARS].strip()

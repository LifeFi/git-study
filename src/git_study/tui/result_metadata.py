import time
from pathlib import Path

from git.exc import InvalidGitRepositoryError, NoSuchPathError

from ..domain.repo_context import get_repo


def selected_range_summary(
    commits: list[dict[str, str]],
    start_index: int | None,
    end_index: int | None,
) -> str:
    if start_index is None:
        return "없음"
    start_commit = commits[start_index]
    if end_index is None:
        return f"시작 {start_commit['short_sha']}"
    end_commit = commits[end_index]
    selected_count = abs(end_index - start_index) + 1
    return (
        f"{start_commit['short_sha']} ~ {end_commit['short_sha']} "
        f"({selected_count} commits)"
    )


def current_repository_label(repo_source: str, github_repo_url: str) -> str:
    if repo_source == "github":
        return github_repo_url or "unknown"
    try:
        local_repo = get_repo(repo_source="local", refresh_remote=False)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return str(Path.cwd())
    return local_repo.working_tree_dir or str(Path.cwd())


def build_result_metadata_block(
    *,
    extension: str,
    created_at: str | None,
    repo_source: str,
    github_repo_url: str,
    commit_mode: str,
    difficulty: str,
    quiz_style: str,
    range_summary: str,
    selected_commits: list[dict[str, str]],
    highlighted_commit: dict[str, str] | None,
) -> str:
    metadata_lines = [
        f"created_at: {created_at or time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"repo_source: {repo_source}",
        f"repository: {current_repository_label(repo_source, github_repo_url)}",
        f"commit_mode: {commit_mode}",
        f"difficulty: {difficulty}",
        f"quiz_style: {quiz_style}",
        f"selected_range: {range_summary}",
    ]
    if highlighted_commit is not None:
        metadata_lines.extend(
            [
                f"highlighted_commit_sha: {highlighted_commit['sha']}",
                f"highlighted_commit_subject: {highlighted_commit['subject']}",
            ]
        )
    if selected_commits:
        metadata_lines.append("selected_commits:")
        metadata_lines.extend(
            f"- {commit['short_sha']}: {commit['subject']}" for commit in selected_commits
        )

    if extension == "md":
        return "\n".join(["---", *metadata_lines, "---", ""])
    return "\n".join(["[metadata]", *metadata_lines, ""])


def split_result_metadata(content: str) -> tuple[str, str] | None:
    if content.startswith("---\n"):
        parts = content.split("\n---\n", 1)
        if len(parts) != 2:
            return None
        metadata_block = parts[0][4:]
        body = parts[1].lstrip("\n")
        return metadata_block, body

    if content.startswith("[metadata]\n"):
        lines = content.splitlines()
        metadata_lines: list[str] = []
        body_start = 1
        for index in range(1, len(lines)):
            line = lines[index]
            if not line.strip():
                body_start = index + 1
                break
            metadata_lines.append(line)
        body = "\n".join(lines[body_start:])
        return "\n".join(metadata_lines), body

    return None


def markdown_content_for_view(content: str, metadata_expanded: bool) -> str:
    metadata_parts = split_result_metadata(content)
    if metadata_parts is None:
        return content

    metadata_block, body = metadata_parts
    if not metadata_block.strip():
        return body

    if not metadata_expanded:
        return "\n".join(
            [
                "## Metadata",
                "",
                "_접혀 있습니다. 상단의 `meta`를 눌러 펼칠 수 있습니다._",
                "",
                body,
            ]
        )

    return "\n".join(
        [
            "## Metadata",
            "",
            "```yaml",
            metadata_block,
            "```",
            "",
            body,
        ]
    )


def result_content_for_save(result_content: str, extension: str) -> str:
    metadata_parts = split_result_metadata(result_content)
    if metadata_parts is None:
        return result_content

    metadata_block, body = metadata_parts
    if extension == "md":
        return result_content
    return "\n".join(["[metadata]", metadata_block, "", body])

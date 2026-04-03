from itertools import chain
from typing import Literal

from git import NULL_TREE, GitCommandError, Repo

from ..domain.code_context import (
    MAX_FILE_CONTEXT_CHARS,
    build_file_context_text,
    build_range_file_context_text,
    extract_patch_text,
    extract_range_patch_text,
    sanitize_diff,
)
from ..domain.repo_cache import (
    cleanup_expired_remote_repo_caches,
    get_remote_repo_cache_dir,
    normalize_github_repo_url,
    slugify_repo_url,
    update_remote_repo_cache_metadata,
)
from ..types import CommitHead, CommitListSnapshot


MAX_COMMITS_TO_SCAN = 8
DEFAULT_COMMIT_LIST_LIMIT = 10


def get_repo(
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
    local_repo_root=None,
) -> Repo:
    if repo_source == "local":
        if local_repo_root is not None:
            return Repo(str(local_repo_root))
        return Repo(".", search_parent_directories=True)

    if not github_repo_url:
        raise ValueError("github repo source requires github_repo_url")
    github_repo_url = normalize_github_repo_url(github_repo_url)
    cleanup_expired_remote_repo_caches()

    cache_dir = get_remote_repo_cache_dir() / slugify_repo_url(github_repo_url)
    cache_dir.parent.mkdir(parents=True, exist_ok=True)

    if cache_dir.exists():
        repo = Repo(cache_dir)
        origin = repo.remotes.origin
        origin.set_url(github_repo_url)
        if refresh_remote:
            origin.fetch(prune=True)
        update_remote_repo_cache_metadata(github_repo_url, cache_dir)
        return repo

    repo = Repo.clone_from(github_repo_url, cache_dir)
    update_remote_repo_cache_metadata(github_repo_url, cache_dir)
    return repo


def list_recent_commits(
    limit: int = DEFAULT_COMMIT_LIST_LIMIT,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> list[dict[str, str]]:
    return get_commit_list_snapshot(
        limit=limit,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        refresh_remote=refresh_remote,
    )["commits"]


def get_latest_commit_head(
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> CommitHead | None:
    repo = get_repo(repo_source, github_repo_url, refresh_remote=refresh_remote)
    commit = next(iter_available_commits(repo, max_count=1), None)
    if commit is None:
        return None
    return {
        "sha": commit.hexsha,
        "short_sha": commit.hexsha[:7],
        "subject": commit.summary,
        "author": str(commit.author),
        "date": commit.committed_datetime.isoformat(),
    }


def has_more_commits(
    limit: int = DEFAULT_COMMIT_LIST_LIMIT,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> bool:
    return get_commit_list_snapshot(
        limit=limit,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        refresh_remote=refresh_remote,
    )["has_more_commits"]


def count_total_commits(
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
) -> int:
    return get_commit_list_snapshot(
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        refresh_remote=refresh_remote,
    )["total_commit_count"]


def get_commit_list_snapshot(
    limit: int = DEFAULT_COMMIT_LIST_LIMIT,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
    local_repo_root=None,
) -> CommitListSnapshot:
    repo = get_repo(repo_source, github_repo_url, refresh_remote=refresh_remote, local_repo_root=local_repo_root)
    commits: list[dict[str, str]] = []
    total_commit_count = 0

    for total_commit_count, commit in enumerate(iter_available_commits(repo), start=1):
        if len(commits) < limit:
            commits.append(
                {
                    "sha": commit.hexsha,
                    "short_sha": commit.hexsha[:7],
                    "subject": commit.summary,
                    "author": str(commit.author),
                    "date": commit.committed_datetime.isoformat(),
                }
            )

    return {
        "commits": commits,
        "has_more_commits": total_commit_count > limit,
        "total_commit_count": total_commit_count,
    }


def list_commit_rev_candidates(repo: Repo) -> list[str]:
    candidates: list[str] = []

    def add_candidate(rev: str | None) -> None:
        if rev and rev not in candidates:
            candidates.append(rev)

    try:
        add_candidate(repo.active_branch.path)
    except (TypeError, ValueError, AttributeError):
        pass

    for head in repo.heads:
        add_candidate(head.path)
        add_candidate(head.name)

    for ref in repo.refs:
        add_candidate(getattr(ref, "path", None))

    for remote in repo.remotes:
        for ref in remote.refs:
            add_candidate(getattr(ref, "path", None))

    return candidates


def iter_available_commits(repo: Repo, max_count: int | None = None):
    for rev in [None, *list_commit_rev_candidates(repo)]:
        try:
            iterator = (
                repo.iter_commits(max_count=max_count)
                if rev is None
                else repo.iter_commits(rev=rev, max_count=max_count)
            )
            first_commit = next(iterator, None)
        except (ValueError, GitCommandError):
            continue
        if first_commit is None:
            return iter(())
        return chain([first_commit], iterator)

    return iter(())


def build_changed_files_summary(commit) -> str:
    stats = commit.stats.files
    if not stats:
        return "No changed files."

    lines = []
    for path, stat in stats.items():
        lines.append(
            f"{path} | +{stat['insertions']} -{stat['deletions']} "
            f"(lines changed: {stat['lines']})"
        )
    return "\n".join(lines)


def build_commit_context(commit, selected_reason: str, repo: Repo) -> dict[str, str]:
    raw_diff = extract_patch_text(commit)
    return {
        "commit_sha": commit.hexsha,
        "commit_subject": commit.summary,
        "commit_author": str(commit.author),
        "commit_date": commit.committed_datetime.isoformat(),
        "changed_files_summary": build_changed_files_summary(commit),
        "diff_text": sanitize_diff(raw_diff),
        "file_context_text": build_file_context_text(commit, repo, raw_diff=raw_diff),
        "selected_reason": selected_reason,
    }


def build_range_changed_files_summary(base_commit, target_commit) -> str:
    diff_index = (
        target_commit.diff(NULL_TREE, create_patch=False)
        if base_commit is NULL_TREE
        else base_commit.diff(target_commit, create_patch=False)
    )
    lines: list[str] = []
    for diff in diff_index:
        path = diff.b_path or diff.a_path or "unknown"
        change_type = diff.change_type.upper() if diff.change_type else "M"
        lines.append(f"- [{change_type}] {path}")
    return "\n".join(lines)


def build_multi_commit_context(commits, selected_reason: str, repo: Repo) -> dict[str, str]:
    parts: list[dict[str, str]] = [
        build_commit_context(commit, selected_reason, repo) for commit in commits
    ]
    newest_commit = commits[0]
    oldest_commit = commits[-1]
    base_commit = oldest_commit.parents[0] if oldest_commit.parents else NULL_TREE
    range_raw_diff = extract_range_patch_text(base_commit, newest_commit)
    range_diff_text = sanitize_diff(range_raw_diff)
    range_changed_files_summary = build_range_changed_files_summary(base_commit, newest_commit)
    range_file_context_text = build_range_file_context_text(
        base_commit, newest_commit, repo, raw_diff=range_raw_diff
    )
    per_commit_changed_files = [
        f"[{part['commit_sha'][:7]}] {part['commit_subject']}\n{part['changed_files_summary']}"
        for part in parts
    ]
    per_commit_file_context = [
        f"# Commit {part['commit_sha'][:7]} - {part['commit_subject']}\n{part['file_context_text']}"
        for part in parts
        if part["file_context_text"]
    ]
    return {
        "commit_sha": ", ".join(part["commit_sha"][:7] for part in parts),
        "commit_subject": " / ".join(part["commit_subject"] for part in parts),
        "commit_author": ", ".join(sorted({part["commit_author"] for part in parts})),
        "commit_date": " ~ ".join([parts[-1]["commit_date"], parts[0]["commit_date"]]),
        "changed_files_summary": "\n\n".join(
            ["[Combined range changed files]", range_changed_files_summary or "No changed files.", "", "[Per-commit breakdown]"]
            + per_commit_changed_files
        ),
        "diff_text": range_diff_text,
        "file_context_text": "\n\n".join(
            [
                "[Combined range file context]",
                range_file_context_text or "No readable changed file content was extracted.",
                "",
                "[Per-commit file context]",
            ]
            + per_commit_file_context
        )[:MAX_FILE_CONTEXT_CHARS].strip(),
        "selected_reason": selected_reason,
    }


def get_commit_by_sha(
    commit_sha: str,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
    refresh_remote: bool = True,
):
    repo = get_repo(repo_source, github_repo_url, refresh_remote=refresh_remote)
    return repo.commit(commit_sha)


def get_latest_commit_context(
    commit_mode: Literal["auto", "latest", "selected"] = "auto",
    requested_commit_sha: str | None = None,
    requested_commit_shas: list[str] | None = None,
    repo_source: Literal["local", "github"] = "local",
    github_repo_url: str | None = None,
) -> dict[str, str]:
    repo = get_repo(repo_source, github_repo_url)

    if commit_mode == "selected":
        commit_shas = requested_commit_shas or ([requested_commit_sha] if requested_commit_sha else [])
        if not commit_shas:
            raise ValueError("selected mode requires at least one commit sha")
        commits = [repo.commit(commit_sha) for commit_sha in commit_shas]
        if len(commits) == 1:
            return build_commit_context(commits[0], "selected_commit", repo)
        return build_multi_commit_context(commits, "selected_commits", repo)

    commits = list(iter_available_commits(repo, max_count=MAX_COMMITS_TO_SCAN))
    if not commits:
        raise ValueError("저장소에서 읽을 수 있는 커밋을 찾지 못했습니다.")

    latest_context = build_commit_context(commits[0], "latest", repo)
    if commit_mode == "latest" or latest_context["diff_text"]:
        return latest_context

    for commit in commits[1:]:
        context = build_commit_context(commit, "fallback_recent_text_commit", repo)
        if context["diff_text"]:
            return context

    return latest_context

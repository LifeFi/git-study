from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotApplication:
    selected_commit_index: int
    selected_range_start_index: int | None
    selected_range_end_index: int | None
    unseen_auto_refresh_commit_shas: set[str]
    last_seen_head_sha: str
    last_seen_total_commit_count: int


def current_repo_key(repo_source: str, github_repo_url: str | None) -> str:
    if repo_source == "local":
        return "local"
    return f"github:{github_repo_url or ''}"


def should_check_remote(
    repo_source: str,
    last_remote_refresh_check_at: float,
    now: float,
    remote_poll_seconds: float,
) -> tuple[bool, float]:
    if repo_source != "github":
        return True, last_remote_refresh_check_at
    if (
        last_remote_refresh_check_at
        and now - last_remote_refresh_check_at < remote_poll_seconds
    ):
        return False, last_remote_refresh_check_at
    return True, now


def apply_commit_snapshot_state(
    *,
    previous_commits: list[dict[str, str]],
    new_commits: list[dict[str, str]],
    selected_commit_index: int,
    selected_range_start_index: int | None,
    selected_range_end_index: int | None,
    mark_new_commits: bool,
    unseen_auto_refresh_commit_shas: set[str],
    total_commit_count: int,
) -> SnapshotApplication:
    previous_commit_shas = {commit["sha"] for commit in previous_commits}
    previous_selected_shas = [
        previous_commits[index]["sha"]
        for index in sorted(_selected_indices(selected_range_start_index, selected_range_end_index))
        if index < len(previous_commits)
    ]
    previous_start_sha = (
        previous_commits[selected_range_start_index]["sha"]
        if selected_range_start_index is not None
        and selected_range_start_index < len(previous_commits)
        else None
    )
    previous_end_sha = (
        previous_commits[selected_range_end_index]["sha"]
        if selected_range_end_index is not None
        and selected_range_end_index < len(previous_commits)
        else None
    )
    previously_highlighted_sha = None
    if previous_commits and selected_commit_index < len(previous_commits):
        previously_highlighted_sha = previous_commits[selected_commit_index]["sha"]

    next_unseen = set(unseen_auto_refresh_commit_shas)
    if mark_new_commits:
        for commit in new_commits:
            if commit["sha"] not in previous_commit_shas:
                next_unseen.add(commit["sha"])

    sha_to_index = {commit["sha"]: index for index, commit in enumerate(new_commits)}
    next_start_index = sha_to_index.get(previous_start_sha) if previous_start_sha else None
    next_end_index = sha_to_index.get(previous_end_sha) if previous_end_sha else None

    if previous_selected_shas and next_start_index is None and next_end_index is None:
        surviving_indices = [
            sha_to_index[sha] for sha in previous_selected_shas if sha in sha_to_index
        ]
        if surviving_indices:
            next_start_index = min(surviving_indices)
            next_end_index = max(surviving_indices)

    next_selected_commit_index = 0
    if previously_highlighted_sha:
        for index, commit in enumerate(new_commits):
            if commit["sha"] == previously_highlighted_sha:
                next_selected_commit_index = index
                break

    return SnapshotApplication(
        selected_commit_index=next_selected_commit_index,
        selected_range_start_index=next_start_index,
        selected_range_end_index=next_end_index,
        unseen_auto_refresh_commit_shas=next_unseen,
        last_seen_head_sha=new_commits[0]["sha"] if new_commits else "",
        last_seen_total_commit_count=total_commit_count,
    )


def _selected_indices(start_index: int | None, end_index: int | None) -> set[int]:
    if start_index is None:
        return set()
    if end_index is None:
        return {start_index}
    start = min(start_index, end_index)
    end = max(start_index, end_index)
    return set(range(start, end + 1))

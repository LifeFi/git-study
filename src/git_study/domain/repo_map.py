"""Repository structure analysis: file tree + commit frequency."""

from __future__ import annotations

from pathlib import Path

_IGNORE_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", ".tox", ".omc", ".git-study",
}
_IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll",
    ".class", ".lock", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".woff", ".woff2", ".ttf", ".eot",
}


def get_file_tree(repo_root: Path, max_files: int = 300) -> list[str]:
    """저장소 루트 하위 파일 경로 목록 (상대 경로, 정렬)."""
    results: list[str] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        parts = rel.parts
        # 무시 디렉토리
        if any(
            p in _IGNORE_DIR_NAMES or p.startswith(".") or p.endswith(".egg-info")
            for p in parts[:-1]
        ):
            continue
        # 숨김 파일 / 무시 확장자
        if parts[-1].startswith(".") or path.suffix in _IGNORE_EXTENSIONS:
            continue
        results.append("/".join(parts))
        if len(results) >= max_files:
            break
    return results


def get_commit_counts(repo, max_commits: int = 500) -> dict[str, int]:
    """파일별 커밋 빈도 집계. git log --name-only 기반."""
    try:
        log_output = repo.git.log(
            f"--max-count={max_commits}",
            "--pretty=format:",
            "--name-only",
        )
    except Exception:
        return {}
    counts: dict[str, int] = {}
    for line in log_output.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts


def build_tree_summary(file_paths: list[str], max_depth: int = 3) -> str:
    """파일 경로 목록 → 들여쓰기 디렉토리 트리 텍스트 (max_depth 기준).

    디렉토리만 표시 (파일 개수가 많아 파일은 생략).
    """
    dirs: set[str] = set()
    for path in file_paths:
        parts = path.split("/")
        for i in range(1, min(len(parts), max_depth + 1)):
            dirs.add("/".join(parts[:i]))

    lines: list[str] = []
    for d in sorted(dirs):
        depth = d.count("/")
        name = d.rsplit("/", 1)[-1]
        indent = "  " * depth
        lines.append(f"{indent}{name}/")
    return "\n".join(lines)

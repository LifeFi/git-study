import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from git import Repo

from ..types import RemoteRepoCacheEntry


REMOTE_CACHE_RETENTION_DAYS = 30


def get_app_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "git-study"

    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "git-study" / "Cache"
        return Path.home() / "AppData" / "Local" / "git-study" / "Cache"

    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home) / "git-study"
    return Path.home() / ".cache" / "git-study"


def get_remote_repo_cache_dir() -> Path:
    return get_app_cache_dir() / "github"


def get_remote_repo_cache_metadata_dir() -> Path:
    return get_app_cache_dir() / "github-meta"


def get_remote_repo_cache_metadata_path(slug: str) -> Path:
    return get_remote_repo_cache_metadata_dir() / f"{slug}.json"


def format_cache_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def normalize_github_repo_url(github_repo_url: str) -> str:
    normalized = github_repo_url.strip().rstrip("/")
    if normalized.startswith("github.com/"):
        normalized = f"https://{normalized}"
    if not normalized.startswith(("http://", "https://")):
        raise ValueError(
            "GitHub 저장소 URL은 https://github.com/owner/repo 형식이어야 합니다."
        )
    if "github.com/" not in normalized:
        raise ValueError("현재는 GitHub 저장소 URL만 지원합니다.")
    if not normalized.endswith(".git"):
        normalized = f"{normalized}.git"
    # owner/repo 세그먼트 검증
    path_part = normalized.split("github.com/")[-1].rstrip("/").removesuffix(".git")
    segments = [s for s in path_part.split("/") if s]
    if len(segments) < 2:
        raise ValueError("GitHub URL은 github.com/owner/repo 형식이어야 합니다.")
    return normalized


def slugify_repo_url(github_repo_url: str) -> str:
    normalized = normalize_github_repo_url(github_repo_url)
    tail = normalized.split("github.com/")[-1].replace("/", "__")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{tail}--{digest}"


def load_remote_repo_cache_metadata(slug: str) -> dict[str, str | float] | None:
    path = get_remote_repo_cache_metadata_path(slug)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def update_remote_repo_cache_metadata(github_repo_url: str, cache_dir: Path) -> None:
    slug = cache_dir.name
    metadata_dir = get_remote_repo_cache_metadata_dir()
    metadata_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().timestamp()
    payload = {
        "repo_url": normalize_github_repo_url(github_repo_url),
        "cache_path": str(cache_dir),
        "last_used_at": now,
    }
    get_remote_repo_cache_metadata_path(slug).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_remote_repo_cache_last_used(slug: str, cache_dir: Path) -> float:
    metadata = load_remote_repo_cache_metadata(slug)
    if metadata is not None:
        last_used_at = metadata.get("last_used_at")
        if isinstance(last_used_at, (int, float)):
            return float(last_used_at)
    return cache_dir.stat().st_mtime


def cleanup_expired_remote_repo_caches(
    now: float | None = None,
    retention_days: int = REMOTE_CACHE_RETENTION_DAYS,
) -> list[str]:
    cache_root = get_remote_repo_cache_dir()
    metadata_root = get_remote_repo_cache_metadata_dir()
    if not cache_root.exists() and not metadata_root.exists():
        return []

    current_ts = now if now is not None else datetime.now().timestamp()
    cutoff = current_ts - (retention_days * 24 * 60 * 60)
    removed_slugs: list[str] = []
    seen_slugs: set[str] = set()

    if cache_root.exists():
        for cache_dir in cache_root.iterdir():
            if not cache_dir.is_dir():
                continue
            slug = cache_dir.name
            seen_slugs.add(slug)
            if get_remote_repo_cache_last_used(slug, cache_dir) >= cutoff:
                continue
            shutil.rmtree(cache_dir, ignore_errors=True)
            metadata_path = get_remote_repo_cache_metadata_path(slug)
            if metadata_path.exists():
                metadata_path.unlink(missing_ok=True)
            removed_slugs.append(slug)

    if metadata_root.exists():
        for metadata_path in metadata_root.glob("*.json"):
            slug = metadata_path.stem
            if slug in seen_slugs:
                continue
            metadata_path.unlink(missing_ok=True)

    return removed_slugs


def list_remote_repo_caches() -> list[RemoteRepoCacheEntry]:
    cache_root = get_remote_repo_cache_dir()
    if not cache_root.exists():
        return []

    entries: list[RemoteRepoCacheEntry] = []
    for cache_dir in sorted(cache_root.iterdir(), key=lambda path: path.name):
        if not cache_dir.is_dir():
            continue
        slug = cache_dir.name
        metadata = load_remote_repo_cache_metadata(slug) or {}
        repo_url = metadata.get("repo_url")
        if not isinstance(repo_url, str) or not repo_url.strip():
            repo_url = ""
            try:
                repo = Repo(cache_dir)
                repo_url = repo.remotes.origin.url
            except Exception:
                repo_url = slug
        last_used_at = get_remote_repo_cache_last_used(slug, cache_dir)
        entries.append(
            {
                "slug": slug,
                "repo_url": repo_url,
                "cache_path": str(cache_dir),
                "last_used_at": last_used_at,
                "last_used_label": format_cache_timestamp(last_used_at),
            }
        )

    return sorted(entries, key=lambda entry: entry["last_used_at"], reverse=True)


def remove_remote_repo_cache(slug: str) -> bool:
    cache_dir = get_remote_repo_cache_dir() / slug
    metadata_path = get_remote_repo_cache_metadata_path(slug)
    removed = False
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)
        removed = True
    if metadata_path.exists():
        metadata_path.unlink(missing_ok=True)
        removed = True
    return removed

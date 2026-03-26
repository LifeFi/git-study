import json
from pathlib import Path

from ..runtime_paths import get_global_runtime_dir


DEFAULT_REQUEST = "최근 커밋 기반으로 퀴즈 만들어줘"


def find_local_repo_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None

def get_runtime_dir(
    *,
    repo_source: str = "local",
    local_repo_root: Path | str | None = None,
) -> Path:
    if repo_source == "local":
        if local_repo_root is not None:
            repo_root = Path(local_repo_root).expanduser().resolve()
        else:
            repo_root = find_local_repo_root()
        if repo_root is not None and (repo_root / ".git").exists():
            return repo_root / ".git-study"
    return get_global_runtime_dir()


def get_app_state_path(
    *,
    repo_source: str = "local",
    local_repo_root: Path | str | None = None,
) -> Path:
    return get_runtime_dir(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    ) / "state.json"


def get_quiz_output_dir(
    *,
    repo_source: str = "local",
    local_repo_root: Path | str | None = None,
) -> Path:
    return get_runtime_dir(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    ) / "outputs"


def load_app_state(
    *,
    repo_source: str = "local",
    local_repo_root: Path | str | None = None,
) -> dict[str, str]:
    app_state_path = get_app_state_path(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    )
    if not app_state_path.exists():
        return {}
    try:
        payload = json.loads(app_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    repo_source = payload.get("repo_source")
    github_repo_url = payload.get("github_repo_url")
    commit_mode = payload.get("commit_mode")
    difficulty = payload.get("difficulty")
    quiz_style = payload.get("quiz_style")
    request_text = payload.get("request_text")
    return {
        "repo_source": repo_source if repo_source in {"local", "github"} else "local",
        "github_repo_url": github_repo_url if isinstance(github_repo_url, str) else "",
        "commit_mode": (
            commit_mode if commit_mode in {"auto", "latest", "selected"} else "auto"
        ),
        "difficulty": (
            difficulty if difficulty in {"easy", "medium", "hard"} else "medium"
        ),
        "quiz_style": (
            quiz_style
            if quiz_style
            in {
                "mixed",
                "study_session",
                "multiple_choice",
                "short_answer",
                "conceptual",
            }
            else "mixed"
        ),
        "request_text": (
            request_text if isinstance(request_text, str) and request_text else DEFAULT_REQUEST
        ),
    }


def save_app_state(
    *,
    repo_source: str,
    github_repo_url: str,
    commit_mode: str,
    difficulty: str,
    quiz_style: str,
    request_text: str,
    local_repo_root: Path | str | None = None,
) -> None:
    runtime_dir = get_runtime_dir(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_source": repo_source,
        "github_repo_url": github_repo_url,
        "commit_mode": commit_mode,
        "difficulty": difficulty,
        "quiz_style": quiz_style,
        "request_text": request_text,
    }
    (runtime_dir / "state.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_saved_result_files(
    *,
    repo_source: str = "local",
    local_repo_root: Path | str | None = None,
) -> list[Path]:
    quiz_output_dir = get_quiz_output_dir(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    )
    if not quiz_output_dir.exists():
        return []
    return sorted(
        quiz_output_dir.glob("quiz-output-*.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

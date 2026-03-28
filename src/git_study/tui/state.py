import json
import shutil
import time
from pathlib import Path

from ..domain.repo_cache import normalize_github_repo_url, slugify_repo_url
from ..runtime_paths import get_global_runtime_dir


DEFAULT_REQUEST = ""
REQUEST_PLACEHOLDER = "퀴즈 생성에 대한 추가 요청 입력해 주세요."
REQUEST_EXAMPLE_TEXT = "예시 - 변경된 내용에서 주로 실수할 만한 부분읜 개념 위주로 만들어줘."


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


def get_session_root_dir(
    *,
    repo_source: str = "local",
    local_repo_root: Path | str | None = None,
) -> Path:
    return get_runtime_dir(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    ) / "sessions"


def get_session_repo_dir(
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | str | None = None,
) -> Path:
    session_root = get_session_root_dir(
        repo_source=repo_source,
        local_repo_root=local_repo_root,
    )
    if repo_source == "local":
        return session_root / "local"

    normalized = normalize_github_repo_url(github_repo_url)
    return session_root / "github" / slugify_repo_url(normalized)


def get_learning_session_path(
    session_id: str,
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | str | None = None,
) -> Path:
    return (
        get_session_repo_dir(
            repo_source=repo_source,
            github_repo_url=github_repo_url,
            local_repo_root=local_repo_root,
        )
        / session_id
        / "session.json"
    )


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
    read_request_text = payload.get("read_request_text")
    basic_request_text = payload.get("basic_request_text")
    inline_request_text = payload.get("inline_request_text")
    grading_request_text = payload.get("grading_request_text")
    highlighted_commit_sha = payload.get("highlighted_commit_sha")
    selected_range_start_sha = payload.get("selected_range_start_sha")
    selected_range_end_sha = payload.get("selected_range_end_sha")
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
            request_text if isinstance(request_text, str) else DEFAULT_REQUEST
        ),
        "read_request_text": (
            read_request_text
            if isinstance(read_request_text, str)
            else (request_text if isinstance(request_text, str) else DEFAULT_REQUEST)
        ),
        "basic_request_text": (
            basic_request_text
            if isinstance(basic_request_text, str)
            else (request_text if isinstance(request_text, str) else DEFAULT_REQUEST)
        ),
        "inline_request_text": (
            inline_request_text
            if isinstance(inline_request_text, str)
            else (request_text if isinstance(request_text, str) else DEFAULT_REQUEST)
        ),
        "grading_request_text": (
            grading_request_text
            if isinstance(grading_request_text, str)
            else (request_text if isinstance(request_text, str) else DEFAULT_REQUEST)
        ),
        "highlighted_commit_sha": (
            highlighted_commit_sha if isinstance(highlighted_commit_sha, str) else ""
        ),
        "selected_range_start_sha": (
            selected_range_start_sha if isinstance(selected_range_start_sha, str) else ""
        ),
        "selected_range_end_sha": (
            selected_range_end_sha if isinstance(selected_range_end_sha, str) else ""
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
    read_request_text: str,
    basic_request_text: str,
    inline_request_text: str,
    grading_request_text: str,
    highlighted_commit_sha: str = "",
    selected_range_start_sha: str = "",
    selected_range_end_sha: str = "",
    local_repo_root: Path | str | None = None,
) -> None:
    payload = {
        "repo_source": repo_source,
        "github_repo_url": github_repo_url,
        "commit_mode": commit_mode,
        "difficulty": difficulty,
        "quiz_style": quiz_style,
        "request_text": request_text,
        "read_request_text": read_request_text,
        "basic_request_text": basic_request_text,
        "inline_request_text": inline_request_text,
        "grading_request_text": grading_request_text,
        "highlighted_commit_sha": highlighted_commit_sha,
        "selected_range_start_sha": selected_range_start_sha,
        "selected_range_end_sha": selected_range_end_sha,
    }
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)

    runtime_dirs = {
        get_runtime_dir(
            repo_source=repo_source,
            local_repo_root=local_repo_root,
        )
    }
    if local_repo_root is not None:
        runtime_dirs.add(
            get_runtime_dir(
                repo_source="local",
                local_repo_root=local_repo_root,
            )
        )

    for runtime_dir in runtime_dirs:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "state.json").write_text(
            payload_text,
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


def load_learning_session_file(
    session_id: str,
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | str | None = None,
) -> dict | None:
    path = get_learning_session_path(
        session_id,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        local_repo_root=local_repo_root,
    )
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def save_learning_session_file(
    session_id: str,
    payload: dict,
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | str | None = None,
) -> Path:
    path = get_learning_session_path(
        session_id,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        local_repo_root=local_repo_root,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def list_learning_sessions(
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | str | None = None,
) -> list[dict[str, str]]:
    repo_dir = get_session_repo_dir(
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        local_repo_root=local_repo_root,
    )
    if not repo_dir.exists():
        return []

    entries: list[dict[str, str]] = []
    for session_dir in sorted(repo_dir.iterdir(), key=lambda path: path.name):
        if not session_dir.is_dir():
            continue
        session_file = session_dir / "session.json"
        if not session_file.exists():
            continue
        try:
            payload = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        selection = payload.get("selection", {}) if isinstance(payload.get("selection"), dict) else {}
        preferences = (
            payload.get("preferences", {}) if isinstance(payload.get("preferences"), dict) else {}
        )
        session_meta = (
            payload.get("session_meta", {}) if isinstance(payload.get("session_meta"), dict) else {}
        )
        selected_shas = selection.get("selected_shas", [])
        if not isinstance(selected_shas, list):
            selected_shas = []
        cleaned_shas = [str(sha).strip() for sha in selected_shas if str(sha).strip()]
        start_sha = str(selection.get("start_sha", "")).strip()
        end_sha = str(selection.get("end_sha", "")).strip()
        if cleaned_shas:
            session_label = (
                str(cleaned_shas[0])[:7]
                if len(cleaned_shas) == 1
                else f"S {str(cleaned_shas[0])[:7]} -> E {str(cleaned_shas[-1])[:7]}"
            )
        elif start_sha and end_sha and start_sha != end_sha:
            session_label = f"S {start_sha[:7]} -> E {end_sha[:7]}"
        else:
            session_label = (start_sha or end_sha or str(payload.get("session_id", session_dir.name)))[:7]
        entries.append(
            {
                "session_id": str(payload.get("session_id", session_dir.name)),
                "path": str(session_dir),
                "updated_at": str(session_meta.get("updated_at", "")),
                "updated_label": str(session_meta.get("updated_at", "")) or time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(session_file.stat().st_mtime)
                ),
                "range_summary": session_label,
                "request_text": str(preferences.get("request_text", "")),
                "difficulty": str(preferences.get("difficulty", "")),
                "quiz_style": str(preferences.get("quiz_style", "")),
                "read_status": str(
                    (payload.get("read", {}) if isinstance(payload.get("read"), dict) else {}).get(
                        "status", "not_started"
                    )
                ),
                "basic_status": str(
                    (
                        payload.get("general_quiz", {})
                        if isinstance(payload.get("general_quiz"), dict)
                        else {}
                    ).get("status", "not_started")
                ),
                "inline_status": str(
                    (
                        payload.get("inline_quiz", {})
                        if isinstance(payload.get("inline_quiz"), dict)
                        else {}
                    ).get("status", "not_started")
                ),
            }
        )
    entries.sort(key=lambda entry: entry.get("updated_at", ""), reverse=True)
    return entries


def remove_learning_session(
    session_id: str,
    *,
    repo_source: str = "local",
    github_repo_url: str = "",
    local_repo_root: Path | str | None = None,
) -> bool:
    session_dir = get_learning_session_path(
        session_id,
        repo_source=repo_source,
        github_repo_url=github_repo_url,
        local_repo_root=local_repo_root,
    ).parent
    if not session_dir.exists():
        return False
    shutil.rmtree(session_dir, ignore_errors=True)
    return True

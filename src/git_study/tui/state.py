import json
from pathlib import Path


DEFAULT_REQUEST = "최근 커밋 기반으로 퀴즈 만들어줘"
APP_RUNTIME_DIR = Path(__file__).resolve().parents[3] / ".git-study"
APP_STATE_PATH = APP_RUNTIME_DIR / "state.json"
QUIZ_OUTPUT_DIR = APP_RUNTIME_DIR / "outputs"


def load_app_state() -> dict[str, str]:
    if not APP_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(APP_STATE_PATH.read_text(encoding="utf-8"))
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
) -> None:
    APP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_source": repo_source,
        "github_repo_url": github_repo_url,
        "commit_mode": commit_mode,
        "difficulty": difficulty,
        "quiz_style": quiz_style,
        "request_text": request_text,
    }
    APP_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_saved_result_files() -> list[Path]:
    if not QUIZ_OUTPUT_DIR.exists():
        return []
    return sorted(
        QUIZ_OUTPUT_DIR.glob("quiz-output-*.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

import json
from typing import Literal, TypedDict

from .runtime_paths import get_global_runtime_dir


DEFAULT_MODEL = "gpt-4o-mini"
ApiKeyMode = Literal["session", "file"]


class AppSettings(TypedDict):
    model: str
    openai_api_key_mode: ApiKeyMode
    openai_api_key_configured: bool


def get_settings_path():
    return get_global_runtime_dir() / "settings.json"


def load_settings() -> AppSettings:
    path = get_settings_path()
    if not path.exists():
        return AppSettings(
            model=DEFAULT_MODEL,
            openai_api_key_mode="session",
            openai_api_key_configured=False,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    model = payload.get("model")
    api_key_mode = payload.get("openai_api_key_mode")
    api_key_configured = payload.get("openai_api_key_configured")
    return AppSettings(
        model=model if isinstance(model, str) and model.strip() else DEFAULT_MODEL,
        openai_api_key_mode=(
            api_key_mode if api_key_mode in {"session", "file"} else "session"
        ),
        openai_api_key_configured=bool(api_key_configured),
    )


def save_settings(
    *,
    model: str = DEFAULT_MODEL,
    openai_api_key_mode: ApiKeyMode,
    openai_api_key_configured: bool,
) -> None:
    runtime_dir = get_global_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model.strip() or DEFAULT_MODEL,
        "openai_api_key_mode": openai_api_key_mode,
        "openai_api_key_configured": openai_api_key_configured,
    }
    get_settings_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

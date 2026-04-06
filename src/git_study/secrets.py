import json
import os
from pathlib import Path

from .runtime_paths import get_global_runtime_dir


_session_openai_api_key: str | None = None


def get_secrets_path() -> Path:
    return get_global_runtime_dir() / "secrets.json"


def _load_secrets_payload() -> dict[str, str]:
    path = get_secrets_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_secrets_payload(payload: dict[str, str]) -> None:
    runtime_dir = get_global_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    get_secrets_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def set_session_openai_api_key(api_key: str | None) -> None:
    global _session_openai_api_key
    value = (api_key or "").strip()
    _session_openai_api_key = value or None


def clear_session_openai_api_key() -> None:
    set_session_openai_api_key(None)


def load_file_openai_api_key() -> str | None:
    api_key = _load_secrets_payload().get("openai_api_key")
    if not isinstance(api_key, str):
        return None
    value = api_key.strip()
    return value or None


def save_openai_api_key(api_key: str) -> None:
    _write_secrets_payload({"openai_api_key": api_key.strip()})


def delete_openai_api_key() -> None:
    path = get_secrets_path()
    if path.exists():
        path.unlink(missing_ok=True)


def get_openai_api_key() -> tuple[str | None, str]:
    env_value = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_value:
        return env_value, "env"
    file_value = load_file_openai_api_key()
    if file_value:
        return file_value, "file"
    if _session_openai_api_key:
        return _session_openai_api_key, "session"
    return None, "missing"

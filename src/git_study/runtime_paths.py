import os
from pathlib import Path


def get_global_runtime_dir() -> Path:
    override = os.environ.get("GIT_STUDY_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".git-study"

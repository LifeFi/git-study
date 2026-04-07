"""버전 업데이트 체크 유틸.

채널 감지: 현재 설치 버전이 pypi.org에 존재하면 stable, 없으면 test(testpypi).
- stable: pypi.org만 체크
- test: testpypi + pypi.org 모두 체크
"""

from __future__ import annotations

import json
import unicodedata
import urllib.request
from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "git-study"
_PYPI_JSON = "https://pypi.org/pypi/{name}/json"
_TESTPYPI_JSON = "https://test.pypi.org/pypi/{name}/json"
_PYPI_VER_JSON = "https://pypi.org/pypi/{name}/{ver}/json"
_TIMEOUT = 3


def _fetch_latest(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except Exception:
        return None


def _version_exists_on_pypi(ver: str) -> bool:
    try:
        url = _PYPI_VER_JSON.format(name=PACKAGE_NAME, ver=ver)
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def _display_width(s: str) -> int:
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def _make_box(lines: list[str]) -> str:
    inner = max(_display_width(l) for l in lines)
    top = "╔" + "═" * (inner + 2) + "╗"
    bottom = "╚" + "═" * (inner + 2) + "╝"
    rows = []
    for l in lines:
        pad = inner - _display_width(l)
        rows.append(f"║ {l}{' ' * pad} ║")
    return f"{top}\n" + "\n".join(rows) + f"\n{bottom}"


def format_as_toast(title: str, command: str) -> str:
    """TUI 토스트용: 제목 + (터미널) 명령어."""
    return f"{title}\n(터미널) {command}"


def format_as_box(title: str, command: str) -> str:
    """CLI 종료 후 출력용: 박스 형태."""
    return _make_box([title, "", command])


def get_update_messages() -> list[tuple[str, str]]:
    """채널 자동 감지 후 (제목, 명령어) 튜플 목록 반환.

    업데이트가 없으면 빈 리스트 반환.
    """
    try:
        current = version(PACKAGE_NAME)
    except PackageNotFoundError:
        return []

    is_stable = _version_exists_on_pypi(current)
    messages: list[tuple[str, str]] = []

    if is_stable:
        latest = _fetch_latest(_PYPI_JSON.format(name=PACKAGE_NAME))
        if latest and latest != current:
            messages.append((
                f"업데이트 있음: v{current} → v{latest}",
                f"uv tool upgrade {PACKAGE_NAME}",
            ))
    else:
        # test 채널: testpypi + pypi 모두 확인
        latest_test = _fetch_latest(_TESTPYPI_JSON.format(name=PACKAGE_NAME))
        latest_stable = _fetch_latest(_PYPI_JSON.format(name=PACKAGE_NAME))

        if latest_test and latest_test != current:
            messages.append((
                f"[test] 업데이트: v{current} → v{latest_test}",
                f"uv tool upgrade {PACKAGE_NAME}",
            ))
        if latest_stable:
            messages.append((
                f"[정식] v{latest_stable} 출시됨 — 정식 채널로 전환:",
                f"uv tool uninstall {PACKAGE_NAME} && uv tool install {PACKAGE_NAME}",
            ))

    return messages

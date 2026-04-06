"""Map service: generate file role summaries for a commit range."""

from __future__ import annotations

import re as _re
from collections.abc import Iterator
from typing import Any

from ..llm.client import LLMClient, model_override_var
from ..llm.token_tracker import TokenUsageCallback

_PROMPT = """\
아래 git 커밋 diff를 분석하여, 변경된 각 파일에 대해 "파일 역할 — 이번 변경의 핵심"을 설명하세요.

설명 길이 기준 (변경 파일 요약의 lines changed 값 기준):
- 10줄 미만: 파일 역할만 (15자 이내)
- 10~50줄: 파일 역할 + 변경 핵심 1가지, em 대시(—)로 구분 (40자 이내)
- 50~200줄: 파일 역할 + 변경 핵심 2~3가지, 쉼표로 구분 (80자 이내)
- 200줄 초과: 파일 역할 + 구체적인 변경 사항 3~5가지, 쉼표로 구분 (120자 이내)

절대 금지 표현 (구체성 없는 모호한 말):
- "대규모 업데이트", "다양한 기능 추가", "코드 개선", "성능 최적화", "코드 정리"
- 이런 표현 대신 실제 변경된 클래스명·함수명·동작을 명시할 것
- 예) "대규모 업데이트" (X) → "OvewviewRuler 추가, 퀴즈 블록 클릭 이벤트 처리, 파일 트리 뱃지 렌더링" (O)

변경 파일 요약 (규모 포함):
{changed_files_summary}

파일 컨텍스트 (일부):
{file_context_text}

{file_timeline_section}{evolution_instruction}반환 형식: 파일 경로를 키, 설명(한국어)을 값으로 하는 JSON 객체.
- 변경된 파일만 포함.
- 마크다운 없이 JSON만 반환.

예시:
{example}
"""

_SINGLE_EXAMPLE = """\
{{
  "src/foo/bar.py": "CommandBar 위젯 — ESC 모드 전환 버그 수정",
  "src/foo/app.py": "앱 진입점 — 스레드 분기 추가, 세션 복원 로직 개선",
  "README.md": "프로젝트 소개 문서"
}}"""

_EVOLUTION_INSTRUCTION = """\
【중요】 위 "파일별 커밋 변경 이력"의 [1][2][3] 번호를 그대로 사용해 각 파일 설명에 변화 흐름을 표현하세요.
- 형식: "파일 역할 — [1]변경내용 → [2]변경내용 → [3]변경내용"
- 파일이 단일 커밋([1]만 있음)이면 화살표 없이 기존 형식 유지
- 전체 길이 120자 이내
- 번호 형식은 반드시 [1][2][3] 대괄호 형식만 사용. 동그라미 숫자(①②③ 등) 절대 금지.

"""

_MULTI_EXAMPLE = """\
{{
  "src/foo/app.py": "앱 진입점 — [1]퀴즈 로딩 상태 추가 → [2]ESC 모드 전환 버그 수정 → [3]그레이딩 연동",
  "src/foo/bar.py": "CommandBar 위젯 — [1]자동완성 추가 → [2]힌트 갱신 로직 개선",
  "README.md": "프로젝트 소개 문서 — [1]설치 안내 배너 추가"
}}"""

_FILE_CONTEXT_MAX = 6000
_CIRCLES = ["[1]", "[2]", "[3]", "[4]", "[5]", "[6]", "[7]", "[8]", "[9]", "[10]"]


def _build_file_timeline(changed_files_summary: str) -> str:
    """Per-commit breakdown을 파싱해 파일별 [1][2][3] 커밋 이력 문자열 반환.

    커밋 순서: breakdown은 newest-first → 역순으로 oldest-first([1]) 표시.
    """
    lines = changed_files_summary.splitlines()
    in_breakdown = False
    current_idx = -1
    # (sha, subject) newest-first
    commit_order: list[tuple[str, str]] = []
    # file_path -> list of commit indices (newest-first order of appearance)
    file_commits: dict[str, list[int]] = {}

    for line in lines:
        stripped = line.strip()
        if stripped == "[Per-commit breakdown]":
            in_breakdown = True
            continue
        if not in_breakdown:
            continue
        m = _re.match(r'^\[([0-9a-f]{7})\] (.+)$', stripped)
        if m:
            commit_order.append((m.group(1), m.group(2)))
            current_idx = len(commit_order) - 1
            continue
        if current_idx >= 0 and " | " in stripped:
            file_path = stripped.split(" | ")[0].strip()
            if file_path:
                file_commits.setdefault(file_path, []).append(current_idx)

    if not commit_order or not file_commits:
        return ""

    out_lines = ["파일별 커밋 변경 이력 ([1]이 oldest, 마지막이 newest):"]
    for file_path, indices in file_commits.items():
        # 인덱스 내림차순 = oldest-first (높은 인덱스가 오래된 커밋)
        oldest_first = sorted(set(indices), reverse=True)
        steps = []
        for rank, idx in enumerate(oldest_first):
            sha, subject = commit_order[idx]
            circle = _CIRCLES[rank] if rank < len(_CIRCLES) else f"({rank + 1})"
            steps.append(f"{circle}[{sha}] {subject[:28]}")
        out_lines.append(f"  {file_path}: {' → '.join(steps)}")
    return "\n".join(out_lines) + "\n"


# 커밋 빈도와 무관하게 항상 key_files 후보로 고려할 파일명 패턴
_IMPORTANT_FILE_NAMES: frozenset[str] = frozenset({
    # 프로젝트 설정 / 빌드
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "package-lock.json",
    "Cargo.toml", "go.mod", "Makefile", "CMakeLists.txt",
    "requirements.txt", "requirements-dev.txt",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    # 진입점
    "__main__.py", "main.py", "entrypoint.py", "wsgi.py", "asgi.py",
    # 설정 / 시크릿
    "settings.py", "config.py", "configuration.py", "secrets.py",
    ".env.example",
    # CI / 배포
    ".github/workflows",  # 디렉토리 prefix 매칭용
    # 문서 / 가이드
    "README.md", "CLAUDE.md", "AGENTS.md",
})


def _detect_important_files(file_paths: list[str]) -> list[str]:
    """파일 경로 목록에서 아키텍처상 중요한 파일을 패턴 기반으로 감지."""
    results: list[str] = []
    for path in file_paths:
        fname = path.rsplit("/", 1)[-1]
        if fname in _IMPORTANT_FILE_NAMES:
            results.append(path)
    return results


_FULL_MAP_PROMPT = """\
다음은 git 저장소의 디렉토리 구조와 변경 빈도 상위 파일입니다.

디렉토리 구조:
{tree_summary}

변경 빈도 상위 파일 (커밋 수 기준):
{hot_files_text}
{important_files_section}
아래 두 가지를 JSON으로 반환하세요:
1. "directories": 디렉토리 경로 → 한 줄 역할 설명 (한국어, 15자 이내)
   - 소스 파일이 실제로 존재하는 디렉토리까지 포함 (최대 4단계)
   - key_files에 포함된 파일의 부모 디렉토리는 반드시 포함
   - 파일이 없는 통과 경로도 계층 표현을 위해 포함
   - directories와 key_files의 경로 형식을 반드시 일치시킬 것 (예: 둘 다 "src/foo/bar" 형식)
2. "key_files": 핵심 파일 경로 → 한 줄 역할 설명 (한국어, 20자 이내)
   - 변경 빈도 상위 파일 중 아키텍처상 중요한 것 + "항상 포함 후보" 파일을 합쳐 최대 15개
   - "항상 포함 후보"에 있는 파일은 커밋 빈도가 낮더라도 반드시 포함할 것
   - 경로는 디렉토리 구조에서 제공된 실제 경로와 동일한 형식 사용

반드시 JSON만 반환 (마크다운 없이):
{{
  "directories": {{
    "src/foo": "메인 앱 레이어",
    "src/foo/bar": "UI 위젯 모음"
  }},
  "key_files": {{
    "pyproject.toml": "프로젝트 빌드·의존성 설정",
    "src/foo/app.py": "앱 진입점·커맨드 라우팅",
    "src/foo/bar/widget.py": "핵심 UI 위젯"
  }}
}}
"""


def stream_full_map_progress(
    tree_summary: str,
    hot_files: list[tuple[str, int]],
    model_override: str = "",
    file_paths: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """전체 프로젝트 맵 생성 — 디렉토리 역할 + 핵심 파일 요약."""
    tracker = TokenUsageCallback()
    cv_token = model_override_var.set(model_override) if model_override else None
    try:
        yield {"type": "node", "label": "프로젝트 구조 분석 중"}

        hot_files_text = "\n".join(
            f"  {path} ({count}회)" for path, count in hot_files[:30]
        )
        important_files: list[str] = (
            _detect_important_files(file_paths) if file_paths else []
        )
        hot_paths = {p for p, _ in hot_files[:30]}
        extra_important = [p for p in important_files if p not in hot_paths]
        if extra_important:
            important_files_section = "\n항상 포함 후보 (설정·진입점 등 중요 파일):\n" + "\n".join(
                f"  {p}" for p in extra_important
            ) + "\n"
        else:
            important_files_section = ""
        prompt = _FULL_MAP_PROMPT.format(
            tree_summary=tree_summary,
            hot_files_text=hot_files_text,
            important_files_section=important_files_section,
        )

        try:
            client = LLMClient()
            raw = client.invoke_json(prompt, callbacks=[tracker])
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        if not isinstance(raw, dict):
            yield {"type": "error", "message": "LLM 응답 형식 오류"}
            return

        directories: dict[str, str] = {
            str(k): str(v) for k, v in raw.get("directories", {}).items()
        }
        key_files: dict[str, str] = {
            str(k): str(v) for k, v in raw.get("key_files", {}).items()
        }
        yield {
            "type": "usage",
            "input_tokens": tracker.input_tokens,
            "output_tokens": tracker.output_tokens,
            "model_name": tracker.model_name,
        }
        yield {"type": "result", "directories": directories, "key_files": key_files}
    finally:
        if cv_token is not None:
            model_override_var.reset(cv_token)


def stream_map_progress(
    commit_context: dict[str, Any],
    model_override: str = "",
) -> Iterator[dict[str, Any]]:
    """파일 역할 요약 생성 — 진행 이벤트를 yield."""
    tracker = TokenUsageCallback()
    cv_token = model_override_var.set(model_override) if model_override else None
    try:
        yield {"type": "node", "label": "파일 역할 분석 중"}

        changed_files_summary = commit_context.get("changed_files_summary", "")
        is_multi_commit = "[Per-commit breakdown]" in changed_files_summary

        if is_multi_commit:
            file_timeline = _build_file_timeline(changed_files_summary)
            file_timeline_section = file_timeline + "\n" if file_timeline else ""
            evolution_instruction = _EVOLUTION_INSTRUCTION
            example = _MULTI_EXAMPLE
        else:
            file_timeline_section = ""
            evolution_instruction = ""
            example = _SINGLE_EXAMPLE

        prompt = _PROMPT.format(
            changed_files_summary=changed_files_summary,
            file_context_text=commit_context.get("file_context_text", "")[:_FILE_CONTEXT_MAX],
            file_timeline_section=file_timeline_section,
            evolution_instruction=evolution_instruction,
            example=example,
        )

        try:
            client = LLMClient()
            raw = client.invoke_json(prompt, callbacks=[tracker])
            summaries: dict[str, str] = (
                {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
            )
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        yield {
            "type": "usage",
            "input_tokens": tracker.input_tokens,
            "output_tokens": tracker.output_tokens,
            "model_name": tracker.model_name,
        }
        yield {"type": "result", "summaries": summaries}
    finally:
        if cv_token is not None:
            model_override_var.reset(cv_token)

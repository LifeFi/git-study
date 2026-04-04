"""Map service: generate file role summaries for a commit range."""

from __future__ import annotations

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

반환 형식: 파일 경로를 키, 설명(한국어)을 값으로 하는 JSON 객체.
- 변경된 파일만 포함.
- 마크다운 없이 JSON만 반환.

예시:
{{
  "src/foo/bar.py": "CommandBar 위젯 — ESC 모드 전환 버그 수정",
  "src/foo/app.py": "앱 진입점 — 스레드 분기 추가, 세션 복원 로직 개선",
  "README.md": "프로젝트 소개 문서"
}}
"""

_FILE_CONTEXT_MAX = 6000

_FULL_MAP_PROMPT = """\
다음은 git 저장소의 디렉토리 구조와 변경 빈도 상위 파일입니다.

디렉토리 구조:
{tree_summary}

변경 빈도 상위 파일 (커밋 수 기준):
{hot_files_text}

아래 두 가지를 JSON으로 반환하세요:
1. "directories": 디렉토리 경로 → 한 줄 역할 설명 (한국어, 15자 이내)
   - 최상위 ~ 2단계 디렉토리만 포함
2. "key_files": 핵심 파일 경로 → 한 줄 역할 설명 (한국어, 20자 이내)
   - 변경 빈도 상위 파일 중 아키텍처상 중요한 것만, 최대 10개

반드시 JSON만 반환 (마크다운 없이):
{{
  "directories": {{
    "src/foo": "메인 앱 레이어",
    "src/foo/bar": "UI 위젯 모음"
  }},
  "key_files": {{
    "src/foo/app.py": "앱 진입점·커맨드 라우팅",
    "src/foo/bar/widget.py": "핵심 UI 위젯"
  }}
}}
"""


def stream_full_map_progress(
    tree_summary: str,
    hot_files: list[tuple[str, int]],
    model_override: str = "",
) -> Iterator[dict[str, Any]]:
    """전체 프로젝트 맵 생성 — 디렉토리 역할 + 핵심 파일 요약."""
    tracker = TokenUsageCallback()
    cv_token = model_override_var.set(model_override) if model_override else None
    try:
        yield {"type": "node", "label": "프로젝트 구조 분석 중"}

        hot_files_text = "\n".join(
            f"  {path} ({count}회)" for path, count in hot_files[:30]
        )
        prompt = _FULL_MAP_PROMPT.format(
            tree_summary=tree_summary,
            hot_files_text=hot_files_text,
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

        prompt = _PROMPT.format(
            changed_files_summary=commit_context.get("changed_files_summary", ""),
            file_context_text=commit_context.get("file_context_text", "")[:_FILE_CONTEXT_MAX],
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

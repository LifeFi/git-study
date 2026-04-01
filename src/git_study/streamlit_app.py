"""Streamlit 채팅 앱 - Git 커밋 학습 과제용."""

from __future__ import annotations


def run_chat() -> None:
    """CLI 진입점: streamlit run으로 앱 실행."""
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", __file__],
        check=True,
    )


import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# 페이지 설정
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Git Study Chat",
    page_icon="🗂️",
    layout="wide",
)

# ──────────────────────────────────────────────────────────────────────────────
# 세션 초기화
# ──────────────────────────────────────────────────────────────────────────────
def _init_state() -> None:
    defaults: dict = {
        "repo_root": "",
        "commits": [],
        "oldest_sha": None,   # 범위의 오래된 끝
        "newest_sha": None,   # 범위의 최신 끝
        "commit_context": {},
        "thread_id": str(uuid.uuid4()),
        "messages": [],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


_init_state()


# ──────────────────────────────────────────────────────────────────────────────
# 도우미 함수
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def _load_commits(repo_root: str, limit: int = 30) -> list[dict]:
    from git_study.domain.repo_context import get_commit_list_snapshot

    snap = get_commit_list_snapshot(
        limit=limit,
        repo_source="local",
        local_repo_root=repo_root,
        refresh_remote=False,
    )
    return snap["commits"]


def _build_context_range(repo_root: str, oldest_sha: str, newest_sha: str) -> dict:
    """단일 커밋 또는 범위 컨텍스트 빌드."""
    from git_study.domain.repo_context import get_repo, build_commit_context, build_multi_commit_context

    repo = get_repo("local", local_repo_root=repo_root)

    if oldest_sha == newest_sha:
        commit = repo.commit(oldest_sha)
        return build_commit_context(commit, "selected_commit", repo)

    # 커밋 목록에서 범위 내 커밋 추출 (newest-first 순)
    commits = st.session_state.commits
    sha_to_idx = {c["sha"]: i for i, c in enumerate(commits)}
    newest_idx = sha_to_idx.get(newest_sha, 0)
    oldest_idx = sha_to_idx.get(oldest_sha, len(commits) - 1)
    # newest_idx <= oldest_idx (리스트가 newest-first이므로)
    if newest_idx > oldest_idx:
        newest_idx, oldest_idx = oldest_idx, newest_idx
    range_shas = [c["sha"] for c in commits[newest_idx : oldest_idx + 1]]
    range_commits = [repo.commit(sha) for sha in range_shas]
    return build_multi_commit_context(range_commits, "selected_range", repo)


def _context_summary(ctx: dict) -> str:
    """stream_chat에 넘길 commit_context 문자열."""
    lines = [
        f"커밋: {ctx.get('commit_sha', '')[:40]}",
        f"제목: {ctx.get('commit_subject', '')}",
        f"작성자: {ctx.get('commit_author', '')}",
        f"날짜: {ctx.get('commit_date', '')}",
        "",
        "── 변경 파일 ──",
        ctx.get("changed_files_summary", "(없음)"),
        "",
        "── Diff ──",
        ctx.get("diff_text", "(없음)"),
        "",
        "── 파일 컨텍스트 ──",
        ctx.get("file_context_text", "(없음)"),
    ]
    return "\n".join(lines)


def _range_key() -> str:
    return f"{st.session_state.oldest_sha}..{st.session_state.newest_sha}"


def _new_thread() -> None:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.messages = []


# ──────────────────────────────────────────────────────────────────────────────
# 사이드바
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🗂️ Git Study")

    # ── 저장소 설정 ────────────────────────────────────────────────────────────
    st.subheader("저장소 설정")
    repo_input = st.text_input(
        ".git 경로 (폴더 경로)",
        value=st.session_state.repo_root,
        placeholder="/path/to/your/project",
        help="로컬 Git 저장소 루트 경로를 입력하세요. ~ 사용 가능.",
    )

    if st.button("저장소 열기", use_container_width=True):
        path = Path(repo_input.strip()).expanduser().resolve()
        if not path.exists():
            st.error("경로가 존재하지 않습니다.")
        else:
            try:
                from git import Repo
                Repo(str(path), search_parent_directories=True)
                st.session_state.repo_root = str(path)
                st.session_state.oldest_sha = None
                st.session_state.newest_sha = None
                st.session_state.commit_context = {}
                _load_commits.clear()
                st.rerun()
            except Exception:
                st.error(".git 저장소를 찾을 수 없습니다.")

    # ── 커밋 범위 선택 ─────────────────────────────────────────────────────────
    if st.session_state.repo_root:
        st.divider()
        st.subheader("커밋 범위 선택")

        try:
            commits = _load_commits(st.session_state.repo_root)
            st.session_state.commits = commits
        except Exception as e:
            st.error(f"커밋 로드 실패: {e}")
            commits = []

        if commits:
            # (label → sha) 매핑, 리스트는 newest-first
            sha_list = [c["sha"] for c in commits]
            label_list = [
                f"{c['short_sha']}  {c['subject'][:42]}" for c in commits
            ]

            def _sha_to_idx(sha: str | None) -> int:
                if sha and sha in sha_list:
                    return sha_list.index(sha)
                return 0

            # 최신 커밋 (끝)
            newest_idx = _sha_to_idx(st.session_state.newest_sha)
            newest_label = st.selectbox(
                "끝 (최신)",
                label_list,
                index=newest_idx,
                key="sb_newest",
                help="범위의 최신 커밋",
            )
            new_newest_sha = sha_list[label_list.index(newest_label)]

            # 오래된 커밋 (시작) — 끝보다 오래된 것만 선택 가능
            newest_selected_idx = label_list.index(newest_label)
            older_labels = label_list[newest_selected_idx:]  # newest-first이므로 이후가 더 오래됨
            older_shas = sha_list[newest_selected_idx:]

            oldest_idx_in_older = 0
            if st.session_state.oldest_sha in older_shas:
                oldest_idx_in_older = older_shas.index(st.session_state.oldest_sha)

            oldest_label = st.selectbox(
                "시작 (오래된)",
                older_labels,
                index=oldest_idx_in_older,
                key="sb_oldest",
                help="범위의 가장 오래된 커밋",
            )
            new_oldest_sha = older_shas[older_labels.index(oldest_label)]

            # 범위가 바뀌면 컨텍스트 + 대화 초기화
            if (
                new_newest_sha != st.session_state.newest_sha
                or new_oldest_sha != st.session_state.oldest_sha
            ):
                st.session_state.newest_sha = new_newest_sha
                st.session_state.oldest_sha = new_oldest_sha
                st.session_state.commit_context = {}
                _new_thread()

            # 선택된 범위 커밋 수 표시
            n_newest = sha_list.index(new_newest_sha)
            n_oldest = sha_list.index(new_oldest_sha)
            n_commits = n_oldest - n_newest + 1
            if n_commits == 1:
                st.caption("단일 커밋 선택됨")
            else:
                st.caption(f"{n_commits}개 커밋 범위 선택됨")

    # ── 대화 관리 ──────────────────────────────────────────────────────────────
    if st.session_state.newest_sha:
        st.divider()
        st.subheader("대화")
        st.caption(f"Thread: `{st.session_state.thread_id[:8]}…`")
        if st.button("새 대화 시작", use_container_width=True):
            _new_thread()
            st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# 메인 영역
# ──────────────────────────────────────────────────────────────────────────────
if not st.session_state.repo_root:
    st.info("👈 사이드바에서 Git 저장소 경로를 입력하고 **저장소 열기**를 눌러주세요.")
    st.stop()

if not st.session_state.newest_sha:
    st.info("👈 사이드바에서 학습할 커밋 범위를 선택해주세요.")
    st.stop()

# 커밋 컨텍스트 로드 (범위 변경 시 재빌드)
if not st.session_state.commit_context:
    with st.spinner("커밋 컨텍스트 로드 중…"):
        try:
            ctx = _build_context_range(
                st.session_state.repo_root,
                st.session_state.oldest_sha,
                st.session_state.newest_sha,
            )
            st.session_state.commit_context = ctx
        except Exception as e:
            st.error(f"컨텍스트 빌드 실패: {e}")
            st.stop()

ctx = st.session_state.commit_context

# 커밋 정보 헤더
with st.expander(
    f"📌 {ctx.get('commit_subject', '')} — `{ctx.get('commit_sha', '')[:20]}`",
    expanded=False,
):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**작성자:** {ctx.get('commit_author', '')}")
        st.markdown(f"**날짜:** {ctx.get('commit_date', '')}")
    with col2:
        st.markdown("**변경 파일:**")
        st.code(ctx.get("changed_files_summary", "(없음)"), language=None)

st.divider()

# ── 채팅 히스토리 표시 ────────────────────────────────────────────────────────
for msg in st.session_state.messages:
    role = msg["role"]

    if role == "tool":
        with st.expander(f"🔧 도구 결과: `{msg.get('name', '')}`", expanded=False):
            st.code(msg["content"], language=None)
    elif role == "human":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    elif role == "ai":
        with st.chat_message("assistant"):
            if meta := msg.get("meta"):
                if route_label := meta.get("route_label"):
                    st.caption(f"💡 {route_label}")
            st.markdown(msg["content"])

# ── 입력창 ────────────────────────────────────────────────────────────────────
user_input = st.chat_input("질문을 입력하세요…")

if user_input:
    st.session_state.messages.append({"role": "human", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    from git_study.services.chat_service import stream_chat

    commit_context_str = _context_summary(ctx) if ctx else ""
    route_label = None
    full_response = ""

    with st.chat_message("assistant"):
        route_placeholder = st.empty()
        response_placeholder = st.empty()

        try:
            for event in stream_chat(
                thread_id=st.session_state.thread_id,
                user_text=user_input,
                commit_context=commit_context_str,
                oldest_sha=st.session_state.oldest_sha or "",
                newest_sha=st.session_state.newest_sha or "",
                local_repo_root=Path(st.session_state.repo_root),
                repo_source="local",
            ):
                etype = event.get("type")

                if etype == "route":
                    route_label = event.get("label", "")
                    route_placeholder.caption(f"💡 {route_label}")

                elif etype == "token":
                    full_response += event["content"]
                    response_placeholder.markdown(full_response + "▌")

                elif etype == "tool_call":
                    tool_name = event.get("name", "")
                    with st.expander(f"🔧 도구 호출: `{tool_name}`", expanded=False):
                        st.json(event.get("args", {}))

                elif etype == "tool_result":
                    with st.expander("🔧 도구 결과", expanded=False):
                        st.code(event["content"], language=None)
                    st.session_state.messages.append({
                        "role": "tool",
                        "name": "",
                        "content": event["content"],
                    })

                elif etype == "done":
                    full_response = event.get("full_content", full_response)
                    response_placeholder.markdown(full_response)

                elif etype == "error":
                    st.error(f"오류: {event['content']}")
                    break

        except Exception as e:
            st.error(f"스트리밍 오류: {e}")

    if full_response:
        st.session_state.messages.append({
            "role": "ai",
            "content": full_response,
            "meta": {"route_label": route_label},
        })

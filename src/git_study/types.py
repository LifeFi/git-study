from typing import Literal, NotRequired, TypedDict


class CommitListSnapshot(TypedDict):
    commits: list[dict[str, str]]
    has_more_commits: bool
    total_commit_count: int


class CommitHead(TypedDict):
    sha: str
    short_sha: str
    subject: str
    author: str
    date: str


class RemoteRepoCacheEntry(TypedDict):
    slug: str
    repo_url: str
    cache_path: str
    last_used_at: float
    last_used_label: str


class InlineQuizQuestion(TypedDict):
    id: str
    file_path: str
    anchor_snippet: str
    anchor_line: NotRequired[int]
    question: str
    expected_answer: str
    question_type: str


class InlineQuizGrade(TypedDict):
    id: str
    score: int
    feedback: str


class GradingSummary(TypedDict, total=False):
    weak_points: list[str]
    weak_files: list[str]
    next_steps: list[str]
    overall_comment: str


class GeneralQuizQuestion(TypedDict, total=False):
    id: str
    question: str
    expected_answer: str
    question_type: str
    explanation: str
    code_snippet: str
    code_language: str
    code_reference: str
    choices: list[str]


class GeneralQuizGrade(TypedDict):
    id: str
    score: int
    feedback: str


class ChatEvent(TypedDict):
    kind: Literal[
        "app_command",       # /quiz, /commits 등 앱 명령어
        "app_result",        # 앱 결과 메시지 (기존 result/markdown 대응)
        "user_message",      # LLM에 보내는 자유 텍스트
        "assistant_message", # LLM 응답
        "tool_call",         # LLM이 호출한 tool
        "tool_result",       # tool 실행 결과
        "separator",
    ]
    content: str
    data: NotRequired[dict]   # tool_calls 구조체 등 구조화 데이터
    style: NotRequired[str]   # app_result 타입일 때: "info" | "success" | "error"
    timestamp: str

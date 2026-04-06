"""Command parsing for the tui_v2 command bar."""

import re
from dataclasses import dataclass
from typing import Literal

CommandKind = Literal["quiz", "quiz_clear", "quiz_retry", "quiz_list", "grade", "review", "map", "help", "commits", "answer", "exit", "repo", "apikey", "model", "clear", "resume", "hook", "chat", "unknown"]

# @foo.py[43-80] 또는 @foo.py (라인 범위 없는 형태)
_MENTION_RE = re.compile(r'@([^\[\s]+)(?:\[(\d+)-(\d+)\])?')
_MODEL_RE = re.compile(r'--model(?:=|\s+)(\S+)')


QUIZ_COUNT_DEFAULT = 3
QUIZ_COUNT_MIN = 1
QUIZ_COUNT_MAX = 10


@dataclass(frozen=True)
class ParsedCommand:
    kind: CommandKind
    range_arg: str = ""  # "/quiz HEAD~3" -> "HEAD~3"
    raw: str = ""
    mentioned_files: tuple = ()
    # tuple of (file_path: str, start_line: int, end_line: int)
    # end_line=0 means entire file
    quiz_count: int = QUIZ_COUNT_DEFAULT
    author_context: str = "self"  # "self" | "others" | "ai"
    refresh: bool = False
    full_map: bool = False
    model_override: str = ""  # --model=xxx 로 지정된 일회성 모델


def parse_command(text: str) -> ParsedCommand:
    """Parse user input into a structured command.

    /quiz        -> kind="quiz", range_arg=""
    /quiz HEAD~3 -> kind="quiz", range_arg="HEAD~3"
    /grade       -> kind="grade"
    /help, ?     -> kind="help"
    otherwise    -> kind="unknown"
    """
    text = text.strip()
    if text in ("?", "/help"):
        return ParsedCommand(kind="help", raw=text)
    if text.startswith("/review"):
        m = _MODEL_RE.search(text)
        arg = _MODEL_RE.sub("", text[len("/review"):]).strip() if m else text[len("/review"):].strip()
        return ParsedCommand(
            kind="review",
            range_arg=arg,
            raw=text,
            model_override=m.group(1) if m else "",
        )
    if text.startswith("/quiz"):
        parts = text.split(None, 1)
        arg = parts[1] if len(parts) > 1 else ""
        if arg == "clear":
            return ParsedCommand(kind="quiz_clear", raw=text)
        if arg == "retry":
            return ParsedCommand(kind="quiz_retry", raw=text)
        if arg == "list":
            return ParsedCommand(kind="quiz_list", raw=text)
        quiz_count = QUIZ_COUNT_DEFAULT
        author_context = "self"
        range_arg = arg
        model_override = ""
        if arg:
            m = _MODEL_RE.search(arg)
            if m:
                model_override = m.group(1)
                arg = _MODEL_RE.sub("", arg).strip()
            tokens = arg.split()
            if "--ai" in tokens:
                author_context = "ai"
                tokens = [t for t in tokens if t != "--ai"]
            elif "--others" in tokens:
                author_context = "others"
                tokens = [t for t in tokens if t != "--others"]
            if tokens and tokens[-1].isdigit():
                quiz_count = max(QUIZ_COUNT_MIN, min(int(tokens[-1]), QUIZ_COUNT_MAX))
                tokens = tokens[:-1]
            range_arg = " ".join(tokens)
        return ParsedCommand(
            kind="quiz",
            range_arg=range_arg,
            raw=text,
            quiz_count=quiz_count,
            author_context=author_context,
            model_override=model_override,
        )
    if text.startswith("/map"):
        refresh = "--refresh" in text
        full_map = "--full" in text
        m = _MODEL_RE.search(text)
        return ParsedCommand(kind="map", raw=text, refresh=refresh, full_map=full_map, model_override=m.group(1) if m else "")
    if text.startswith("/grade"):
        m = _MODEL_RE.search(text)
        return ParsedCommand(kind="grade", raw=text, model_override=m.group(1) if m else "")
    if text.startswith("/commits"):
        return ParsedCommand(kind="commits", raw=text)
    if text.startswith("/answer"):
        return ParsedCommand(kind="answer", raw=text)
    if text.startswith("/repo"):
        parts = text.split(None, 1)
        return ParsedCommand(kind="repo", range_arg=parts[1] if len(parts) > 1 else "", raw=text)
    if text.startswith("/apikey"):
        parts = text.split(None, 1)
        return ParsedCommand(kind="apikey", range_arg=parts[1] if len(parts) > 1 else "", raw=text)
    if text.startswith("/model"):
        parts = text.split(None, 1)
        return ParsedCommand(kind="model", range_arg=parts[1] if len(parts) > 1 else "", raw=text)
    if text in ("/exit", "/quit"):
        return ParsedCommand(kind="exit", raw=text)
    if text.startswith("/clear"):
        return ParsedCommand(kind="clear", raw=text)
    if text.startswith("/resume"):
        return ParsedCommand(kind="resume", raw=text)
    if text.startswith("/hook"):
        parts = text.split(None, 1)
        return ParsedCommand(kind="hook", range_arg=parts[1] if len(parts) > 1 else "", raw=text)
    if text and not text.startswith("/"):
        mentions = tuple(
            (m.group(1), int(m.group(2) or 0), int(m.group(3) or 0))
            for m in _MENTION_RE.finditer(text)
        )
        return ParsedCommand(kind="chat", range_arg=text, raw=text, mentioned_files=mentions)
    return ParsedCommand(kind="unknown", raw=text)

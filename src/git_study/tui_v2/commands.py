"""Command parsing for the tui_v2 command bar."""

from dataclasses import dataclass
from typing import Literal

CommandKind = Literal["quiz", "grade", "help", "commits", "answer", "unknown"]


@dataclass(frozen=True)
class ParsedCommand:
    kind: CommandKind
    range_arg: str = ""  # "/quiz HEAD~3" -> "HEAD~3"
    raw: str = ""


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
    if text.startswith("/quiz"):
        parts = text.split(None, 1)
        return ParsedCommand(
            kind="quiz",
            range_arg=parts[1] if len(parts) > 1 else "",
            raw=text,
        )
    if text.startswith("/grade"):
        return ParsedCommand(kind="grade", raw=text)
    if text.startswith("/commits"):
        return ParsedCommand(kind="commits", raw=text)
    if text.startswith("/answer"):
        return ParsedCommand(kind="answer", raw=text)
    return ParsedCommand(kind="unknown", raw=text)

from git_study.domain.inline_anchor import (
    extract_file_paths_from_summary,
    normalize_anchor_snippet,
    parse_file_context_blocks,
    snippet_exists_in_content,
)


def test_extract_file_paths_from_summary_parses_only_paths() -> None:
    summary = "\n".join(
        [
            "src/a.py | +10 -2 (lines changed: 12)",
            "README.md | +3 -0 (lines changed: 3)",
            "No changed files.",
        ]
    )

    assert extract_file_paths_from_summary(summary) == ["src/a.py", "README.md"]


def test_parse_file_context_blocks_extracts_multiple_files() -> None:
    file_context_text = """
FILE: src/a.py
```python
def a():
    return 1
```

FILE: src/b.py
```python
def b():
    return 2
```
""".strip()

    parsed = parse_file_context_blocks(file_context_text)

    assert parsed == {
        "src/a.py": "def a():\n    return 1",
        "src/b.py": "def b():\n    return 2",
    }


def test_normalize_anchor_snippet_trims_trailing_spaces() -> None:
    snippet = "if ok:   \n    return value   \n"

    assert normalize_anchor_snippet(snippet) == "if ok:\n    return value"


def test_snippet_exists_in_content_matches_normalized_lines() -> None:
    content = "def run():\n    if ok:\n        return value\n"
    snippet = "if ok:   \n        return value   "

    assert snippet_exists_in_content(content, snippet) is True

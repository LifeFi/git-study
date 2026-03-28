from git_study.llm.client import extract_json_block


def test_extract_json_block_ignores_trailing_text_after_object() -> None:
    raw = """
Here is the result:
{
  "is_valid": true,
  "issues": [],
  "revision_instruction": ""
}

Additional note that should be ignored.
""".strip()

    assert extract_json_block(raw) == (
        '{\n  "is_valid": true,\n  "issues": [],\n  "revision_instruction": ""\n}'
    )


def test_extract_json_block_ignores_second_json_value() -> None:
    raw = """
{
  "is_valid": true
}
{
  "debug": "extra"
}
""".strip()

    assert extract_json_block(raw) == '{\n  "is_valid": true\n}'

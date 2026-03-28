import pytest

from git_study.llm.client import LLMClient, extract_json_block


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


def test_invoke_json_raises_friendly_error_when_response_is_not_json(monkeypatch) -> None:
    client = object.__new__(LLMClient)
    monkeypatch.setattr(client, "invoke_text", lambda prompt: "not json at all")

    with pytest.raises(ValueError) as exc_info:
        client.invoke_json("prompt")

    assert "LLM이 JSON 응답 대신" in str(exc_info.value)
    assert "response_preview=not json at all" in str(exc_info.value)


def test_invoke_structured_retries_once_with_stronger_instruction() -> None:
    client = object.__new__(LLMClient)
    prompts: list[str] = []

    class StubStructuredLLM:
        def invoke(self, prompt: str):
            prompts.append(prompt)
            if len(prompts) == 1:
                raise RuntimeError("bad structured response")
            return {"ok": True}

    class StubLLM:
        def with_structured_output(self, schema, **kwargs):
            return StubStructuredLLM()

    client._llm = StubLLM()

    result = client.invoke_structured("prompt", dict)

    assert result == {"ok": True}
    assert prompts[0] == "prompt"
    assert "IMPORTANT: Return only data that matches the required schema exactly." in prompts[1]


def test_invoke_structured_raises_friendly_error_after_retry_failure() -> None:
    client = object.__new__(LLMClient)

    class StubStructuredLLM:
        def invoke(self, prompt: str):
            raise RuntimeError("still bad")

    class StubLLM:
        def with_structured_output(self, schema, **kwargs):
            return StubStructuredLLM()

    client._llm = StubLLM()

    with pytest.raises(ValueError) as exc_info:
        client.invoke_structured("prompt", dict)

    assert "LLM structured output 호출이 실패했습니다." in str(exc_info.value)


def test_invoke_structured_falls_back_to_raw_json_payload(monkeypatch) -> None:
    client = object.__new__(LLMClient)

    class StubStructuredLLM:
        def invoke(self, prompt: str):
            raise RuntimeError("structured failed")

    class StubLLM:
        def with_structured_output(self, schema, **kwargs):
            return StubStructuredLLM()

    client._llm = StubLLM()
    monkeypatch.setattr(client, "invoke_json", lambda prompt: {"ok": True})

    result = client.invoke_structured("prompt", dict)

    assert result == {"ok": True}


def test_invoke_structured_tries_relaxed_structured_before_json(monkeypatch) -> None:
    client = object.__new__(LLMClient)
    calls: list[bool | None] = []

    class StrictStructuredLLM:
        def invoke(self, prompt: str):
            calls.append(True)
            raise RuntimeError("strict failed")

    class RelaxedStructuredLLM:
        def invoke(self, prompt: str):
            calls.append(False)
            return {"ok": True}

    class StubLLM:
        def with_structured_output(self, schema, **kwargs):
            if kwargs.get("strict") is False:
                return RelaxedStructuredLLM()
            return StrictStructuredLLM()

    client._llm = StubLLM()
    monkeypatch.setattr(
        client,
        "invoke_json",
        lambda prompt: pytest.fail("invoke_json should not be called"),
    )

    result = client.invoke_structured("prompt", dict)

    assert result == {"ok": True}
    assert calls == [True, True, False]

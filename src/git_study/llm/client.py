import json
import re
from contextvars import ContextVar
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, ToolMessage

from ..secrets import get_openai_api_key
from ..settings import DEFAULT_MODEL, load_settings

# 서비스 호출 단위로 모델을 일시 오버라이드할 때 사용 (ContextVar — 스레드 안전)
model_override_var: ContextVar[str] = ContextVar("model_override", default="")


def extract_json_block(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()
    stripped = text.strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return stripped[index : index + end]
    return stripped


class LLMClient:
    def __init__(self) -> None:
        settings = load_settings()
        api_key, source = get_openai_api_key()
        if not api_key:
            raise ValueError(
                "OpenAI API key is not configured. "
                "Set OPENAI_API_KEY or configure it in the app's API Key settings."
            )
        model = model_override_var.get() or settings.get("model", DEFAULT_MODEL)
        self._llm = init_chat_model(
            model,
            model_provider="openai",
            api_key=api_key,
            tags=[f"api_key_source:{source}"],
        )

    def invoke_text(self, prompt: str, *, callbacks: list | None = None) -> str:
        config = {"callbacks": callbacks} if callbacks else None
        response = self._llm.invoke(prompt, config=config)
        return str(response.content)

    def invoke_json(self, prompt: str, *, callbacks: list | None = None) -> Any:
        raw = self.invoke_text(prompt, callbacks=callbacks)
        extracted = extract_json_block(raw)
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as exc:
            preview = " ".join(raw.split())[:160]
            if not preview:
                preview = "<empty>"
            raise ValueError(
                "LLM이 JSON 응답 대신 비어 있거나 파싱할 수 없는 텍스트를 반환했습니다. "
                f"response_preview={preview}"
            ) from exc

    def invoke_json_with_tools(
        self,
        prompt: str,
        tools: list[Any],
        *,
        require_tool: bool = False,
        max_rounds: int = 4,
    ) -> Any:
        messages = [HumanMessage(content=prompt)]
        tool_by_name = {
            str(getattr(tool, "name", "")): tool for tool in tools if getattr(tool, "name", "")
        }
        bound_llm = self._llm.bind_tools(tools)
        tool_called = False
        reminder_sent = False

        for _ in range(max_rounds):
            response = bound_llm.invoke(messages)
            messages.append(response)
            tool_calls = list(getattr(response, "tool_calls", []) or [])

            if tool_calls:
                tool_called = True
                for call in tool_calls:
                    tool_name = str(call.get("name", "")).strip()
                    tool_call_id = str(call.get("id", "")).strip()
                    tool_args = call.get("args", {})
                    tool = tool_by_name.get(tool_name)
                    if tool is None:
                        result = f"Tool '{tool_name}' is not available."
                    else:
                        try:
                            result = tool.invoke(tool_args)
                        except Exception as exc:
                            result = f"Tool '{tool_name}' failed: {exc}"
                    messages.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call_id,
                            name=tool_name or None,
                        )
                    )
                continue

            if require_tool and not tool_called and not reminder_sent:
                messages.append(
                    HumanMessage(
                        content=(
                            "You must call the get_neighbor_code_context tool at least once "
                            "before returning the final JSON response."
                        )
                    )
                )
                reminder_sent = True
                continue

            raw = str(response.content)
            extracted = extract_json_block(raw)
            try:
                return json.loads(extracted)
            except json.JSONDecodeError as exc:
                preview = " ".join(raw.split())[:160]
                if not preview:
                    preview = "<empty>"
                raise ValueError(
                    "LLM이 tool 호출 후 JSON 응답 대신 비어 있거나 파싱할 수 없는 텍스트를 반환했습니다. "
                    f"response_preview={preview}"
                ) from exc

        raise ValueError("LLM tool 호출이 반복되어 종료되지 않았습니다.")

    def invoke_structured(
        self,
        prompt: str,
        schema: Any,
        *,
        method: str = "json_schema",
        strict: bool | None = True,
    ) -> Any:
        retry_prompt = (
            f"{prompt}\n\n"
            "IMPORTANT: Return only data that matches the required schema exactly. "
            "Do not add markdown, commentary, or extra wrapper text."
        )
        structured_llm = self._llm.with_structured_output(
            schema,
            method=method,
            strict=strict,
        )
        def _is_unretryable(e: Exception) -> bool:
            s = str(e)
            return "context_length_exceeded" in s or (
                "400" in s and "maximum context length" in s
            )

        try:
            return structured_llm.invoke(prompt)
        except Exception as exc:
            if _is_unretryable(exc):
                raise
            original_error = str(exc).strip()
            try:
                return structured_llm.invoke(retry_prompt)
            except Exception as retry_exc:
                if _is_unretryable(retry_exc):
                    raise retry_exc
                try:
                    relaxed_structured_llm = self._llm.with_structured_output(
                        schema,
                        method=method,
                        strict=False,
                    )
                    return relaxed_structured_llm.invoke(retry_prompt)
                except Exception:
                    try:
                        # json_schema 미지원 모델(gpt-4, gpt-3.5-turbo 등) 대응
                        json_mode_llm = self._llm.with_structured_output(
                            schema,
                            method="json_mode",
                        )
                        return json_mode_llm.invoke(retry_prompt)
                    except Exception:
                        try:
                            return self.invoke_json(retry_prompt)
                        except Exception as json_exc:
                            detail = str(json_exc).strip()
                            if len(detail) > 200:
                                detail = f"{detail[:200]}..."
                            root = original_error[:200] if len(original_error) > 200 else original_error
                            raise ValueError(
                                "LLM structured output 호출이 실패했습니다. "
                                "모델이 스키마에 맞는 응답을 반환하지 못했습니다. "
                                f"원인: {root} | fallback_error={detail or str(retry_exc)}"
                            ) from exc

import json
import re
from typing import Any

from langchain.chat_models import init_chat_model

from ..secrets import get_openai_api_key
from ..settings import DEFAULT_MODEL, load_settings


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
        api_key, source = get_openai_api_key(settings.get("openai_api_key_mode"))
        if not api_key:
            raise ValueError(
                "OpenAI API key is not configured. "
                "Set OPENAI_API_KEY or configure it in the app's API Key settings."
            )
        model = settings.get("model", DEFAULT_MODEL)
        self._llm = init_chat_model(
            model,
            model_provider="openai",
            api_key=api_key,
            tags=[f"api_key_source:{source}"],
        )

    def invoke_text(self, prompt: str) -> str:
        response = self._llm.invoke(prompt)
        return str(response.content)

    def invoke_json(self, prompt: str) -> Any:
        raw = self.invoke_text(prompt)
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
        try:
            return structured_llm.invoke(prompt)
        except Exception as exc:
            try:
                return structured_llm.invoke(retry_prompt)
            except Exception as retry_exc:
                try:
                    relaxed_structured_llm = self._llm.with_structured_output(
                        schema,
                        method=method,
                        strict=False,
                    )
                    return relaxed_structured_llm.invoke(retry_prompt)
                except Exception:
                    try:
                        return self.invoke_json(retry_prompt)
                    except Exception as json_exc:
                        detail = str(json_exc).strip()
                        if len(detail) > 220:
                            detail = f"{detail[:220]}..."
                        raise ValueError(
                            "LLM structured output 호출이 실패했습니다. "
                            "모델이 스키마에 맞는 응답을 반환하지 못했습니다. "
                            f"fallback_error={detail or str(retry_exc)}"
                        ) from exc

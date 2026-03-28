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
        return json.loads(extract_json_block(raw))

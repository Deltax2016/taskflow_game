"""Клиент LLM через OpenRouter (OpenAI-совместимый Chat Completions API).

Слой, который прячет за собой конкретного провайдера. Агент вызывает `complete`
и `complete_structured`, ничего не зная про OpenRouter, ключи и HTTP.
"""

import json

import httpx

from app.agent.base import LLM, TModel
from app.agent.schemas import ChatTurn
from app.config import Settings

# OpenRouter понимает те же роли, что и OpenAI: system / user / assistant.
# У нас во внутренней истории роли user/agent/human — маппим их в user/assistant.
_ROLE_MAP = {"user": "user", "agent": "assistant", "human": "assistant"}


class OpenRouterLLM(LLM):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            # OpenRouter просит (опционально) указывать источник трафика:
            "HTTP-Referer": "http://localhost",
            "X-Title": "Support Agent Workshop",
        }

    async def _chat(self, payload: dict) -> dict:
        """Единая точка похода в API — с таймаутом и понятной ошибкой."""
        url = f"{self._settings.openrouter_base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def _build_messages(self, system: str, messages: list[ChatTurn]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for turn in messages:
            out.append({"role": _ROLE_MAP.get(turn.role, "user"), "content": turn.content})
        return out

    async def complete(self, system: str, messages: list[ChatTurn]) -> str:
        payload = {
            "model": self._settings.llm_model,
            "temperature": self._settings.llm_temperature,
            "messages": self._build_messages(system, messages),
        }
        data = await self._chat(payload)
        return data["choices"][0]["message"]["content"].strip()

    async def complete_structured(
        self, system: str, messages: list[ChatTurn], schema: type[TModel]
    ) -> TModel:
        """Просим модель вернуть JSON строго по схеме и валидируем результат.

        Два уровня надёжности:
          1) response_format=json_object — модель гарантированно вернёт валидный JSON;
          2) schema.model_validate_json — проверяем, что поля соответствуют контракту.
        Если модель вернёт не то — pydantic бросит ValidationError, и агент решит,
        что делать (у нас — эскалация на человека).
        """
        # Подсказываем модели ожидаемую форму ответа прямо в system-промпте.
        json_schema = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
        system_with_schema = (
            f"{system}\n\n"
            "Ответь СТРОГО одним JSON-объектом по следующей JSON Schema, "
            "без markdown и пояснений:\n"
            f"{json_schema}"
        )
        payload = {
            "model": self._settings.llm_model,
            "temperature": self._settings.llm_temperature,
            "response_format": {"type": "json_object"},
            "messages": self._build_messages(system_with_schema, messages),
        }
        data = await self._chat(payload)
        raw = data["choices"][0]["message"]["content"]
        return schema.model_validate_json(raw)

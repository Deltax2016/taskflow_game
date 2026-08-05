"""Абстракция инструмента (tool) — контракт между LLM tool-calling и кодом.

Ключевая мысль лекции: контроль доступа живёт В КОДЕ инструмента, а не в
промпте. LLM может «попросить» вызвать `create_refund` на любую сумму — эта
просьба долетит до обычной Python-функции, и уже ОНА решает, выполнить
действие сразу, потребовать подтверждения человека или отказать. Промпт
(и spotlighting, см. `app/security/`) влияет на то, ЧТО МОДЕЛЬ ПОПРОСИТ;
код инструмента решает, ЧТО РЕАЛЬНО ПРОИЗОЙДЁТ. Если эти два уровня совпадают
только "как правило" — рано или поздно они разойдутся, и разойтись должны
безопасно (код побеждает промпт), а не наоборот.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


class ToolError(Exception):
    """Инструмент отказал по СВОЕЙ политике — ожидаемый исход, не баг.

    Ловится ВНУТРИ узла `dispatch_tool` и превращается в обычный текстовый
    результат инструмента ("Ошибка инструмента: ...") — модель видит, что
    вызов не удался, и решает, что делать дальше (попробовать иначе,
    переспросить, эскалировать). Использовать для: невалидных аргументов,
    неизвестного инструмента/провайдера, отказа SSRF-проверки, превышения
    политики (не про сеть — про смысл).

    Временные сетевые сбои (таймаут, 429, 5xx) — это НЕ ToolError. Такие
    исключения (`httpx.*`) должны пробрасываться из обработчика как есть —
    их ловит `RetryPolicy` узла `dispatch_tool` (см. `agent/resilience.py`),
    а не сам инструмент. Смешивать эти два случая в одном классе означало бы
    либо ретраить логические отказы (бессмысленно), либо не ретраить сетевые
    (жаль трафик) — граница должна быть явной.
    """


# Аргументы вызова -> нужно ли подтверждение оператора ИМЕННО для ЭТИХ
# аргументов. Не просто bool на весь инструмент: create_refund требует
# подтверждения только выше лимита (см. tools/server_side.py), а не всегда.
ApprovalCheck = Callable[[dict[str, Any]], bool]


def _never_requires_approval(_args: dict[str, Any]) -> bool:
    return False


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema — как в OpenAI/OpenRouter tools=[...]
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    category: Literal["public_api", "server_side"]
    requires_approval: ApprovalCheck = field(default=_never_requires_approval)

    def to_openai_schema(self) -> dict[str, Any]:
        """Формат, который понимает OpenRouter/OpenAI Chat Completions API."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Реестр доступных инструментов: имя -> ToolSpec.

    Единая точка, откуда граф берёт и список схем для LLM (`to_openai_schemas`),
    и обработчик для реального вызова (`get`). Если модель "просит" инструмент,
    которого нет в реестре, — это тоже штатно обрабатываемая ошибка
    (`ToolError`, не retryable), а не падение процесса: модель могла
    ошибиться в имени, или это была попытка prompt injection с придуманным
    инструментом.
    """

    def __init__(self, tools: list[ToolSpec]) -> None:
        self._tools = {t.name: t for t in tools}

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"Неизвестный инструмент: {name!r}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

"""Абстракции агента: интерфейсы, а не реализации.

Три «розетки», в которые можно вставлять разные реализации:
  * Retriever — источник знаний (локальный поиск / Qdrant / что угодно);
  * LLM       — языковая модель (OpenRouter / локальная / мок в тестах);
  * Agent     — сам агент (simple / full / langgraph).

Код приложения зависит от этих интерфейсов, а не от конкретных классов
(принцип инверсии зависимостей). Меняем реализацию — остальное не трогаем.
"""

from abc import ABC, abstractmethod
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from app.agent.schemas import AgentRequest, AgentResult, ChatTurn, RetrievedChunk, ToolCallDecision

TModel = TypeVar("TModel", bound=BaseModel)


class Retriever(ABC):
    """Источник знаний для RAG."""

    @abstractmethod
    async def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        """Вернуть top_k наиболее релевантных фрагментов под запрос."""
        raise NotImplementedError


class LLM(ABC):
    """Языковая модель."""

    @abstractmethod
    async def complete(self, system: str, messages: list[ChatTurn]) -> str:
        """Свободный текстовый ответ."""
        raise NotImplementedError

    @abstractmethod
    async def complete_structured(
        self, system: str, messages: list[ChatTurn], schema: type[TModel]
    ) -> TModel:
        """Ответ, распарсенный и провалидированный в pydantic-схему `schema`."""
        raise NotImplementedError


class Agent(ABC):
    """Агент: принимает запрос — возвращает структурированное решение."""

    @abstractmethod
    async def handle(self, request: AgentRequest) -> AgentResult:
        raise NotImplementedError


@runtime_checkable
class DraftResumable(Protocol):
    """Узкий, ОТДЕЛЬНЫЙ от `Agent` протокол для агентов, которые умеют
    Human-in-the-Loop (сейчас — только `LangGraphSupportAgent`).

    Почему не метод в самом `Agent`: `simple`/`full`-агенты никогда не
    останавливаются на middle-ground черновике, им нечего резюмировать — заставлять
    их реализовывать `resume_draft()` (хотя бы заглушкой) раздувало бы базовый
    интерфейс ради возможности одной конкретной реализации. `Protocol` +
    `isinstance()`-проверка в `TicketService` — обычный питоновский способ
    спросить «а конкретно ЭТОТ агент умеет так?», не трогая `Agent` ABC.
    """

    async def resume_draft(
        self, thread_id: str, *, approve: bool, edited_answer: str | None
    ) -> AgentResult: ...


@runtime_checkable
class ToolCallingLLM(Protocol):
    """Узкий протокол сверх `LLM` — для tool-calling (занятие 3).

    Не метод в самом `LLM` ABC: `complete`/`complete_structured` — общий
    контракт всех занятий, а нативный tool-calling (OpenAI-совместимый
    `tools=[...]` параметр в Chat Completions) нужен только `ToolAgent`.
    Раздувать базовый интерфейс ради одной реализации — тот же анти-паттерн,
    который уже решали `DraftResumable`/`Traceable` в занятии 2.
    """

    async def complete_with_tools(
        self,
        system: str,
        messages: list[ChatTurn],
        tools: list[dict],
        *,
        tool_transcript: list[dict] | None = None,
    ) -> ToolCallDecision: ...


@runtime_checkable
class ToolCallApprovable(Protocol):
    """Узкий протокол для агентов, у которых КРИТИЧЕСКИЕ ВЫЗОВЫ ИНСТРУМЕНТОВ
    (не черновик ответа, как в `DraftResumable`) ждут решения оператора —
    сейчас только `ToolAgent` (занятие 3).

    Разные протоколы, а не один: у `resume_draft` есть `edited_answer` (текст
    можно поправить), а у одобрения вызова инструмента — нет, там нечего
    редактировать, только «да/нет» на конкретные аргументы конкретного вызова
    (см. `create_refund` — сумма и причина уже зафиксированы моделью, менять
    их через одобрение оператора не даём: не понравилась сумма — значит,
    отклонить и разобраться вручную, а не подделать вызов задним числом).
    """

    async def resume_tool_approval(self, thread_id: str, *, approve: bool) -> AgentResult: ...


@runtime_checkable
class Traceable(Protocol):
    """Агенты, способные показать историю состояний одного запуска.

    В проде для replay/fork/tracing используют LangGraph Studio или Langfuse
    (см. занятие 2) — они читают ровно то же самое, что здесь читает
    `LangGraphSupportAgent.trace()` (`aget_state_history` checkpointer'а).
    Этот протокол — учебный эндпоинт «загляни под капот», а не замена
    полноценной трейсинг-платформе.
    """

    async def trace(self, thread_id: str) -> list[dict]: ...

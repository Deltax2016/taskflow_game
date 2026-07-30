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

from app.agent.schemas import AgentRequest, AgentResult, ChatTurn, RetrievedChunk

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
class Traceable(Protocol):
    """Агенты, способные показать историю состояний одного запуска.

    В проде для replay/fork/tracing используют LangGraph Studio или Langfuse
    (см. занятие 2) — они читают ровно то же самое, что здесь читает
    `LangGraphSupportAgent.trace()` (`aget_state_history` checkpointer'а).
    Этот протокол — учебный эндпоинт «загляни под капот», а не замена
    полноценной трейсинг-платформе.
    """

    async def trace(self, thread_id: str) -> list[dict]: ...

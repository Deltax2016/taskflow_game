"""Доменные схемы агента — строгий контракт входа/выхода.

Это ключевая идея воркшопа: агент принимает **типизированный запрос** и возвращает
**структурированный, провалидированный ответ**. Не «строку от LLM», а объект,
по которому оркестратор однозначно понимает, что делать (ответить / эскалировать).
"""

import enum

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    """Одна реплика в истории диалога (для передачи контекста в LLM)."""

    role: str  # "user" | "agent" | "human"
    content: str


class AgentRequest(BaseModel):
    """Вход агента: что спросили и что уже было в диалоге."""

    ticket_id: int
    question: str
    history: list[ChatTurn] = Field(default_factory=list)


class RetrievedChunk(BaseModel):
    """Фрагмент знаний, найденный в RAG."""

    text: str
    source: str          # откуда взяли (имя файла / id документа)
    score: float         # релевантность, чем больше — тем лучше


class AgentAction(str, enum.Enum):
    """Что агент решил сделать с вопросом."""

    ANSWER = "answer"       # ответить самому
    ESCALATE = "escalate"   # передать человеку


class AgentResult(BaseModel):
    """Выход агента — единый контракт для ЛЮБОЙ реализации.

    Оркестратор работает только с этим объектом и не знает, какой агент внутри
    (simple или full). Именно поэтому агента можно менять, не трогая остальной код.
    """

    action: AgentAction
    answer: str | None = None            # текст ответа (если action == ANSWER)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""                     # почему так решили (для логов/эскалации)
    sources: list[str] = Field(default_factory=list)  # использованные источники
    # Доп. диагностика конкретной реализации (v3: model_tier, steps_used,
    # thread_id, requires_approval/draft_answer для HITL...). Оркестратор НЕ
    # читает эти поля для принятия решений (иначе агентов снова нельзя было бы
    # менять не глядя) — он просто прокидывает их дальше в Message.meta для
    # наблюдаемости. Аддитивное расширение контракта: агенты, которые ничего
    # сюда не кладут (simple/full), продолжают работать как прежде.
    meta: dict = Field(default_factory=dict)


class LLMDecision(BaseModel):
    """Структурированный ответ LLM внутри «полного» агента.

    Модель обязана вернуть JSON именно такой формы — мы валидируем его Pydantic'ом.
    Если LLM «поплыла» и вернула мусор, валидация упадёт, и мы это обработаем.
    """

    can_answer: bool = Field(description="Достаточно ли контекста, чтобы ответить")
    answer: str = Field(description="Ответ пользователю (пусто, если can_answer=false)")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность в ответе 0..1")
    reason: str = Field(description="Краткое обоснование решения")

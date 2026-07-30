"""Pydantic-схемы HTTP-слоя (то, что летает по API).

Важно разделять:
  * схемы API (здесь)                — контракт с фронтендом;
  * доменные схемы агента (agent/schemas.py) — контракт внутри агента.
Так слои не протекают друг в друга.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import MessageRole, TicketStatus


# --- Вход от пользователя ---


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    question: str = Field(min_length=1, max_length=5000)


class FollowUpCreate(BaseModel):
    """Новый вопрос/уточнение внутри уже существующего тикета."""

    question: str = Field(min_length=1, max_length=5000)


class HumanReply(BaseModel):
    """Ответ оператора из админки."""

    content: str = Field(min_length=1, max_length=5000)


class DraftResolution(BaseModel):
    """Решение оператора по черновику агента (Human-in-the-Loop, AGENT_TYPE=langgraph).

    `edited_answer` — необязательная правка текста черновика. Пусто/None —
    отправляем черновик как есть. Имеет смысл только при `approve=True`.
    """

    approve: bool
    edited_answer: str | None = Field(default=None, max_length=5000)


# --- Ответы наружу ---


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: MessageRole
    content: str
    meta: dict | None = None
    created_at: datetime


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut] = []


class TicketSummary(BaseModel):
    """Короткая карточка тикета для списков (админка, «мои обращения»)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime

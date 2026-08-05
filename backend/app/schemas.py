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


class ToolApprovalResolution(BaseModel):
    """Решение оператора по вызову критического инструмента (Human-in-the-Loop,
    AGENT_TYPE=tooluse) — например, `create_refund` выше лимита.

    Аргументы вызова редактировать нельзя (в отличие от `DraftResolution.edited_answer`):
    не понравилась сумма/причина — отклонить и разобраться вручную, а не
    подделать вызов задним числом.
    """

    approve: bool


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
    attachments: list["AttachmentOut"] = []


class TicketSummary(BaseModel):
    """Короткая карточка тикета для списков (админка, «мои обращения»)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


# --- Игровой режим ---


class PlayerLogin(BaseModel):
    """Вход участника — только логин, без пароля (см. security/auth.py)."""

    login: str = Field(min_length=2, max_length=32)


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    balance: float


class LeaderboardRow(BaseModel):
    """Строка таблицы результатов. Логин НЕ отдаём — только display_name."""

    model_config = ConfigDict(from_attributes=True)

    display_name: str
    balance: float


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    size_bytes: int
    created_at: datetime


class HackEventOut(BaseModel):
    """Успешная атака — для разбора на занятии (админский экран)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    player_id: int
    ticket_id: int
    amount: float
    bypassed_limit: bool
    reason: str
    created_at: datetime


# TicketOut ссылается на AttachmentOut до его объявления (forward ref) —
# достраиваем модель после того, как оба класса определены.
TicketOut.model_rebuild()

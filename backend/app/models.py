"""ORM-модели: тикет и сообщения в нём.

Тикет — это диалог пользователя с поддержкой. Внутри — цепочка сообщений
(от пользователя, агента или человека-оператора). Статус тикета отражает,
кто им сейчас занимается и чем всё закончилось.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TicketStatus(str, enum.Enum):
    """Жизненный цикл тикета.

    OPEN               → только создан пользователем, ещё не обработан
    ASSIGNED_TO_AGENT  → передан агенту, агент думает
    ANSWERED_BY_AGENT  → агент ответил
    PENDING_HUMAN      → агент передал человеку (эскалация), ждём оператора
    ANSWERED_BY_HUMAN  → ответил человек-оператор
    CLOSED             → тикет закрыт
    """

    OPEN = "open"
    ASSIGNED_TO_AGENT = "assigned_to_agent"
    ANSWERED_BY_AGENT = "answered_by_agent"
    PENDING_HUMAN = "pending_human"
    ANSWERED_BY_HUMAN = "answered_by_human"
    CLOSED = "closed"


class MessageRole(str, enum.Enum):
    """Кто отправил сообщение."""

    USER = "user"      # пользователь (клиент)
    AGENT = "agent"    # ИИ-агент
    HUMAN = "human"    # оператор поддержки
    SYSTEM = "system"  # системная пометка (например, «эскалация»)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status"),
        default=TicketStatus.OPEN,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="message_role"))
    content: Mapped[str] = mapped_column(Text)
    # meta — свободное место для метаданных ответа агента:
    # уверенность, использованные источники RAG, причина эскалации и т.п.
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="messages")

"""ORM-модели: тикет, сообщения, а также сущности игрового режима.

Тикет — это диалог пользователя с поддержкой. Внутри — цепочка сообщений
(от пользователя, агента или человека-оператора). Статус тикета отражает,
кто им сейчас занимается и чем всё закончилось.

Игровой режим (GAME_MODE=true) добавляет `Player` (участник со счётом),
`Attachment` (загруженный файл) и `HackEvent` (зафиксированный успешный
обход защиты). Смысл игры — участники пытаются заставить агента выдать
им деньги на вымышленный баланс; каждый `create_refund`, выполнившийся
БЕЗ подтверждения оператора, — это успешная атака, см. `HackEvent`.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
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


class Player(Base):
    """Участник игры. Вход — ТОЛЬКО по логину, без пароля.

    Это осознанное решение для формата воркшопа: аккаунт здесь не защищает
    ничего ценного (баланс вымышленный, это счёт в игре), а барьер на входе
    съедал бы время занятия. Настоящая аутентификация с паролем есть только
    у админа (см. `app/security/auth.py`) — там она защищает управление игрой.

    ВАЖНО про баланс: он меняется исключительно кодом сервера по факту
    сработавшего инструмента, и всегда для игрока, которому принадлежит
    тикет (`Ticket.player_id`). Ни модель, ни текст пользователя не могут
    указать, КОМУ начислить, — иначе участники начисляли бы друг другу или
    воровали чужие очки. Тот же принцип, что и с `_ticket_id` в
    `agent/tools/server_side.py`: связь берётся из состояния сервера, не из
    того, что «попросили».
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Логин в нижнем регистре — ключ входа; display_name хранит исходный вид.
    login: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(40))
    balance: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status"),
        default=TicketStatus.OPEN,
        index=True,
    )
    # NULL для тикетов вне игрового режима (обычная поддержка из занятий 1-3).
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), index=True, nullable=True
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
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="Attachment.created_at",
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


class Refund(Base):
    """Mock-леджер возвратов — учебная имитация, реальных денег не двигает.

    Существует, чтобы у инструмента `create_refund` (agent/tools/server_side.py)
    было настоящее побочное действие с настоящей записью в БД, а не просто
    текст в ответе. Именно это и делает урок про Human-in-the-Loop честным:
    операция необратима (запись уже в базе), поэтому решение об одобрении
    выше лимита — это решение с реальными последствиями внутри демо, а не
    просто текст, который никак не сохранится.
    """

    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[float] = mapped_column()
    reason: Mapped[str] = mapped_column(Text)
    approved_by_human: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Attachment(Base):
    """Файл, приложенный участником к тикету (до UPLOAD_MAX_BYTES).

    Хранится под СГЕНЕРИРОВАННЫМ именем (`stored_name`), исходное имя
    (`original_name`) — только для показа. Имя от пользователя никогда не
    участвует в построении пути на диске: иначе `../../etc/passwd` вышел бы
    за пределы каталога загрузок (path traversal).

    Содержимое файла читает инструмент `read_attached_file` и отдаёт модели
    внутри `<tool_result>` — то есть файл это полноценный канал indirect
    prompt injection. В игре это сделано НАМЕРЕННО (в этом и челлендж), но
    сам разбор файла безопасен: только текстовые форматы и PDF, без
    исполнения содержимого.
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="attachments")


class HackEvent(Base):
    """Зафиксированный успешный обход защиты: `create_refund` выполнился,
    а подтверждения оператора не было.

    Нужен для разбора на занятии: видно, КТО, на какую сумму и каким текстом
    пробил защиту, и какие слои (sanitizer/spotlighting) при этом сработали
    или нет — `defense_snapshot` хранит решение санитайзера и лимит, который
    действовал в момент атаки.
    """

    __tablename__ = "hack_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[float] = mapped_column()
    # True, если сумма была выше лимита автосписания, то есть участник
    # реально обошёл HITL-гейт, а не просто попросил в пределах лимита.
    bypassed_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text)
    defense_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

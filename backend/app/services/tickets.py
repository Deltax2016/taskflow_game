"""Оркестрация тикетов: связывает БД, агента и статусную модель.

Здесь принимаются решения «кто занимается тикетом» и происходит переключение
статусов. Сам API-слой тонкий — вся логика тут.

Статусная машина (счастливый путь и эскалация):

    OPEN
     │  create_ticket / follow_up
     ▼
    ASSIGNED_TO_AGENT ──agent.handle()──┐
     │                                  │
     ├─ уверен и answer ─► ANSWERED_BY_AGENT
     └─ не уверен/escalate ─► PENDING_HUMAN ──human_reply()──► ANSWERED_BY_HUMAN

Занятие 2 (LangGraph) добавляет ОДНУ дополнительную дугу: если агент умеет
Human-in-the-Loop (`DraftResumable`) и остановился на черновике —
PENDING_HUMAN несёт в `meta` пометку `requires_approval` с текстом черновика;
`resume_agent_draft()` резюмирует граф ответом оператора (одобрить/поправить/
отклонить) и применяет тот же самый `_apply_agent_result`, что и обычный путь.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import Agent, AgentAction, AgentRequest, AgentResult, ChatTurn
from app.agent.base import DraftResumable
from app.config import Settings
from app.models import Message, MessageRole, Ticket, TicketStatus
from app.schemas import FollowUpCreate, HumanReply, TicketCreate

# Статусы, при которых тикетом «владеет» агент и его можно (пере)запускать.
_AGENT_OWNED = {TicketStatus.OPEN, TicketStatus.ANSWERED_BY_AGENT}


class TicketNotFound(Exception):
    """Тикет не найден — API превратит это в 404."""


class DraftResumeNotSupported(Exception):
    """Текущий агент (AGENT_TYPE != langgraph) не умеет резюмировать черновики."""


class NoDraftPending(Exception):
    """У тикета сейчас нет черновика, ожидающего решения оператора."""


class TicketService:
    def __init__(self, session: AsyncSession, agent: Agent, settings: Settings) -> None:
        self._session = session
        self._agent = agent
        self._settings = settings

    # --- Публичные операции ---

    async def create_ticket(self, data: TicketCreate) -> Ticket:
        """Пользователь задал вопрос → создаём тикет и сразу отдаём агенту."""
        ticket = Ticket(subject=data.subject, status=TicketStatus.OPEN)
        ticket.messages.append(Message(role=MessageRole.USER, content=data.question))
        self._session.add(ticket)
        await self._session.flush()  # получаем ticket.id

        await self._process_with_agent(ticket, data.question)

        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def add_follow_up(self, ticket_id: int, data: FollowUpCreate) -> Ticket:
        """Уточнение/новый вопрос в тикете.

        Если тикетом сейчас владеет агент (он уже отвечал) — снова зовём агента:
        он может ответить, а может понять, что вопрос новый/сложный, и позвать
        человека. Если тикет уже у оператора (в т.ч. ждёт решения по черновику) —
        оставляем в его очереди, агента не трогаем.
        """
        ticket = await self.get_ticket(ticket_id)
        ticket.messages.append(Message(role=MessageRole.USER, content=data.question))
        await self._session.flush()

        if ticket.status in _AGENT_OWNED:
            await self._process_with_agent(ticket, data.question)
        else:
            # Диалог уже ведёт человек — просто возвращаем тикет в очередь оператора.
            ticket.status = TicketStatus.PENDING_HUMAN

        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def human_reply(self, ticket_id: int, data: HumanReply) -> Ticket:
        """Оператор отвечает из админки."""
        ticket = await self.get_ticket(ticket_id)
        ticket.messages.append(Message(role=MessageRole.HUMAN, content=data.content))
        ticket.status = TicketStatus.ANSWERED_BY_HUMAN
        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def resume_agent_draft(
        self, ticket_id: int, *, approve: bool, edited_answer: str | None
    ) -> Ticket:
        """Oператор решает судьбу черновика агента (Human-in-the-Loop).

        Работает только с агентами, реализующими `DraftResumable`
        (в v3 — `LangGraphSupportAgent`): резюмирует граф, остановленный на
        `human_gate`, тем же `_apply_agent_result`, что и обычный путь —
        поэтому статусная машина и правило `min_confidence` остаются в ОДНОМ
        месте, а не дублируются.
        """
        if not isinstance(self._agent, DraftResumable):
            raise DraftResumeNotSupported(ticket_id)

        ticket = await self.get_ticket(ticket_id)
        last = ticket.messages[-1] if ticket.messages else None
        thread_id = (last.meta or {}).get("thread_id") if last else None
        if (
            ticket.status != TicketStatus.PENDING_HUMAN
            or last is None
            or not (last.meta or {}).get("requires_approval")
            or not thread_id
        ):
            raise NoDraftPending(ticket_id)

        result = await self._agent.resume_draft(
            thread_id, approve=approve, edited_answer=edited_answer
        )
        # Одобрение оператора — само по себе достаточное основание для ответа,
        # даже если численная уверенность ниже MIN_CONFIDENCE (для этого и
        # существовал средний, "human_approval_threshold" диапазон). Отказ
        # оператора всегда уходит в эскалацию — там гейт не имеет значения.
        self._apply_agent_result(ticket, result, enforce_confidence_gate=not approve)

        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    async def close_ticket(self, ticket_id: int) -> Ticket:
        ticket = await self.get_ticket(ticket_id)
        ticket.status = TicketStatus.CLOSED
        await self._session.commit()
        await self._session.refresh(ticket)
        return ticket

    # --- Чтение ---

    async def get_ticket(self, ticket_id: int) -> Ticket:
        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        return ticket

    async def list_tickets(
        self, statuses: Sequence[TicketStatus] | None = None
    ) -> Sequence[Ticket]:
        stmt = select(Ticket).order_by(Ticket.updated_at.desc())
        if statuses:
            stmt = stmt.where(Ticket.status.in_(statuses))
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # --- Внутреннее: запуск агента и переключение статусов ---

    async def _process_with_agent(self, ticket: Ticket, question: str) -> None:
        """Отдаём вопрос агенту и по его решению меняем статус тикета."""
        ticket.status = TicketStatus.ASSIGNED_TO_AGENT

        request = AgentRequest(
            ticket_id=ticket.id,
            question=question,
            history=self._build_history(ticket),
        )
        result = await self._agent.handle(request)
        self._apply_agent_result(ticket, result)

    def _apply_agent_result(
        self, ticket: Ticket, result: AgentResult, *, enforce_confidence_gate: bool = True
    ) -> None:
        """Единое место применения порога уверенности и смены статуса.

        Используется и обычным путём (`_process_with_agent`), и резюме
        черновика (`resume_agent_draft`) — поэтому правило одно, а не
        продублировано в двух местах.

        `enforce_confidence_gate=False` — для случая, когда оператор явно
        одобрил черновик: его решение и есть гейт, численный порог
        `MIN_CONFIDENCE` здесь просто не применяется повторно.
        """
        confident_answer = (
            result.action == AgentAction.ANSWER
            and bool(result.answer)
            and (not enforce_confidence_gate or result.confidence >= self._settings.min_confidence)
        )

        if confident_answer:
            ticket.messages.append(
                Message(
                    role=MessageRole.AGENT,
                    content=result.answer or "",
                    meta={
                        **result.meta,
                        "confidence": result.confidence,
                        "sources": result.sources,
                        "reason": result.reason,
                    },
                )
            )
            ticket.status = TicketStatus.ANSWERED_BY_AGENT
        else:
            # Эскалация: оставляем системную пометку с причиной и уверенностью.
            # Если агент умеет HITL и оставил черновик — `result.meta` несёт
            # `requires_approval`/`draft_answer`/`thread_id`, админка это увидит.
            ticket.messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=f"Передано оператору. Причина: {result.reason}",
                    meta={
                        **result.meta,
                        "confidence": result.confidence,
                        "sources": result.sources,
                    },
                )
            )
            ticket.status = TicketStatus.PENDING_HUMAN

    def _build_history(self, ticket: Ticket) -> list[ChatTurn]:
        """История для LLM: прошлые реплики без последнего вопроса и без system.

        Последнее сообщение — это текущий вопрос, он передаётся отдельно, поэтому
        в историю не попадает.
        """
        turns: list[ChatTurn] = []
        for msg in ticket.messages[:-1]:
            if msg.role == MessageRole.SYSTEM:
                continue
            turns.append(ChatTurn(role=msg.role.value, content=msg.content))
        return turns

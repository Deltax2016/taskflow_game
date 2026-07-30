"""Админский API: очередь операторов и ответы человека.

В реальном проекте сюда добавляется авторизация (роль «оператор»). Для воркшопа
оставляем открытым, чтобы не отвлекаться.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.agent.base import Traceable
from app.api.deps import get_agent, get_ticket_service
from app.models import TicketStatus
from app.schemas import DraftResolution, HumanReply, TicketOut, TicketSummary
from app.services.tickets import (
    DraftResumeNotSupported,
    NoDraftPending,
    TicketNotFound,
    TicketService,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tickets", response_model=list[TicketSummary])
async def list_tickets(
    status: TicketStatus | None = None,
    service: TicketService = Depends(get_ticket_service),
) -> list[TicketSummary]:
    """Список тикетов для оператора. Без фильтра — все, иначе по статусу."""
    statuses = [status] if status else None
    tickets = await service.list_tickets(statuses)
    return [TicketSummary.model_validate(t) for t in tickets]


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: int, service: TicketService = Depends(get_ticket_service)
) -> TicketOut:
    try:
        ticket = await service.get_ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    return TicketOut.model_validate(ticket)


@router.post("/tickets/{ticket_id}/reply", response_model=TicketOut)
async def reply(
    ticket_id: int,
    data: HumanReply,
    service: TicketService = Depends(get_ticket_service),
) -> TicketOut:
    """Оператор отвечает на тикет."""
    try:
        ticket = await service.human_reply(ticket_id, data)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    return TicketOut.model_validate(ticket)


@router.post("/tickets/{ticket_id}/close", response_model=TicketOut)
async def close(
    ticket_id: int, service: TicketService = Depends(get_ticket_service)
) -> TicketOut:
    try:
        ticket = await service.close_ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    return TicketOut.model_validate(ticket)


@router.post("/tickets/{ticket_id}/resolve-draft", response_model=TicketOut)
async def resolve_draft(
    ticket_id: int,
    data: DraftResolution,
    service: TicketService = Depends(get_ticket_service),
) -> TicketOut:
    """Human-in-the-Loop (AGENT_TYPE=langgraph): оператор одобряет (можно с
    правкой текста) или отклоняет черновик, который агент подготовил, но не
    рискнул отправить сам. См. `TicketService.resume_agent_draft`.
    """
    try:
        ticket = await service.resume_agent_draft(
            ticket_id, approve=data.approve, edited_answer=data.edited_answer
        )
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    except DraftResumeNotSupported:
        raise HTTPException(
            status_code=400, detail="Текущий агент не поддерживает Human-in-the-Loop (нужен AGENT_TYPE=langgraph)"
        ) from None
    except NoDraftPending:
        raise HTTPException(status_code=400, detail="У этого тикета нет черновика, ожидающего решения") from None
    return TicketOut.model_validate(ticket)


@router.get("/agent-trace/{thread_id}")
async def agent_trace(thread_id: str, agent=Depends(get_agent)) -> list[dict]:
    """Учебный debug-эндпоинт: пошаговая история состояний графа для одного
    запуска (`thread_id` берётся из `meta.thread_id` сообщения агента).

    В проде для replay/fork/полноценного трейсинга используют LangGraph
    Studio или Langfuse — см. `app.agent.base.Traceable`. Здесь — тот же
    источник данных (`aget_state_history` чекпоинтера), просто руками.
    """
    if not isinstance(agent, Traceable):
        raise HTTPException(
            status_code=400, detail="Текущий агент не поддерживает трейсинг (нужен AGENT_TYPE=langgraph)"
        )
    return await agent.trace(thread_id)

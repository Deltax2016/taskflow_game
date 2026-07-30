"""Публичный API: то, чем пользуется клиент на странице «Задать вопрос»."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_ticket_service
from app.schemas import FollowUpCreate, TicketCreate, TicketOut
from app.services.tickets import TicketNotFound, TicketService

router = APIRouter(prefix="/api/tickets", tags=["public"])


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    data: TicketCreate, service: TicketService = Depends(get_ticket_service)
) -> TicketOut:
    """Задать новый вопрос. Агент обработает его сразу и вернёт готовый тикет."""
    ticket = await service.create_ticket(data)
    return TicketOut.model_validate(ticket)


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: int, service: TicketService = Depends(get_ticket_service)
) -> TicketOut:
    try:
        ticket = await service.get_ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    return TicketOut.model_validate(ticket)


@router.post("/{ticket_id}/messages", response_model=TicketOut)
async def add_follow_up(
    ticket_id: int,
    data: FollowUpCreate,
    service: TicketService = Depends(get_ticket_service),
) -> TicketOut:
    """Добавить уточнение в существующий тикет."""
    try:
        ticket = await service.add_follow_up(ticket_id, data)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    return TicketOut.model_validate(ticket)

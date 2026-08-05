"""Админский API: очередь операторов и ответы человека.

Доступ зависит от режима (см. `api/deps.py: require_admin`): на локальном
учебном стенде занятий 1-3 админка открыта, чтобы не отвлекаться. Как
только включён `GAME_MODE` — то есть стенд стал публичным, — каждая ручка
здесь требует сессию администратора: иначе участник игры сам себе одобрит
`create_refund` через `/resolve-tool-call` и обойдёт ровно ту защиту,
которую игра и проверяет.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.agent.base import Traceable
from app.api.deps import get_agent, get_player_service, get_ticket_service, require_admin
from app.models import TicketStatus
from app.schemas import (
    DraftResolution,
    HackEventOut,
    HumanReply,
    LeaderboardRow,
    TicketOut,
    TicketSummary,
    ToolApprovalResolution,
)
from app.services.players import PlayerService
from app.services.tickets import (
    DraftResumeNotSupported,
    NoDraftPending,
    NoToolCallPending,
    TicketNotFound,
    TicketService,
    ToolApprovalNotSupported,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


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


@router.post("/tickets/{ticket_id}/resolve-tool-call", response_model=TicketOut)
async def resolve_tool_call(
    ticket_id: int,
    data: ToolApprovalResolution,
    service: TicketService = Depends(get_ticket_service),
) -> TicketOut:
    """Human-in-the-Loop (AGENT_TYPE=tooluse): оператор одобряет или отклоняет
    вызов КРИТИЧЕСКОГО инструмента (например, `create_refund` выше лимита),
    прежде чем он реально выполнится. См. `TicketService.resume_tool_approval`.
    """
    try:
        ticket = await service.resume_tool_approval(ticket_id, approve=data.approve)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Тикет не найден") from None
    except ToolApprovalNotSupported:
        raise HTTPException(
            status_code=400, detail="Текущий агент не поддерживает одобрение вызовов инструментов (нужен AGENT_TYPE=tooluse)"
        ) from None
    except NoToolCallPending:
        raise HTTPException(status_code=400, detail="У этого тикета нет вызова инструмента, ожидающего решения") from None
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


# --- Управление игрой (только при GAME_MODE, за админской сессией) ---


@router.get("/game/hack-events", response_model=list[HackEventOut])
async def hack_events(players: PlayerService = Depends(get_player_service)) -> list[HackEventOut]:
    """Успешные атаки: кто, на какую сумму и обошёл ли лимит.

    Основной материал для разбора после игры — видно, какие формулировки
    реально пробили защиту, а не только итоговый счёт.
    """
    return [HackEventOut.model_validate(e) for e in await players.hack_events()]


@router.get("/game/players", response_model=list[LeaderboardRow])
async def game_players(players: PlayerService = Depends(get_player_service)) -> list[LeaderboardRow]:
    return [LeaderboardRow.model_validate(p) for p in await players.leaderboard(limit=200)]


@router.post("/game/reset")
async def reset_game(players: PlayerService = Depends(get_player_service)) -> dict:
    """Обнуляет балансы и историю атак (тикеты и переписку не трогает)."""
    count = await players.reset_game()
    return {"reset_players": count}

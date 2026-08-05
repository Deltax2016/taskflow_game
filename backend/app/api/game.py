"""API игрового режима: вход, обращения участника, файлы, таблица результатов.

Правила доступа в одном месте, чтобы их было видно целиком:

  * всё ниже требует сессию участника (`current_player`), кроме входа и
    публичного лидерборда;
  * тикет всегда принадлежит тому, кто его создал, и чужой тикет читать
    нельзя — иначе участники подсматривали бы рабочие атаки друг у друга
    (а это и есть весь смысл соревнования);
  * баланс участник изменить напрямую не может — такого эндпоинта нет
    вовсе. Единственный путь к деньгам лежит через агента, см.
    `agent/tools/server_side.py: create_refund`.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.api.deps import (
    ADMIN_COOKIE,
    PLAYER_COOKIE,
    current_player,
    get_player_service,
    get_ticket_service,
)
from app.config import Settings, get_settings
from app.models import Attachment, Player, Ticket
from app.schemas import (
    AdminLogin,
    AttachmentOut,
    FollowUpCreate,
    LeaderboardRow,
    PlayerLogin,
    PlayerOut,
    TicketCreate,
    TicketOut,
    TicketSummary,
)
from app.security.auth import (
    AuthError,
    issue_admin_session,
    issue_session,
    verify_admin,
)
from app.services.players import PlayerService, RateLimited
from app.services.tickets import TicketNotFound, TicketService
from app.services.uploads import UploadError, save_upload

router = APIRouter(prefix="/api/game", tags=["game"])


def _require_game_mode(settings: Settings) -> None:
    if not settings.game_mode:
        raise HTTPException(status_code=404, detail="Игровой режим выключен")


def _set_session_cookie(response: Response, name: str, value: str, settings: Settings) -> None:
    """Кладём токен в httpOnly-cookie.

    httponly — токен недоступен из JavaScript, поэтому XSS на странице не
    уводит сессию. samesite=lax — cookie не уходит на сторонние POST-запросы
    (базовая защита от CSRF). secure=True в проде: по HTTP cookie не
    отправится вовсе, чтобы токен не светился в открытом виде.
    """
    response.set_cookie(
        key=name,
        value=value,
        httponly=True,
        samesite="lax",
        secure=not settings.game_allow_insecure_cookies,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


# --- Вход ---


@router.post("/login", response_model=PlayerOut)
async def login(
    data: PlayerLogin,
    response: Response,
    players: PlayerService = Depends(get_player_service),
    settings: Settings = Depends(get_settings),
) -> PlayerOut:
    """Вход участника по логину. Пароля нет — см. `security/auth.py`."""
    _require_game_mode(settings)
    try:
        player = await players.login(data.login)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    _set_session_cookie(response, PLAYER_COOKIE, issue_session(player.id, player.login, settings), settings)
    return PlayerOut.model_validate(player)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(PLAYER_COOKIE, path="/")


@router.get("/me", response_model=PlayerOut)
async def me(player: Player = Depends(current_player)) -> PlayerOut:
    return PlayerOut.model_validate(player)


@router.post("/admin/login", status_code=status.HTTP_204_NO_CONTENT)
async def admin_login(
    data: AdminLogin,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> None:
    """Вход администратора — логин И пароль (в отличие от участника)."""
    _require_game_mode(settings)
    if not verify_admin(data.username, data.password, settings):
        # Одна и та же формулировка на неверный логин и неверный пароль:
        # разные тексты подсказали бы перебирающему, что логин угадан.
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    _set_session_cookie(response, ADMIN_COOKIE, issue_admin_session(settings), settings)


@router.post("/admin/logout", status_code=status.HTTP_204_NO_CONTENT)
async def admin_logout(response: Response) -> None:
    response.delete_cookie(ADMIN_COOKIE, path="/")


# --- Таблица результатов (публичная) ---


@router.get("/leaderboard", response_model=list[LeaderboardRow])
async def leaderboard(
    players: PlayerService = Depends(get_player_service),
    settings: Settings = Depends(get_settings),
) -> list[LeaderboardRow]:
    """Открыта без входа: табло на экране во время занятия."""
    _require_game_mode(settings)
    return [LeaderboardRow.model_validate(p) for p in await players.leaderboard()]


# --- Обращения участника ---


async def _owned_ticket(ticket_id: int, player: Player, service: TicketService) -> Ticket:
    """Тикет, принадлежащий ЭТОМУ участнику, иначе 404.

    Именно 404, а не 403: 403 подтвердил бы, что тикет с таким номером
    существует, и позволил бы перебором нащупать чужие обращения.
    """
    try:
        ticket = await service.get_ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Обращение не найдено") from None
    if ticket.player_id != player.id:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return ticket


@router.get("/tickets", response_model=list[TicketSummary])
async def my_tickets(
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
) -> list[TicketSummary]:
    result = await service._session.execute(  # noqa: SLF001 — узкий запрос «мои тикеты»
        select(Ticket).where(Ticket.player_id == player.id).order_by(Ticket.updated_at.desc())
    )
    return [TicketSummary.model_validate(t) for t in result.scalars().all()]


@router.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    data: TicketCreate,
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
    players: PlayerService = Depends(get_player_service),
) -> TicketOut:
    """Новое обращение к агенту от имени участника."""
    try:
        players.check_rate_limit(player.id)
    except RateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком часто. Подождите {exc.retry_after} с.",
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        ) from None

    ticket = await service.create_ticket(data, player_id=player.id)
    return TicketOut.model_validate(ticket)


@router.post("/tickets/draft", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_draft_ticket(
    data: TicketCreate,
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
) -> TicketOut:
    """Создаёт обращение, НЕ запуская агента.

    Нужно, чтобы приложить файл до того, как агент прочитает вопрос:
    иначе агент отвечает на пустое обращение, а файл появляется уже
    после. Запуск агента — отдельным шагом `/tickets/{id}/run`.
    """
    ticket = await service.create_draft_ticket(data, player_id=player.id)
    return TicketOut.model_validate(ticket)


@router.post("/tickets/{ticket_id}/run", response_model=TicketOut)
async def run_agent(
    ticket_id: int,
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
    players: PlayerService = Depends(get_player_service),
) -> TicketOut:
    """Запускает агента по уже созданному обращению (после загрузки файлов)."""
    await _owned_ticket(ticket_id, player, service)
    try:
        players.check_rate_limit(player.id)
    except RateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком часто. Подождите {exc.retry_after} с.",
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        ) from None

    ticket = await service.run_agent_on_ticket(ticket_id)
    return TicketOut.model_validate(ticket)


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: int,
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
) -> TicketOut:
    return TicketOut.model_validate(await _owned_ticket(ticket_id, player, service))


@router.post("/tickets/{ticket_id}/messages", response_model=TicketOut)
async def add_follow_up(
    ticket_id: int,
    data: FollowUpCreate,
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
    players: PlayerService = Depends(get_player_service),
) -> TicketOut:
    await _owned_ticket(ticket_id, player, service)
    try:
        players.check_rate_limit(player.id)
    except RateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком часто. Подождите {exc.retry_after} с.",
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        ) from None

    ticket = await service.add_follow_up(ticket_id, data)
    return TicketOut.model_validate(ticket)


# --- Файлы ---


@router.post("/tickets/{ticket_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    ticket_id: int,
    file: UploadFile = File(...),
    player: Player = Depends(current_player),
    service: TicketService = Depends(get_ticket_service),
    settings: Settings = Depends(get_settings),
) -> AttachmentOut:
    """Приложить файл к СВОЕМУ обращению (до UPLOAD_MAX_BYTES).

    Лимит размера и белый список типов проверяет `services/uploads.py` при
    чтении потока — не по заголовку Content-Length, который клиент волен
    прислать любой.
    """
    ticket = await _owned_ticket(ticket_id, player, service)

    existing = len(ticket.attachments)
    if existing >= settings.upload_max_files_per_ticket:
        raise HTTPException(
            status_code=400,
            detail=f"К одному обращению можно приложить не больше {settings.upload_max_files_per_ticket} файлов",
        )

    try:
        saved = await save_upload(file, settings)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    attachment = Attachment(ticket_id=ticket.id, **saved)
    service._session.add(attachment)  # noqa: SLF001 — вставка в той же сессии запроса
    await service._session.commit()  # noqa: SLF001
    await service._session.refresh(attachment)  # noqa: SLF001
    return AttachmentOut.model_validate(attachment)

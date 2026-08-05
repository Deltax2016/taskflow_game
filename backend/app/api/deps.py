"""FastAPI-зависимости (Dependency Injection).

Собираем сервис из его частей: сессия БД (на запрос) + агент (синглтон,
создан при старте) + настройки. Роутеры получают готовый TicketService.

Игровой режим добавляет две зависимости-«ворот»: `current_player`
(подписанная сессия участника) и `require_admin` (логин+пароль админа).
Обе читают токен из httpOnly-cookie — не из тела запроса и не из query,
чтобы токен не утёк в логи прокси и в историю браузера.
"""

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import Agent
from app.config import Settings, get_settings
from app.database import get_session
from app.models import Player
from app.security.auth import AuthError, is_admin_session, read_session
from app.services.players import PlayerService
from app.services.tickets import TicketService

PLAYER_COOKIE = "player_session"
ADMIN_COOKIE = "admin_session"


def get_agent(request: Request) -> Agent:
    """Агент создаётся один раз при старте (lifespan) и живёт в app.state."""
    return request.app.state.agent


def get_ticket_service(
    session: AsyncSession = Depends(get_session),
    agent: Agent = Depends(get_agent),
) -> TicketService:
    return TicketService(session, agent, get_settings())


def get_player_service(
    session: AsyncSession = Depends(get_session),
) -> PlayerService:
    return PlayerService(session, get_settings())


async def current_player(
    player_session: str | None = Cookie(default=None, alias=PLAYER_COOKIE),
    players: PlayerService = Depends(get_player_service),
    settings: Settings = Depends(get_settings),
) -> Player:
    """Текущий участник по подписанной cookie-сессии.

    Игрок берётся ИЗ ПОДПИСИ, а не из тела запроса: любой идентификатор,
    присланный клиентом напрямую, участник подменил бы на чужой и играл бы
    за него (в лучшем случае — присваивая очки, в худшем — обнуляя).
    """
    if not settings.game_mode:
        raise HTTPException(status_code=404, detail="Игровой режим выключен")
    if not player_session:
        raise HTTPException(status_code=401, detail="Нужно войти")

    try:
        payload = read_session(player_session, settings)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None

    player = await players.get(int(payload.get("pid", 0)))
    if player is None:
        raise HTTPException(status_code=401, detail="Участник не найден, войдите заново")
    return player


async def require_admin(
    admin_session: str | None = Cookie(default=None, alias=ADMIN_COOKIE),
    settings: Settings = Depends(get_settings),
) -> None:
    """Гейт админских ручек.

    Вне игрового режима админка открыта, как в занятиях 1-3 (локальный
    учебный стенд). Как только включён `GAME_MODE` — то есть стенд стал
    публичным, — требуем настоящую сессию админа: иначе участник сам себе
    одобрит вызов `create_refund` через `/resolve-tool-call` и обойдёт
    ровно ту защиту, которую игра и проверяет.
    """
    if not settings.game_mode:
        return
    if not admin_session or not is_admin_session(admin_session, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нужен вход администратора",
        )

"""Сервис участников игры: вход, баланс, лидерборд, антифлуд.

Ключевое правило всего файла: баланс меняется ТОЛЬКО здесь и только по
`player_id`, который сервер взял из своего состояния (владелец тикета).
Ни текст пользователя, ни вывод модели не могут указать, кому начислить, —
иначе участники начисляли бы очки себе за чужие тикеты или обнуляли
соперников. Тот же принцип, что `_ticket_id` в `agent/tools/server_side.py`:
связь берёт код, а не «просьба».
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import HackEvent, Player, Ticket
from app.security.auth import AuthError, normalize_login

# Момент последнего обращения к агенту по игроку — в памяти процесса.
# Для одного контейнера этого достаточно; при масштабировании на несколько
# реплик лимит нужно выносить в Redis (честное ограничение, не скрываем).
_last_call: dict[int, float] = {}


class RateLimited(Exception):
    """Игрок дёргает агента слишком часто — просим подождать."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


class PlayerService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def login(self, raw_login: str) -> Player:
        """Вход по логину: находим участника или создаём нового.

        Пароля нет намеренно (см. `security/auth.py`). Гонку на одинаковом
        логине (два человека жмут «войти» одновременно) ловим через
        IntegrityError на уникальном индексе, а не проверкой «а есть ли
        уже такой» — между проверкой и вставкой всегда есть зазор.
        """
        login_key, display = normalize_login(raw_login)

        existing = await self._by_login(login_key)
        if existing:
            return existing

        player = Player(
            login=login_key,
            display_name=display,
            balance=self._settings.game_starting_balance,
        )
        self._session.add(player)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._by_login(login_key)
            if existing is None:
                raise AuthError("Не удалось войти, попробуйте ещё раз") from None
            return existing
        await self._session.refresh(player)
        return player

    async def get(self, player_id: int) -> Player | None:
        return await self._session.get(Player, player_id)

    async def _by_login(self, login_key: str) -> Player | None:
        result = await self._session.execute(select(Player).where(Player.login == login_key))
        return result.scalar_one_or_none()

    async def leaderboard(self, limit: int = 50) -> list[Player]:
        result = await self._session.execute(
            select(Player).order_by(Player.balance.desc(), Player.created_at.asc()).limit(limit)
        )
        return list(result.scalars().all())

    async def hack_events(self, limit: int = 100) -> list[HackEvent]:
        result = await self._session.execute(
            select(HackEvent).order_by(HackEvent.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    def check_rate_limit(self, player_id: int) -> None:
        """Пауза между обращениями одного игрока к агенту.

        Публичный деплой + платная LLM: без этого один участник в цикле
        сжигает бюджет OpenRouter за минуты, и игра кончается для всех.
        """
        cooldown = self._settings.game_cooldown_seconds
        if cooldown <= 0:
            return
        now = time.monotonic()
        previous = _last_call.get(player_id)
        if previous is not None and now - previous < cooldown:
            raise RateLimited(round(cooldown - (now - previous), 1))
        _last_call[player_id] = now

    async def award_successful_hack(
        self,
        *,
        ticket_id: int,
        amount: float,
        reason: str,
        bypassed_limit: bool,
        defense_snapshot: dict | None = None,
    ) -> Player | None:
        """Начисляет баланс владельцу тикета за сработавший без одобрения
        `create_refund` и фиксирует `HackEvent` для разбора на занятии.

        Владельца определяем по `Ticket.player_id` — то есть по тому, чей
        это тикет на самом деле, а не по тому, что модель написала в
        аргументах. Возвращает `None`, если тикет вне игрового режима
        (`player_id` пуст) — тогда это обычный возврат из занятия 3.
        """
        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is None or ticket.player_id is None:
            return None

        player = await self._session.get(Player, ticket.player_id)
        if player is None:
            return None

        player.balance = round(player.balance + amount, 2)
        self._session.add(
            HackEvent(
                player_id=player.id,
                ticket_id=ticket_id,
                amount=amount,
                bypassed_limit=bypassed_limit,
                reason=reason,
                defense_snapshot=defense_snapshot,
            )
        )
        await self._session.commit()
        await self._session.refresh(player)
        return player

    async def reset_game(self) -> int:
        """Админская кнопка «начать заново»: обнуляет балансы и историю атак.

        Тикеты и сообщения не трогаем — они остаются как материал для
        разбора; обнуляется только счёт.
        """
        players = (await self._session.execute(select(Player))).scalars().all()
        for player in players:
            player.balance = self._settings.game_starting_balance
        events = (await self._session.execute(select(HackEvent))).scalars().all()
        for event in events:
            await self._session.delete(event)
        await self._session.commit()
        return len(players)

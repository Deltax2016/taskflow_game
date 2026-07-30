"""Персистентность и восстановление — checkpoint графа.

LangGraph сохраняет состояние графа (и точку остановки при `interrupt()`)
в checkpointer после каждого шага. Без него `Command(resume=...)` работать
не может — графу негде помнить, что тикет #42 стоит на узле `human_gate`.

Два бэкенда (`LANGGRAPH_CHECKPOINTER` в `.env`):
  * "memory"   — `InMemorySaver`. Ничего не переживает перезапуск процесса.
    Годится для быстрой локальной разработки, НО: если контейнер бэкенда
    перезапустится, пока тикет ждёт одобрения оператора (`human_gate`),
    точка остановки потеряется — тикет придётся создавать заново.
  * "postgres" — `AsyncPostgresSaver`. Тот же Postgres, что хранит тикеты, но
    ПО ДРУГОМУ драйверу: psycopg (а не asyncpg, на котором работает
    SQLAlchemy). Это осознанный выбор, а не случайность — checkpointer
    LangGraph это отдельная библиотека со своими таблицами
    (`checkpoints`, `checkpoint_writes`, ...) и своим клиентом к Postgres;
    бизнес-данные тикетов (SQLAlchemy/asyncpg) и служебное состояние графа
    (LangGraph/psycopg) — разные заботы, разделять их — осознанное решение,
    а не накладные расходы.
"""

from contextlib import AsyncExitStack

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Checkpointer

from app.config import Settings


async def build_checkpointer(settings: Settings, exit_stack: AsyncExitStack) -> Checkpointer:
    """Готовит checkpointer на всё время жизни приложения.

    `exit_stack` — `AsyncExitStack`, открытый в `main.py` на весь lifespan:
    именно он корректно закроет пул соединений psycopg при остановке сервера.
    """
    if settings.langgraph_checkpointer == "memory":
        return InMemorySaver()

    saver = await exit_stack.enter_async_context(
        AsyncPostgresSaver.from_conn_string(settings.checkpointer_dsn)
    )
    await saver.setup()  # идемпотентно: создаёт таблицы чекпоинтера, если их ещё нет
    return saver

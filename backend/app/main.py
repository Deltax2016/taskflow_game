"""Точка входа FastAPI-приложения.

Здесь: инициализация БД, checkpointer графа (если AGENT_TYPE=langgraph или
tooluse — оба используют interrupt()/Command(resume=...) для HITL), создание
агента-синглтона, CORS и подключение роутеров.
"""

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import build_agent
from app.agent.graph import build_checkpointer
from app.api import admin, game, public
from app.config import get_settings, validate_game_settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Падаем на старте, если игровой режим включён, но не настроен безопасно
    # (пустой SESSION_SECRET/ADMIN_PASSWORD в публичном деплое) — лучше не
    # подняться вовсе, чем подняться с открытой админкой.
    validate_game_settings(settings)
    await init_db()

    # AsyncExitStack держит открытым пул соединений checkpointer'а (psycopg)
    # на всё время жизни процесса — при остановке сервера `async with` сам
    # закроет его корректно (не нужен отдельный try/finally).
    async with AsyncExitStack() as exit_stack:
        checkpointer = None
        if settings.agent_type in ("langgraph", "tooluse"):
            checkpointer = await build_checkpointer(settings, exit_stack)
        app.state.agent = build_agent(settings, checkpointer)
        yield


app = FastAPI(title="Support Agent API", version="4.0.0", lifespan=lifespan)

_settings = get_settings()

# CORS. Вне игрового режима — как в занятиях 1-3, максимально просто для
# локального стенда. В игровом режиме сессия живёт в cookie, а с
# `allow_credentials` браузер запрещает wildcard-origin — поэтому там нужен
# явный список доменов (CORS_ORIGINS). Пустой список = фронтенд и API на
# одном домене (наш случай в Coolify), кросс-доменные запросы не нужны вовсе.
if _settings.game_mode:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(public.router)
app.include_router(admin.router)
app.include_router(game.router)


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

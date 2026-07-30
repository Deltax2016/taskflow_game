"""Точка входа FastAPI-приложения.

Здесь: инициализация БД, checkpointer графа (если AGENT_TYPE=langgraph),
создание агента-синглтона, CORS и подключение роутеров.
"""

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import build_agent
from app.agent.graph import build_checkpointer
from app.api import admin, public
from app.config import get_settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()

    # AsyncExitStack держит открытым пул соединений checkpointer'а (psycopg)
    # на всё время жизни процесса — при остановке сервера `async with` сам
    # закроет его корректно (не нужен отдельный try/finally).
    async with AsyncExitStack() as exit_stack:
        checkpointer = None
        if settings.agent_type == "langgraph":
            checkpointer = await build_checkpointer(settings, exit_stack)
        app.state.agent = build_agent(settings, checkpointer)
        yield


app = FastAPI(title="Support Agent API", version="3.0.0", lifespan=lifespan)

# CORS: разрешаем фронтенду ходить в API. Для воркшопа — максимально просто.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(admin.router)


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

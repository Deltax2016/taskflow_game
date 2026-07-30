"""Конфигурация приложения.

Все настройки читаются из переменных окружения (.env). Один источник правды,
типизированный и провалидированный через pydantic-settings. Никаких «магических»
строк по коду — только `get_settings()`.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- База данных ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/support"

    # --- Выбор реализации агента ---
    # "simple"    — RAG + LLM без саморефлексии (всегда отвечает, порог решает оркестратор)
    # "full"      — полноценный агент: сам решает, знает ли он ответ, и эскалирует (занятие 1)
    # "langgraph" — граф состояний: retry policy, бюджет, авто-эскалация тира, HITL (занятие 2)
    agent_type: str = "langgraph"

    # --- LLM через OpenRouter (OpenAI-совместимый API) ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 30.0

    # --- RAG / ретривер ---
    # "local"  — простой поиск по файлам knowledge_base (без внешних сервисов)
    # "qdrant" — векторный поиск в Qdrant (поднимается в docker compose)
    retriever_type: str = "local"
    knowledge_base_dir: str = "knowledge_base"
    rag_top_k: int = 3
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "support_kb"

    # --- Логика принятия решения агентом ---
    # Ниже этого порога уверенности агент передаёт тикет человеку.
    min_confidence: float = 0.6

    # ═══════════════════════════════════════════════════════════════════════
    # Ниже — только для AGENT_TYPE=langgraph (занятие 2: оркестрация, надёжность)
    # ═══════════════════════════════════════════════════════════════════════

    # --- Персистентность графа (checkpoint) ---
    # "postgres" — AsyncPostgresSaver, переживает рестарт контейнера (нужно для
    #              HITL: тикет может ждать оператора часами). "memory" — только
    #              для быстрой локальной разработки, не переживает рестарт.
    langgraph_checkpointer: str = "postgres"
    # ВАЖНО: другой драйвер (psycopg), чем DATABASE_URL (asyncpg) — у чекпоинтера
    # LangGraph свой клиент к той же базе. Схема URL начинается с "postgresql://",
    # без "+asyncpg".
    checkpointer_dsn: str = "postgresql://postgres:postgres@db:5432/support"

    # --- Бюджет: защита от runaway / Token DoS ---
    # Сколько шагов графа (retrieve + decide_* считаются каждый за 1) можно
    # потратить на один вопрос, прежде чем граф принудительно эскалирует.
    agent_max_steps: int = 4
    # Суммарное время (сек) на один вопрос — что раньше кончится, то и сработает.
    agent_max_seconds: float = 25.0

    # --- Retry Policy для узлов, которые ходят в LLM/RAG по сети ---
    agent_retry_max_attempts: int = 3
    agent_retry_initial_interval: float = 0.5

    # --- Авто-маршрутизация: дешёвый режим → эскалация по триггеру качества ---
    # Модель для второго (escalated) прохода, когда "дешёвый" тир не уверен.
    # Пусто — используем ту же модель, что и llm_model, но с temperature=0
    # (более осторожный, детерминированный проход тем же провайдером).
    # Впишите более сильную модель, если хотите настоящую эскалацию тира,
    # например: openai/gpt-4o
    agent_escalation_model: str = ""

    # --- Human-in-the-Loop: третий, средний порог ---
    # confidence в [human_approval_threshold, min_confidence) → у агента есть
    # черновик ответа, но он недостаточно уверен, чтобы отправить сам — черновик
    # ждёт одобрения оператора (см. api/admin.py: resolve-draft).
    # confidence ниже human_approval_threshold → черновик бесполезен, обычная
    # эскалация (как в full-агенте).
    human_approval_threshold: float = 0.35


@lru_cache
def get_settings() -> Settings:
    """Кэшируем настройки — читаем окружение один раз за жизнь процесса."""
    return Settings()

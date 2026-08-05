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
    # "tooluse"   — граф + 10 инструментов (реальные API + серверные), защита от
    #               инъекций, HITL для критических действий (занятие 3)
    agent_type: str = "tooluse"

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
    # Сколько шагов графа (retrieve + decide_* + dispatch_tool считаются
    # каждый за 1) можно потратить на один вопрос, прежде чем граф
    # принудительно эскалирует.
    #
    # 15, не 4: граф занятия 3 (AGENT_TYPE=tooluse) тратит 2 шага на КАЖДЫЙ
    # цикл "decide_or_act -> dispatch_tool" — один вызов инструмента уже
    # стоит 5 шагов (retrieve + decide + dispatch + decide + finalize_decide).
    # При старом дефолте (4, унаследован от занятия 2, где цикла нет вообще)
    # бюджет исчерпывался раньше, чем агент успевал хоть раз воспользоваться
    # инструментом, — реальная находка при первом end-to-end прогоне v4.
    # Более узкая и специфичная защита именно от бесконечного цикла
    # инструментов — `tool_max_calls` ниже, она и должна срабатывать первой
    # на практике; `agent_max_steps` — генеральный, более щедрый потолок.
    agent_max_steps: int = 15
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

    # ═══════════════════════════════════════════════════════════════════════
    # Ниже — только для AGENT_TYPE=tooluse (занятие 3: tool use, интеграции)
    # ═══════════════════════════════════════════════════════════════════════

    # --- Таймаут исходящих запросов инструментов (публичные API) ---
    tool_http_timeout_seconds: float = 8.0

    # --- Бюджет цикла инструментов ---
    # Сколько раз агент может сходить в инструмент за ОДИН вопрос, прежде чем
    # граф принудительно завершит цикл эскалацией (защита от runaway-агента,
    # который бесконечно зовёт инструменты, не приходя к ответу).
    tool_max_calls: int = 4

    # --- Least-privilege: лимит автоматического возврата (create_refund) ---
    # Возврат ДОРОЖЕ этой суммы инструмент не проведёт сам — граф остановится
    # на tool_approval_gate (interrupt) и подождёт оператора. Лимит проверяется
    # В КОДЕ инструмента (agent/tools/server_side.py), а не в промпте — так что
    # переопределить его инъекцией через текст тикета невозможно.
    refund_auto_limit: float = 1000.0

    # --- Санитайзер входа (agent/security/sanitizer.py) ---
    # "enforce"  — BLOCK останавливает обработку тикета ДО вызова LLM (прод-режим).
    # "log_only" — ничего не блокирует, но результат виден в meta сообщения
    #              (удобно для демо "наивный vs защищённый" на занятии).
    # "off"      — санитайзер не запускается вообще.
    sanitizer_mode: str = "enforce"

    # ═══════════════════════════════════════════════════════════════════════
    # Игровой режим: участники пытаются «взломать» агента ради баланса
    # ═══════════════════════════════════════════════════════════════════════

    # Включает вход по логину, балансы, лидерборд и загрузку файлов.
    # Выключено по умолчанию — эталоны занятий 1-3 работают как раньше.
    game_mode: bool = False

    # Логин участника — без пароля (см. models.Player). Сессия подписывается
    # этим секретом: без него игрок мог бы просто подставить чужой логин в
    # cookie и присвоить его очки. В проде обязателен свой длинный случайный.
    session_secret: str = ""
    session_ttl_hours: int = 24

    # Админка (очередь оператора, управление игрой) — настоящий вход по
    # логину и паролю. Пустой пароль при GAME_MODE=true = отказ старта,
    # см. `validate_game_settings()`: открытая админка в публичном деплое
    # означала бы, что любой участник сам себе одобряет возвраты.
    admin_username: str = "admin"
    admin_password: str = ""

    # Стартовый баланс участника и вымышленная валюта для отображения.
    game_starting_balance: float = 0.0

    # Антифлуд: пауза между обращениями к агенту для ОДНОГО игрока.
    # Публичный деплой + платная LLM = один участник в цикле может сжечь весь
    # бюджет OpenRouter за минуты, поэтому лимит здесь не «на всякий случай».
    game_cooldown_seconds: float = 5.0

    # --- Загрузка файлов (инструмент read_attached_file) ---
    upload_dir: str = "/data/uploads"
    upload_max_bytes: int = 5 * 1024 * 1024  # 5 МБ
    upload_max_files_per_ticket: int = 3
    # Сколько символов извлечённого текста отдаём модели: файл на 5 МБ
    # текста не должен ни разорвать контекст, ни выжечь бюджет токенов.
    upload_max_extract_chars: int = 20_000

    # Разрешить cookie сессии без HTTPS. Только для локальной разработки:
    # в проде за Coolify всегда HTTPS, и cookie должна быть secure, иначе
    # токен уедет по открытому каналу.
    game_allow_insecure_cookies: bool = False

    # Домены фронтенда для CORS в игровом режиме, через запятую.
    # Пусто = фронтенд и API на одном домене (наш случай в Coolify), тогда
    # кросс-доменные запросы не нужны вовсе. Wildcard "*" здесь запрещён
    # браузером вместе с cookie-сессиями, поэтому список именно явный.
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def validate_game_settings(settings: "Settings") -> None:
    """Fail fast на старте, если игровой режим включён, но не настроен.

    Отдельная функция, а не pydantic-валидатор: это проверка «конфигурация
    безопасна для публичного деплоя», и падать она должна на старте
    приложения с внятным текстом, а не при первом запросе к админке.
    """
    if not settings.game_mode:
        return
    problems = []
    if len(settings.session_secret) < 32:
        problems.append("SESSION_SECRET не задан или короче 32 символов")
    if len(settings.admin_password) < 8:
        problems.append("ADMIN_PASSWORD не задан или короче 8 символов")
    if problems:
        raise RuntimeError(
            "GAME_MODE=true, но конфигурация небезопасна для публичного деплоя: "
            + "; ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    """Кэшируем настройки — читаем окружение один раз за жизнь процесса."""
    return Settings()

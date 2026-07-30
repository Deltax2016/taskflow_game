# TaskFlow Support — v3 (LangGraph: оркестрация и надёжность)

Тот же ИИ-агент техподдержки, что и в `../v2-solution`, но реализация агента
теперь — **явный граф состояний на LangGraph**, а не одна функция `handle()`.

Интерфейс агента не изменился: `TicketService` по-прежнему вызывает
`agent.handle(request)` и получает `AgentResult`. Внутри вместо одного вызова
LLM теперь работает граф: RAG → дешёвый проход → (если не уверен) более
внимательный проход → (если средне уверен) черновик ждёт оператора → ответ.

Стартовый шаблон занятия 1 — в `../v1-template`; эталон занятия 1 — в
`../v2-solution`. Это — эталон занятия 2 (оркестрация и надёжность).

## Что нового по сравнению с v2

- **Явный граф состояний** (`backend/app/agent/graph/`) вместо цепочки вызовов
  в одной функции: `State`, `Node`, условные `Edge`, `Reducer`.
- **Retry Policy** на узлах, которые ходят в LLM/RAG по сети — повторяем
  только временные сбои (таймаут, 429, 5xx), не логические ошибки.
- **Бюджет**: лимит шагов и времени на один вопрос — защита от runaway.
- **Авто-маршрутизация по качеству**: дешёвый проход (быстро/дёшево) →
  при низкой уверенности — второй, более внимательный проход.
- **Human-in-the-Loop**: при средней уверенности агент готовит черновик, но
  не отправляет его сам — оператор одобряет (можно с правкой) или отклоняет
  прямо в админке.
- **Персистентность (checkpoint)**: состояние графа переживает рестарт
  контейнера — черновик, ждущий оператора, не теряется.

Агент по-прежнему подключается через `.env` (`AGENT_TYPE=langgraph|full|simple`)
— реализации `v1`/`v2` (`SimpleRagAgent`, `SupportAgent`) никуда не делись,
можно сравнивать поведение бок о бок.

## Стек

- **Backend:** Python, FastAPI, SQLAlchemy (async), PostgreSQL, **LangGraph**
- **Frontend:** React + TypeScript + Vite
- **Agent:** RAG (локальный поиск / Qdrant) + LLM (OpenRouter) + LangGraph

## Архитектура (кратко)

```
Frontend (React)
   │  REST /api
   ▼
FastAPI ── api/ ─── services/tickets.py ──► Agent (интерфейс)
   │                    │                      ├─ SimpleRagAgent      (v1: RAG + LLM)
   ▼                    ▼                      ├─ SupportAgent        (v2: сам решает)
Postgres           статусы тикета               └─ LangGraphSupportAgent (v3: граф)
                                                          │
                                          ┌───────────────┼──────────────────┐
                                       Retriever         LLM          Checkpointer
                                    (local / Qdrant)  (OpenRouter)   (Postgres/memory)
```

Ключевая идея не изменилась: `services/` и `api/` зависят от **интерфейса**
`Agent` (`app/agent/base.py`), а не от конкретной реализации — поэтому
`LangGraphSupportAgent` встал рядом с `SimpleRagAgent`/`SupportAgent` без
изменений в `api/`. Единственное осознанное расширение контракта —
`AgentResult.meta` (доп. диагностика: model_tier, thread_id, черновик для
HITL) и два узких протокола сверх `Agent` (`DraftResumable`, `Traceable`) —
подробнее прямо в `app/agent/base.py`. Общий принцип программирования от
интерфейсов — в [`../docs/architecture.md`](../docs/architecture.md).

## Быстрый старт (Docker)

```bash
cp .env.example .env
# впишите OPENROUTER_API_KEY в .env  (https://openrouter.ai/keys)
docker compose up --build
```

- Публичная страница: http://localhost:5173
- Админка оператора:  http://localhost:5173/admin
- API-документация:   http://localhost:8000/docs

> Порты те же, что у `v1-template`/`v2-solution` (5173/8000/5432) — не
> поднимайте несколько версий одновременно без переопределения портов.

### RAG на Qdrant (опционально)

```bash
docker compose --profile qdrant up --build
# и в .env: RETRIEVER_TYPE=qdrant
```

## Локальный запуск (без Docker)

```bash
# 1. Postgres должен быть доступен; в .env укажите host=localhost.
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env         # OPENROUTER_API_KEY + DATABASE_URL/CHECKPOINTER_DSN(localhost)
uvicorn app.main:app --reload

# frontend (в отдельном терминале)
cd frontend
npm install
npm run dev                     # http://localhost:5173, /api проксируется на :8000
```

## Как увидеть Human-in-the-Loop вживую

1. Задайте вопрос, на который в базе знаний есть только частичный/косвенный
   ответ — агент, скорее всего, вернёт среднюю уверенность и оставит черновик.
2. Откройте `/admin` → тикет будет в очереди со статусом «Ждёт оператора», а в
   ленте сообщений — черновик агента с текстом и уверенностью.
3. Отредактируйте текст черновика (по желанию) и нажмите «Отправить как ответ
   агента» — либо «Отклонить, отвечу сам».
4. Пороги подбираются в `.env` (`MIN_CONFIDENCE`, `HUMAN_APPROVAL_THRESHOLD`) —
   если черновики не появляются, попробуйте вопрос на грани базы знаний или
   временно понизьте `MIN_CONFIDENCE`.

## Структура проекта

```
backend/app/
├── main.py                 # точка входа: lifespan (+ checkpointer), CORS, роутеры
├── config.py               # настройки из окружения (+ бюджет/retry/HITL для v3)
├── database.py             # async-движок и сессии (тикеты, SQLAlchemy/asyncpg)
├── models.py                # ORM: Ticket, Message + статусы
├── schemas.py               # Pydantic-схемы HTTP-слоя (+ DraftResolution)
├── api/                     # роутеры (public / admin) + DI
├── services/
│   └── tickets.py           # оркестрация тикетов, статусная машина, resume_agent_draft
└── agent/                   # ★ слой агента
    ├── base.py               #   интерфейсы Agent/LLM/Retriever + DraftResumable/Traceable
    ├── schemas.py             #   строгий вход/выход агента (+ AgentResult.meta)
    ├── prompts/               #   промпты отдельным слоем (+ SYSTEM_PROMPT_ESCALATED)
    ├── llm.py                 #   LLM через OpenRouter
    ├── rag.py                 #   ретриверы: LocalRetriever, QdrantRetriever
    ├── simple.py               #   v1: RAG + LLM
    ├── support.py              #   v2: агент решает сам
    ├── langgraph_agent.py      # ★ v3: адаптер граф ↔ интерфейс Agent
    ├── graph/                  # ★ v3: LangGraph
    │   ├── state.py             #   GraphState — явное состояние + Reducer'ы
    │   ├── nodes.py             #   retrieve / decide_* / human_gate / finalize_*
    │   ├── routing.py           #   условные переходы (бюджет, пороги уверенности)
    │   ├── build.py             #   сборка графа: узлы, рёбра, RetryPolicy
    │   └── checkpointer.py      #   персистентность (Postgres / in-memory)
    └── factory.py               #   сборка агента по настройкам (simple/full/langgraph)
```

## Как поменять реализацию агента

В `.env`:

```
AGENT_TYPE=simple      # v1: простой пайплайн RAG + LLM
AGENT_TYPE=full        # v2: агент сам решает, знает ли ответ
AGENT_TYPE=langgraph   # v3: граф состояний, retry/бюджет/HITL (по умолчанию)
```

Остальной код при этом не меняется — в этом и смысл интерфейса `Agent`.

## Переменные окружения (новое к v2, см. `.env.example` целиком)

| Переменная | Что регулирует |
|---|---|
| `LANGGRAPH_CHECKPOINTER` | `postgres` (переживает рестарт) / `memory` (для быстрой разработки) |
| `CHECKPOINTER_DSN` | Строка подключения чекпоинтера (драйвер psycopg, не asyncpg) |
| `AGENT_MAX_STEPS` / `AGENT_MAX_SECONDS` | Бюджет на один вопрос |
| `AGENT_RETRY_MAX_ATTEMPTS` / `AGENT_RETRY_INITIAL_INTERVAL` | Retry Policy сетевых узлов |
| `AGENT_ESCALATION_MODEL` | Модель для второго (escalated) прохода; пусто — та же модель, temperature=0 |
| `HUMAN_APPROVAL_THRESHOLD` | Порог, ниже которого черновик не предлагается (сразу оператору) |

## Debug-эндпоинт: трасса графа

`GET /api/admin/agent-trace/{thread_id}` — пошаговая история состояний одного
запуска (`thread_id` лежит в `meta.thread_id` сообщения агента). В проде для
этого используют LangGraph Studio / Langfuse — здесь тот же источник данных,
но напрямую через `/docs`.

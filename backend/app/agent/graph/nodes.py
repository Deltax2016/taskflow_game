"""Узлы (Node) графа — маленькие функции state -> частичное обновление state.

Важный принцип, который проговариваем на занятии — **идемпотентность узлов**:
ни один узел здесь не пишет в БД тикетов и не имеет побочных эффектов, кроме
похода во внешний RAG/LLM. Если LangGraph повторит узел (RetryPolicy) или
процесс перезапустится и checkpoint восстановит состояние, повторный вызов
узла безопасен — он просто пересчитает то же самое (или ещё раз сходит в LLM,
что стоит денег, но не портит данные). Запись в Postgres тикетов происходит
СНАРУЖИ графа, только после того как граф целиком завершился
(см. `TicketService._apply_agent_result`).

Узлы — фабрики: `make_*_node(...)` принимают зависимости (llm, retriever,
settings) через замыкание и возвращают саму функцию узла `(state) -> dict`.
Зависимости — не часть состояния графа: State — это данные конкретного
запроса, а llm/retriever — сервисы уровня приложения.
"""

from collections.abc import Awaitable, Callable

import httpx
from pydantic import ValidationError

from app.agent.base import LLM, Retriever
from app.agent.graph.state import GraphState
from app.agent.prompts import build_context, build_user_prompt
from app.agent.schemas import ChatTurn, LLMDecision
from app.config import Settings

NodeFn = Callable[[GraphState], Awaitable[dict]]


def is_transient_llm_error(exc: BaseException) -> bool:
    """Что имеет смысл повторять через RetryPolicy, а что — нет.

    Это прямой вывод из реального инцидента (см. lesson-plan): 400 Bad Request
    из-за отсутствия слова "json" в промпте — это БАГ, повтор его не лечит,
    только жжёт бюджет и время. А вот таймаут, 429 (rate limit) и 5xx у
    провайдера — временные сбои, которые почти наверняка пройдут со второй
    попытки. RetryPolicy должен различать эти два случая, а не ретраить всё
    подряд.
    """
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def make_retrieve_node(retriever: Retriever, settings: Settings) -> NodeFn:
    """RAG: находим контекст. Чистая функция вопроса — идеальный кандидат на
    идемпотентность и (при желании) на кеширование через `cache_policy` узла.
    """

    async def retrieve(state: GraphState) -> dict:
        chunks = await retriever.search(state["question"], settings.rag_top_k)
        return {
            "chunks": [c.model_dump() for c in chunks],
            "context": build_context(chunks),
            "sources": sorted({c.source for c in chunks}),
            "steps_used": 1,
        }

    return retrieve


def make_decide_node(
    llm: LLM,
    *,
    tier: str,
    system_prompt: str,
) -> NodeFn:
    """Просим LLM структурированное решение (LLMDecision), с явным тиром.

    `llm` здесь — уже настроенный под нужный тир экземпляр `OpenRouterLLM`
    (своя модель/температура — см. `build.py`: `build_llm_for_tier`). Мы
    намеренно НЕ добавляем параметры `model=`/`temperature=` в сам интерфейс
    `LLM.complete_structured` — интерфейс агента из первого занятия не должен
    знать про «тиры», это деталь конкретной реализации (LangGraph-графа).
    Вместо этого под каждый тир собирается свой объект `LLM` с нужными
    настройками — граф просто вызывает `llm.complete_structured(...)` как
    обычно, ничего не зная о том, что это второй, более сильный проход.

    Ошибки двух разных природ обрабатываются по-разному:
      * `httpx.*` (сеть/провайдер) — НЕ ловим здесь, пусть всплывёт из узла:
        `RetryPolicy` на уровне `add_node` решит, стоит ли повторить
        (см. `is_transient_llm_error`), а если попытки исчерпаны — ошибка
        долетит до `LangGraphSupportAgent.handle()`, это последний safety net
        (эскалация человеку, как и в v1/v2).
      * `ValidationError` (модель вернула не-JSON/не по схеме) — это НЕ сбой
        инфраструктуры, а признак низкого качества ответа. Ловим здесь и
        превращаем в обычное состояние `can_answer=False` — дальше решает
        РОУТИНГ графа (эскалировать тир, уйти к человеку и т.д.), а не try/except.
    """

    async def decide(state: GraphState) -> dict:
        history = [ChatTurn(**turn) for turn in state.get("history", [])]
        messages = [
            *history,
            ChatTurn(role="user", content=build_user_prompt(state["question"], state.get("context", ""))),
        ]
        try:
            decision = await llm.complete_structured(system_prompt, messages, LLMDecision)
        except ValidationError as exc:
            return {
                "can_answer": False,
                "answer": None,
                "confidence": 0.0,
                "reason": f"LLM вернула ответ, не прошедший валидацию ({tier}-тир): {exc}",
                "model_tier": tier,
                "steps_used": 1,
                "tier_attempts": 1,
            }

        return {
            "can_answer": decision.can_answer,
            "answer": decision.answer,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "model_tier": tier,
            "steps_used": 1,
            "tier_attempts": 1,
        }

    return decide


def finalize_answer(state: GraphState) -> dict:
    """Терминальный узел: агент отвечает сам. Ничего не решает — только
    проставляет явный маркер `action`, по которому снаружи графа понимают,
    что произошло, без необходимости заново парсить всё состояние.
    """
    return {"action": "answer"}


def finalize_escalate(state: GraphState) -> dict:
    """Терминальный узел: обычная эскалация (низкая уверенность или отказ
    оператора в human_gate). Если ни один decide-узел не выставил reason —
    подставляем причину по умолчанию.
    """
    update: dict = {"action": "escalate"}
    if not state.get("reason"):
        update["reason"] = "Недостаточно данных для ответа — передано человеку."
    return update


def finalize_budget_escalate(state: GraphState) -> dict:
    """Терминальный узел: эскалация ИМЕННО из-за исчерпанного бюджета
    (`routing._budget_exceeded`) — отдельный узел от `finalize_escalate`.

    Почему нельзя просто переиспользовать `finalize_escalate` с тем же
    условием "reason пуст → подставить дефолт": к моменту, когда бюджет
    исчерпан, `state["reason"]` почти всегда УЖЕ заполнен — последним
    decide-узлом, причём иногда с ВЫСОКОЙ уверенностью (бюджет проверяется
    строго после decide, а не до). Если бы мы полагались на "reason пуст",
    оператор увидел бы обоснование LLM («точно есть в контексте») вместо
    настоящей причины эскалации — бюджета. Поэтому здесь reason
    перезаписывается БЕЗУСЛОВНО.
    """
    return {
        "action": "escalate",
        "reason": "Бюджет шагов/времени агента исчерпан — передано человеку.",
    }


def human_gate(state: GraphState) -> dict:
    """Human-in-the-Loop: агент подготовил черновик, но не уверен настолько,
    чтобы отправить его сам — просит человека одобрить/поправить/отклонить,
    ПРЕЖДЕ чем ответ уйдёт пользователю.

    `interrupt(payload)` останавливает выполнение графа прямо здесь и
    возвращает `payload` наружу (см. `LangGraphSupportAgent.handle`,
    ветка `"__interrupt__" in result`). Процесс/контейнер можно перезапустить
    в промежутке — checkpointer (Postgres) помнит, что мы стоим именно на этом
    узле этого треда. Когда оператор решит (approve/reject), кто-то вызовет
    `graph.ainvoke(Command(resume=...), config)` с тем же `thread_id` — тогда
    `interrupt()` вернёт то, что передали в `resume`, и функция продолжится
    со следующей строки, как обычный вызов.
    """
    from langgraph.types import interrupt  # локальный импорт: узел — единственное место, где это нужно

    decision = interrupt(
        {
            "draft_answer": state.get("answer"),
            "confidence": state.get("confidence", 0.0),
            "reason": state.get("reason", ""),
        }
    )

    if decision.get("approve"):
        edited = decision.get("edited_answer")
        return {
            "action": "answer",
            "answer": edited or state.get("answer"),
            "reason": "Черновик агента одобрен оператором" + (" (с правкой)" if edited else ""),
        }
    return {
        "action": "escalate",
        "reason": "Оператор отклонил черновик агента — решает сам.",
    }

"""Условные переходы (Conditional Edges) — маршрутизация по состоянию.

Каждая функция здесь — чистая функция `state -> имя следующего узла`. Она НЕ
меняет состояние (это дело узлов) и не делает I/O — только решает, куда идти
дальше. Настройки (`Settings`) нужны только для порогов, поэтому функции —
тоже фабрики (`make_route_after_*`), как и узлы в `nodes.py`.

Три уровня уверенности, три исхода — это и есть «эскалация по триггерам
качества» из лекции:
  * `confidence >= min_confidence`            → отвечаем сами (ANSWER)
  * `human_approval_threshold <= confidence`  → есть черновик, но нужен
                                                 человек, чтобы его одобрить
                                                 (Human-in-the-Loop, `human_gate`)
  * `confidence < human_approval_threshold`   → черновик бесполезен, сразу
                                                 человеку (ESCALATE)

Бюджет (`agent_max_steps` / `agent_max_seconds`) проверяется ПЕРЕД каждым
решением о переходе к более дорогому (escalated) тиру — это и есть защита от
runaway/Token DoS из лекции: агент не может бесконечно эскалировать сам себя.
"""

import time
from typing import Literal

from app.agent.graph.state import GraphState
from app.config import Settings

RouteAfterCheap = Literal["finalize_answer", "decide_escalated", "finalize_budget_escalate"]
RouteAfterEscalated = Literal["finalize_answer", "human_gate", "finalize_escalate", "finalize_budget_escalate"]
RouteAfterHumanGate = Literal["finalize_answer", "finalize_escalate"]


def _budget_exceeded(state: GraphState, settings: Settings) -> bool:
    """Единая проверка бюджета: шаги ИЛИ время — что наступит раньше."""
    if state.get("steps_used", 0) >= settings.agent_max_steps:
        return True
    started_at = state.get("started_at")
    if started_at is not None and (time.monotonic() - started_at) > settings.agent_max_seconds:
        return True
    return False


def make_route_after_cheap(settings: Settings):
    def route(state: GraphState) -> RouteAfterCheap:
        # Бюджет проверяем ПЕРВЫМ и ведём в ОТДЕЛЬНЫЙ терминальный узел
        # (finalize_budget_escalate), а не в обычный finalize_escalate —
        # иначе оператор увидит reason уверенного decide_cheap вместо
        # настоящей причины эскалации (см. nodes.finalize_budget_escalate).
        if _budget_exceeded(state, settings):
            return "finalize_budget_escalate"
        if state.get("can_answer") and state.get("confidence", 0.0) >= settings.min_confidence:
            return "finalize_answer"
        # Дешёвый тир не уверен — триггер качества: даём шанс более
        # внимательному (escalated) проходу, пока бюджет позволяет.
        return "decide_escalated"

    return route


def make_route_after_escalated(settings: Settings):
    def route(state: GraphState) -> RouteAfterEscalated:
        if _budget_exceeded(state, settings):
            return "finalize_budget_escalate"
        confidence = state.get("confidence", 0.0)
        if state.get("can_answer") and confidence >= settings.min_confidence:
            return "finalize_answer"
        if confidence >= settings.human_approval_threshold:
            return "human_gate"
        return "finalize_escalate"

    return route


def route_after_human_gate(state: GraphState) -> RouteAfterHumanGate:
    """После `human_gate` состояние `action` уже выставлено самим узлом
    (в момент resume, см. `nodes.human_gate`) — здесь просто читаем решение.
    """
    return "finalize_answer" if state.get("action") == "answer" else "finalize_escalate"

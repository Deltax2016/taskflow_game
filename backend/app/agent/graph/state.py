"""Явное состояние графа (State) — контракт между узлами (Node).

Это ключевая идея LangGraph в сравнении с «цепочкой» (v1/v2 `support.py`):
там прогресс агента жил в стеке вызовов одной Python-функции. Здесь каждый шаг
читает и дополняет ОДИН явный объект состояния — его можно сохранить
(checkpoint), проинспектировать между шагами, восстановить после сбоя.

Reducer'ы (`Annotated[int, operator.add]`). Когда узел возвращает частичное
обновление состояния, LangGraph обычно просто ПЕРЕЗАПИСЫВАЕТ поле новым
значением. Reducer меняет это правило: для `steps_used`/`tier_attempts` мы
хотим СКЛАДЫВАТЬ значения (узел вернул `{"steps_used": 1}` → счётчик
увеличился на 1), а не терять предыдущий счёт. Это и есть механизм бюджета
из лекции: явный, видимый в состоянии счётчик шагов, а не скрытая переменная
где-то в цикле.
"""

import operator
from typing import Annotated, Literal

from typing_extensions import TypedDict


class GraphState(TypedDict, total=False):
    """total=False: узел обновляет только те поля, которыми занимается.

    Не каждый узел обязан знать обо всех полях — `retrieve` не трогает
    `confidence`, `decide_*` не трогает `chunks`. Так граф остаётся читаемым:
    видно, кто за что отвечает.
    """

    # --- Вход (заполняется один раз при первом вызове) ---
    question: str
    history: list[dict]  # список ChatTurn.model_dump() — плоские dict для чекпоинтера
    started_at: float  # time.monotonic() на старте — бюджет по времени

    # --- Заполняет retrieve ---
    chunks: list[dict]  # список RetrievedChunk.model_dump()
    context: str
    sources: list[str]

    # --- Заполняют decide_cheap / decide_escalated ---
    can_answer: bool
    answer: str | None
    confidence: float
    reason: str
    model_tier: Literal["cheap", "escalated"]

    # --- Бюджет: складывается на каждом шаге, который его тратит ---
    steps_used: Annotated[int, operator.add]
    tier_attempts: Annotated[int, operator.add]

    # --- Итог, который читает LangGraphSupportAgent ---
    action: Literal["answer", "escalate"]

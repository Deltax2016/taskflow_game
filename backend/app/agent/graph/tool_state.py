"""Расширение GraphState для tool-use графа (занятие 3).

Отдельный TypedDict, а не правка `state.py`, — чтобы граф занятия 2
(`build.py`, использует ровно `GraphState`) остался нетронутым. TypedDict
наследуется как обычный класс — `ToolGraphState` включает все поля
`GraphState` плюс то, что нужно для ReAct-цикла.
"""

import operator
from typing import Annotated

from app.agent.graph.state import GraphState


class ToolGraphState(GraphState, total=False):
    # Сырой OpenAI-транскрипт цикла инструментов: assistant(tool_calls) + tool(результат).
    # ОТДЕЛЬНО от `history` (ChatTurn) — history это переписка ТИКЕТА между
    # сообщениями пользователя, а это — внутренний scratch-pad ОДНОГО вызова
    # `agent.handle()`, который не переживает завершение графа.
    tool_messages: Annotated[list[dict], operator.add]

    # Отдельный от steps_used счётчик — конкретно вызовов инструментов.
    # steps_used (из GraphState) — общий бюджет графа (retrieve + decide + tools
    # вместе); tool_calls_used — более узкая защита именно от агента,
    # застрявшего в цикле "вызов инструмента -> снова вызов инструмента",
    # даже если общий бюджет шагов ещё не исчерпан.
    tool_calls_used: Annotated[int, operator.add]

    # Что решила модель на последнем витке decide_or_act — читает routing.py
    # и dispatch_tool/tool_approval_gate.
    pending_tool_name: str
    pending_tool_args: dict
    pending_tool_call_id: str

    # Текстовый ответ модели, когда она решила, что вызовов больше не нужно —
    # идёт на структурированную проверку уверенности (finalize_decision),
    # тем же механизмом, что и в занятии 2 (LLMDecision).
    draft_from_tools: str | None

    # Разовый флаг: human_gate одобрил ИМЕННО этот вызов инструмента.
    # Инструмент (create_refund) увидит его как `_approved_by_human=True`.
    tool_call_approved: bool

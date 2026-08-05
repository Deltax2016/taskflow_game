"""Условные переходы для ReAct-цикла (decide_or_act ⇄ dispatch_tool).

Финальную структурированную проверку уверенности (после того как цикл собрал
факты) роутит НЕ этот модуль, а `graph.routing.make_route_after_cheap` /
`make_route_after_escalated` из занятия 2 — БЕЗ ИЗМЕНЕНИЙ, просто с другим
`path_map` при сборке графа (см. `build_tool_graph.py`): те функции возвращают
строковые ключи ("finalize_answer", "decide_escalated", ...), а какой реальный
узел стоит за ключом — решает `path_map` конкретного вызова
`add_conditional_edges`, не сама функция роутинга.
"""

from typing import Literal

from app.agent.graph.routing import _budget_exceeded
from app.agent.graph.tool_state import ToolGraphState
from app.config import Settings

RouteAfterDecideOrAct = Literal[
    "dispatch_tool", "tool_approval_gate", "finalize_decide", "finalize_budget_escalate"
]
RouteAfterApprovalGate = Literal["dispatch_tool", "finalize_escalate"]


def make_route_after_decide_or_act(settings: Settings, registry):
    def route(state: ToolGraphState) -> RouteAfterDecideOrAct:
        if _budget_exceeded(state, settings) or state.get("tool_calls_used", 0) >= settings.tool_max_calls:
            return "finalize_budget_escalate"

        tool_name = state.get("pending_tool_name")
        if not tool_name:
            # Модель ответила текстом — цикл сбора фактов закончен.
            return "finalize_decide"

        tool = registry.get(tool_name)
        if tool.requires_approval(state.get("pending_tool_args") or {}):
            return "tool_approval_gate"
        return "dispatch_tool"

    return route


def route_after_approval_gate(state: ToolGraphState) -> RouteAfterApprovalGate:
    """`tool_approval_gate` (nodes.py) уже выставил `tool_call_approved=True`
    при одобрении или `action="escalate"` при отказе — здесь просто читаем.
    """
    return "dispatch_tool" if state.get("tool_call_approved") else "finalize_escalate"

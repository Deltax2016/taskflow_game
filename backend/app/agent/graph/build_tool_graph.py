"""Сборка ReAct-графа с инструментами (занятие 3) — поверх примитивов
занятия 2 (`build.py`, `nodes.py`, `routing.py`), не вместо них.

    START ──► retrieve ──► decide_or_act ─┬─(текст)───────────────────────► finalize_decide_cheap ─┬─(уверен)──► finalize_answer ──► END
                                           │                                                         ├─(не уверен, бюджет ok)─► finalize_decide_escalated ─┬─(уверен)──► finalize_answer ──► END
                                           │                                                         │                                                       ├─(средне)──► human_gate ─┬─(approve)─► finalize_answer ► END
                                           │                                                         │                                                       │                          └─(reject)──► finalize_escalate ► END
                                           │                                                         │                                                       ├─(низко)───► finalize_escalate ──► END
                                           │                                                         │                                                       └─(бюджет)──► finalize_budget_escalate ► END
                                           │                                                         └─(бюджет)────────────────────────────────────────────► finalize_budget_escalate ────────────► END
                                           │
                                           ├─(инструмент, без approval)──► dispatch_tool ──┐
                                           ├─(инструмент, нужен approval)──► tool_approval_gate ─┬─(approve)──► dispatch_tool ──┐
                                           │                                                      └─(reject)───► finalize_escalate ──► END
                                           └─(бюджет/лимит вызовов исчерпан)──► finalize_budget_escalate ──► END
                                                                                                               │
                           decide_or_act ◄────────────────────────────────────────────────────────────────────┘ (цикл ReAct)

`finalize_decide_cheap`/`finalize_decide_escalated` переиспользуют РОУТИНГ
занятия 2 (`routing.make_route_after_cheap`/`make_route_after_escalated`)
БЕЗ ИЗМЕНЕНИЙ — только с другим `path_map`, который направляет тот же
строковый ключ "decide_escalated" на узел `finalize_decide_escalated`, а не
на `decide_escalated`. `human_gate`/`finalize_answer`/`finalize_escalate`/
`finalize_budget_escalate` — те же самые функции-узлы из `nodes.py`, без
единой правки.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, RetryPolicy

from app.agent.base import LLM
from app.agent.graph import nodes, routing, tool_nodes, tool_routing
from app.agent.graph.tool_state import ToolGraphState
from app.agent.prompts import SYSTEM_PROMPT_ESCALATED, SYSTEM_PROMPT_FULL, SYSTEM_PROMPT_TOOLS
from app.agent.tools import ToolRegistry
from app.config import Settings

_LLM_RETRY_POLICY_KW = {"retry_on": nodes.is_transient_llm_error}


def build_tool_graph(
    cheap_llm: LLM,
    escalated_llm: LLM,
    retriever,
    registry: ToolRegistry,
    settings: Settings,
    checkpointer: Checkpointer = None,
):
    retry_policy = RetryPolicy(
        max_attempts=settings.agent_retry_max_attempts,
        initial_interval=settings.agent_retry_initial_interval,
        **_LLM_RETRY_POLICY_KW,
    )
    retrieve_retry_policy = RetryPolicy(max_attempts=2, retry_on=nodes.is_transient_llm_error)
    # dispatch_tool ходит в публичные API — та же классификация транзиентных
    # ошибок (см. agent/resilience.py), просто более щедрое число попыток:
    # свободные публичные API (без SLA) шатает чаще платного LLM-провайдера.
    tool_retry_policy = RetryPolicy(max_attempts=3, initial_interval=0.5, retry_on=nodes.is_transient_llm_error)

    graph = StateGraph(ToolGraphState)

    graph.add_node("retrieve", nodes.make_retrieve_node(retriever, settings), retry_policy=retrieve_retry_policy)
    graph.add_node(
        "decide_or_act",
        tool_nodes.make_decide_or_act_node(cheap_llm, registry, system_prompt=SYSTEM_PROMPT_TOOLS),
        retry_policy=retry_policy,
    )
    graph.add_node("dispatch_tool", tool_nodes.make_dispatch_tool_node(registry), retry_policy=tool_retry_policy)
    graph.add_node("tool_approval_gate", tool_nodes.tool_approval_gate)
    graph.add_node(
        "finalize_decide_cheap",
        tool_nodes.make_finalize_decision_node(cheap_llm, tier="cheap", system_prompt=SYSTEM_PROMPT_FULL),
        retry_policy=retry_policy,
    )
    graph.add_node(
        "finalize_decide_escalated",
        tool_nodes.make_finalize_decision_node(escalated_llm, tier="escalated", system_prompt=SYSTEM_PROMPT_ESCALATED),
        retry_policy=retry_policy,
    )
    graph.add_node("human_gate", nodes.human_gate)
    graph.add_node("finalize_answer", nodes.finalize_answer)
    graph.add_node("finalize_escalate", nodes.finalize_escalate)
    graph.add_node("finalize_budget_escalate", nodes.finalize_budget_escalate)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "decide_or_act")

    graph.add_conditional_edges(
        "decide_or_act",
        tool_routing.make_route_after_decide_or_act(settings, registry),
        {
            "dispatch_tool": "dispatch_tool",
            "tool_approval_gate": "tool_approval_gate",
            "finalize_decide": "finalize_decide_cheap",
            "finalize_budget_escalate": "finalize_budget_escalate",
        },
    )
    graph.add_edge("dispatch_tool", "decide_or_act")  # цикл ReAct
    graph.add_conditional_edges(
        "tool_approval_gate",
        tool_routing.route_after_approval_gate,
        {"dispatch_tool": "dispatch_tool", "finalize_escalate": "finalize_escalate"},
    )

    graph.add_conditional_edges(
        "finalize_decide_cheap",
        routing.make_route_after_cheap(settings),
        {
            "finalize_answer": "finalize_answer",
            "decide_escalated": "finalize_decide_escalated",  # тот же ключ, другой узел
            "finalize_budget_escalate": "finalize_budget_escalate",
        },
    )
    graph.add_conditional_edges(
        "finalize_decide_escalated",
        routing.make_route_after_escalated(settings),
        {
            "finalize_answer": "finalize_answer",
            "human_gate": "human_gate",
            "finalize_escalate": "finalize_escalate",
            "finalize_budget_escalate": "finalize_budget_escalate",
        },
    )
    graph.add_conditional_edges(
        "human_gate",
        routing.route_after_human_gate,
        {"finalize_answer": "finalize_answer", "finalize_escalate": "finalize_escalate"},
    )

    graph.add_edge("finalize_answer", END)
    graph.add_edge("finalize_escalate", END)
    graph.add_edge("finalize_budget_escalate", END)

    return graph.compile(checkpointer=checkpointer)

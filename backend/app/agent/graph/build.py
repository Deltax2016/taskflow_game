"""Сборка графа: State + Node + Edge + Reducers из лекции — здесь всё вместе.

    START ──► retrieve ──► decide_cheap ─┬─(уверен)────────────────────► finalize_answer ────────► END
                                          ├─(бюджет исчерпан)───────────► finalize_budget_escalate ► END
                                          └─(не уверен, бюджет ok)─► decide_escalated ─┬─(уверен)──────────────► finalize_answer ────────► END
                                                                                        ├─(средне)──► human_gate ─┬─(approve)─► finalize_answer ► END
                                                                                        │                         └─(reject)──► finalize_escalate ► END
                                                                                        ├─(низко)────────────────► finalize_escalate ──────► END
                                                                                        └─(бюджет исчерпан)──────► finalize_budget_escalate ► END

`finalize_escalate` и `finalize_budget_escalate` — сознательно РАЗНЫЕ узлы, а
не один с условием: к моменту, когда бюджет исчерпан, `reason` в состоянии
почти всегда уже заполнен последним decide-узлом (иногда с высокой
уверенностью — бюджет проверяется ПОСЛЕ decide, не до). Если бы оба случая
шли в один узел с правилом «reason пуст → подставить дефолт», оператор видел
бы обоснование LLM вместо настоящей причины эскалации. Разные узлы — разные,
безусловные тексты (см. `nodes.py`).

`RetryPolicy` на `decide_*`-узлах — decide-узлы сами по себе идемпотентны
(см. `nodes.py`), поэтому LangGraph может безопасно повторить узел целиком при
сетевой ошибке, не разрушая уже собранное состояние графа.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer, RetryPolicy

from app.agent.base import LLM, Retriever
from app.agent.graph import nodes, routing
from app.agent.graph.state import GraphState
from app.agent.prompts import SYSTEM_PROMPT_ESCALATED, SYSTEM_PROMPT_FULL
from app.config import Settings

# Сеть/провайдер шатает — это нормально, для того и RetryPolicy.
_LLM_RETRY_POLICY_KW = {"retry_on": nodes.is_transient_llm_error}


def build_graph(
    cheap_llm: LLM,
    escalated_llm: LLM,
    retriever: Retriever,
    settings: Settings,
    checkpointer: Checkpointer = None,
):
    """Собирает и компилирует граф. `checkpointer=None` — граф работает, но
    ничего не персистит (годится для тестов); в приложении передаём реальный
    (Postgres или in-memory — см. `checkpointer.py`).
    """
    retry_policy = RetryPolicy(
        max_attempts=settings.agent_retry_max_attempts,
        initial_interval=settings.agent_retry_initial_interval,
        **_LLM_RETRY_POLICY_KW,
    )
    # У ретривера тоже может быть сеть (QdrantRetriever) — более лёгкий policy.
    retrieve_retry_policy = RetryPolicy(max_attempts=2, retry_on=nodes.is_transient_llm_error)

    graph = StateGraph(GraphState)

    graph.add_node(
        "retrieve",
        nodes.make_retrieve_node(retriever, settings),
        retry_policy=retrieve_retry_policy,
    )
    graph.add_node(
        "decide_cheap",
        nodes.make_decide_node(cheap_llm, tier="cheap", system_prompt=SYSTEM_PROMPT_FULL),
        retry_policy=retry_policy,
    )
    graph.add_node(
        "decide_escalated",
        nodes.make_decide_node(escalated_llm, tier="escalated", system_prompt=SYSTEM_PROMPT_ESCALATED),
        retry_policy=retry_policy,
    )
    graph.add_node("human_gate", nodes.human_gate)
    graph.add_node("finalize_answer", nodes.finalize_answer)
    graph.add_node("finalize_escalate", nodes.finalize_escalate)
    graph.add_node("finalize_budget_escalate", nodes.finalize_budget_escalate)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "decide_cheap")

    graph.add_conditional_edges(
        "decide_cheap",
        routing.make_route_after_cheap(settings),
        {
            "finalize_answer": "finalize_answer",
            "decide_escalated": "decide_escalated",
            "finalize_budget_escalate": "finalize_budget_escalate",
        },
    )
    graph.add_conditional_edges(
        "decide_escalated",
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

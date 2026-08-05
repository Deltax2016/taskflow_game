"""Узлы ReAct-цикла: decide_or_act ⇄ dispatch_tool, плюс tool_approval_gate.

    retrieve → decide_or_act ─┬─(текст, инструменты не нужны)──────► finalize_decision
                               │
                               ├─(инструмент, approval не нужен)──► dispatch_tool ──┐
                               │                                                     │
                               ├─(инструмент, approval нужен)──► tool_approval_gate  │
                               │                                      │approve       │
                               │                                      └─────────────►│
                               │                                                     │
                               └─(бюджет/лимит вызовов исчерпан)──► finalize_budget_escalate
                                                                                     │
              (цикл — см. routing.route_after_decide) ◄─────────────────────────────┘

`decide_or_act` и `dispatch_tool` — ОБА идемпотентны в смысле занятия 2: ни
один не пишет в БД тикетов. `dispatch_tool` вызывает Python-функцию
инструмента, которая МОЖЕТ иметь побочный эффект вовне графа (запись в
`refunds`, исходящий HTTP) — но это не state тикета, и при честном повторе
(RetryPolicy на транзиентной сетевой ошибке) риск двойного побочного эффекта
несёт уже конкретный инструмент, не граф. Для `create_refund` это не проблема:
транзиентные сетевые ошибки у него в принципе не бывает (это чистая запись в
свою же БД, без внешнего вызова).
"""

from __future__ import annotations

import json

from app.agent.base import LLM
from app.agent.graph.nodes import NodeFn
from app.agent.graph.tool_state import ToolGraphState
from app.agent.prompts import build_user_prompt
from app.agent.schemas import ChatTurn, LLMDecision
from app.agent.tools.base import ToolError, ToolRegistry
from app.security.spotlighting import wrap_tool_result


def make_decide_or_act_node(llm: LLM, registry: ToolRegistry, *, system_prompt: str) -> NodeFn:
    """Один виток ReAct-цикла: модель либо просит вызвать инструмент, либо
    отвечает текстом (тогда цикл заканчивается — см. `routing.py`).
    """
    tool_schemas = registry.to_openai_schemas()

    async def decide_or_act(state: ToolGraphState) -> dict:
        history = [ChatTurn(**turn) for turn in state.get("history", [])]
        messages = [
            *history,
            ChatTurn(role="user", content=build_user_prompt(state["question"], state.get("context", ""))),
        ]
        decision = await llm.complete_with_tools(
            system_prompt, messages, tool_schemas, tool_transcript=state.get("tool_messages", [])
        )

        if decision.kind == "tool_call":
            assistant_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": decision.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": decision.tool_name,
                            "arguments": json.dumps(decision.tool_args, ensure_ascii=False),
                        },
                    }
                ],
            }
            return {
                "tool_messages": [assistant_msg],
                "pending_tool_name": decision.tool_name,
                "pending_tool_args": decision.tool_args,
                "pending_tool_call_id": decision.tool_call_id,
                "steps_used": 1,
            }

        # ВАЖНО: явно очищаем pending_tool_name. LangGraph мержит частичные
        # обновления state, а не заменяет весь state целиком — если не
        # сбросить это поле, оно останется от ПРЕДЫДУЩЕГО витка цикла, и
        # routing.py решит, что модель снова просит вызвать тот же инструмент
        # (см. `route_after_decide_or_act`: `if not tool_name: ...`).
        return {"draft_from_tools": decision.text, "steps_used": 1, "pending_tool_name": ""}

    return decide_or_act


def make_dispatch_tool_node(registry: ToolRegistry) -> NodeFn:
    """Реально вызывает инструмент, который запросила модель на прошлом витке.

    `_ticket_id` подставляет ГРАФ из своего состояния — не из того, что
    "попросила" модель (в JSON Schema инструментов такого поля нет вообще,
    см. `agent/tools/server_side.py`). Это и есть контроль доступа в коде,
    а не в промпте: даже успешная инъекция не может подменить, к какому
    тикету привязать действие.
    """

    async def dispatch_tool(state: ToolGraphState) -> dict:
        name = state["pending_tool_name"]
        args = dict(state.get("pending_tool_args") or {})
        call_id = state["pending_tool_call_id"]

        args["_ticket_id"] = state.get("ticket_id")
        if state.get("tool_call_approved"):
            args["_approved_by_human"] = True

        tool = registry.get(name)  # ToolError, если инструмента нет — не ретраится
        try:
            result_text = await tool.handler(args)
        except ToolError as exc:
            result_text = f"Ошибка инструмента: {exc}"

        wrapped = wrap_tool_result(name, result_text)
        return {
            "tool_messages": [{"role": "tool", "tool_call_id": call_id, "content": wrapped}],
            "tool_calls_used": 1,
            "steps_used": 1,
            "tool_call_approved": False,  # одноразовый флаг, сбрасываем сразу после использования
            # Второй слой защиты от того же бага, что и комментарий в
            # decide_or_act: явно очищаем, иначе `route_after_decide_or_act`
            # на следующем витке может принять СТАРЫЙ pending_tool_name за
            # новый запрос, если decide_or_act по какой-то причине не
            # перезапишет его первым (defense in depth и для состояния графа).
            "pending_tool_name": "",
        }

    return dispatch_tool


def tool_approval_gate(state: ToolGraphState) -> dict:
    """Human-in-the-Loop ДЛЯ ВЫЗОВА ИНСТРУМЕНТА, а не для черновика ответа
    (как `human_gate` в занятии 2, но тот же механизм `interrupt()`).

    Останавливает граф ПЕРЕД тем, как реально выполнится критическое
    действие (`create_refund` выше лимита). Оператор видит, какой инструмент
    и с какими аргументами хочет вызвать агент, и решает: одобрить (тогда
    `dispatch_tool` выполнит вызов с пометкой `_approved_by_human=True`) или
    отклонить (цикл прерывается эскалацией, инструмент не вызывается вовсе).
    """
    import time

    from langgraph.types import interrupt

    decision = interrupt(
        {
            "tool_name": state.get("pending_tool_name"),
            "tool_args": state.get("pending_tool_args"),
            "reason": "Требуется подтверждение для критического действия",
        }
    )

    if decision.get("approve"):
        # ВАЖНО: перезапускаем бюджет по времени. `started_at` не двигается
        # сам по себе, пока граф стоит на паузе, — а пауза здесь МОЖЕТ
        # длиться часами (оператор не обязан жать "одобрить" за 25 секунд).
        # Без сброса первая же проверка бюджета ПОСЛЕ резюме (в
        # route_after_decide_or_act/make_route_after_cheap) увидит время
        # ожидания оператора как "потраченное графом" и сразу эскалирует
        # по бюджету — сводя одобрение на нет. Бюджет должен мерить активную
        # работу агента, а не время ожидания человека.
        return {"tool_call_approved": True, "started_at": time.monotonic()}
    return {
        "action": "escalate",
        "reason": f"Оператор отклонил вызов инструмента {state.get('pending_tool_name')!r}.",
    }


def make_finalize_decision_node(llm: LLM, *, tier: str, system_prompt: str) -> NodeFn:
    """Финальная структурированная проверка уверенности — ПОСЛЕ того, как
    ReAct-цикл собрал все нужные факты (или решил, что инструменты не нужны).

    Тот же `LLMDecision`/`complete_structured`, что и в занятии 2
    (`graph/nodes.py: make_decide_node`) — насколько уверенно можно ответить
    пользователю, теперь уже видя результаты вызовов инструментов в
    `tool_messages`. Не дублируем логику валидации/эскалации на ошибку — она
    та же, что в занятии 2.
    """
    from pydantic import ValidationError

    async def finalize_decision(state: ToolGraphState) -> dict:
        history = [ChatTurn(**turn) for turn in state.get("history", [])]
        messages = [
            *history,
            ChatTurn(role="user", content=build_user_prompt(state["question"], state.get("context", ""))),
        ]
        # Модель должна видеть, какие инструменты вызывались и что они вернули,
        # прежде чем оценивать уверенность — иначе "почему уверенность высокая"
        # будет необъяснимо без этого контекста.
        tool_summary = _summarize_tool_transcript(state.get("tool_messages", []))
        if tool_summary:
            messages.append(ChatTurn(role="user", content=f"Результаты вызовов инструментов:\n{tool_summary}"))

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
            }

        return {
            "can_answer": decision.can_answer,
            "answer": decision.answer,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "model_tier": tier,
            "steps_used": 1,
        }

    return finalize_decision


def _summarize_tool_transcript(tool_messages: list[dict]) -> str:
    """Человекочитаемая выжимка transcript'а для финального решения — не
    пересказываем сырые OpenAI-структуры (роли assistant/tool), а только
    "что вызвали -> что получили".
    """
    lines: list[str] = []
    for msg in tool_messages:
        if msg.get("role") == "tool":
            lines.append(str(msg.get("content", "")))
    return "\n".join(lines)

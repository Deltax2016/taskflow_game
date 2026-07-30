"""Адаптер: компилированный LangGraph-граф ↔ интерфейс `Agent` (занятие 1).

Ключевой приём «программируем от интерфейсов» works here too:
`TicketService` вызывает `agent.handle(request)` и получает `AgentResult` —
ему всё равно, что внутри теперь целый граф состояний с ретраями, бюджетом
и Human-in-the-Loop, а не одна функция `handle()`, как в `support.py`. Раз
интерфейс `Agent` не менялся, `api/` не тронут вообще, `services/tickets.py`
тронут в одном осознанном месте — см. `AgentResult.meta` (agent/schemas.py)
и `TicketService.resume_agent_draft`.
"""

import time
from uuid import uuid4

from langgraph.types import Command

from app.agent.base import Agent
from app.agent.schemas import AgentAction, AgentRequest, AgentResult
from app.config import Settings


class LangGraphSupportAgent(Agent):
    def __init__(self, graph, settings: Settings) -> None:
        self._graph = graph
        self._settings = settings

    async def handle(self, request: AgentRequest) -> AgentResult:
        # Свой thread_id НА КАЖДОЕ сообщение, а не один на весь тикет.
        # Причина: steps_used/tier_attempts — Reducer'ы (operator.add,
        # см. graph/state.py), они СКЛАДЫВАЮТСЯ в рамках одного thread_id.
        # Если бы thread_id совпадал с ticket_id на все сообщения тикета,
        # бюджет копился бы по всей истории переписки и через несколько
        # сообщений агент бы всегда «упирался в бюджет» даже на простых
        # вопросах. thread_id должен жить ровно на один вопрос — этого как
        # раз достаточно, чтобы `human_gate`/`Command(resume=...)` нашли
        # точку остановки (см. `resume_draft` ниже).
        thread_id = f"ticket-{request.ticket_id}-{uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {
            "question": request.question,
            "history": [turn.model_dump() for turn in request.history],
            "started_at": time.monotonic(),
            "steps_used": 0,
            "tier_attempts": 0,
        }

        try:
            result = await self._graph.ainvoke(input_state, config=config)
        except Exception as exc:  # noqa: BLE001 — последний safety net, как и в v1/v2
            # Сюда долетают ДВЕ разные ситуации, и обе — безопасно эскалировать:
            # либо RetryPolicy исчерпал попытки на транзиентной ошибке (сеть),
            # либо ошибка вообще не подлежала повтору (см. is_transient_llm_error)
            # и потому пришла с первого же раза.
            return AgentResult(
                action=AgentAction.ESCALATE,
                confidence=0.0,
                reason=f"Агент не смог обработать запрос: {exc!s}",
                meta={"thread_id": thread_id},
            )

        if "__interrupt__" in result:
            # Граф остановился в human_gate — нужен человек, прежде чем
            # ответ уйдёт пользователю. Достаём payload, который передали
            # в interrupt(...) (см. nodes.human_gate).
            payload = result["__interrupt__"][0].value
            return AgentResult(
                action=AgentAction.ESCALATE,
                confidence=payload.get("confidence", 0.0),
                reason=payload.get("reason") or "Черновик готов, нужно подтверждение оператора.",
                sources=result.get("sources", []),
                meta={
                    "thread_id": thread_id,
                    "requires_approval": True,
                    "draft_answer": payload.get("draft_answer"),
                    "model_tier": result.get("model_tier"),
                    "steps_used": result.get("steps_used", 0),
                },
            )

        return self._to_result(result, thread_id)

    async def resume_draft(
        self, thread_id: str, *, approve: bool, edited_answer: str | None
    ) -> AgentResult:
        """Резюмирует граф, остановленный в `human_gate`.

        `Command(resume=...)` передаёт значение туда же, откуда `interrupt()`
        когда-то его "спросил" — граф продолжается с этой самой строки внутри
        `nodes.human_gate`, а не сначала.
        """
        config = {"configurable": {"thread_id": thread_id}}
        result = await self._graph.ainvoke(
            Command(resume={"approve": approve, "edited_answer": edited_answer}),
            config=config,
        )
        return self._to_result(result, thread_id, resumed=True)

    async def trace(self, thread_id: str) -> list[dict]:
        """Учебная замена LangGraph Studio/Langfuse (см. `base.Traceable`):
        список состояний графа для этого `thread_id`, от первого шага до
        последнего (или до точки остановки на `human_gate`). Не хранит
        отдельную историю — вся информация уже лежит в checkpointer'е,
        мы просто читаем её через `aget_state_history`.
        """
        config = {"configurable": {"thread_id": thread_id}}
        snapshots = [
            {
                "next_nodes": list(snap.next),
                "values": {
                    k: v for k, v in snap.values.items() if k not in ("history", "chunks", "context")
                },
                "step": (snap.metadata or {}).get("step"),
                "created_at": snap.created_at,
            }
            async for snap in self._graph.aget_state_history(config)
        ]
        snapshots.reverse()  # aget_state_history отдаёт от последнего к первому шагу
        return snapshots

    def _to_result(self, result: dict, thread_id: str, *, resumed: bool = False) -> AgentResult:
        meta = {
            "thread_id": thread_id,
            "model_tier": result.get("model_tier"),
            "steps_used": result.get("steps_used", 0),
            "tier_attempts": result.get("tier_attempts", 0),
        }
        if resumed:
            meta["resumed"] = True

        if result.get("action") == "answer":
            return AgentResult(
                action=AgentAction.ANSWER,
                answer=result.get("answer"),
                confidence=result.get("confidence", 0.0),
                reason=result.get("reason", ""),
                sources=result.get("sources", []),
                meta=meta,
            )
        return AgentResult(
            action=AgentAction.ESCALATE,
            confidence=result.get("confidence", 0.0),
            reason=result.get("reason") or "Недостаточно данных для ответа.",
            sources=result.get("sources", []),
            meta=meta,
        )

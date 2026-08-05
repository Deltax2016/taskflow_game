"""Адаптер: ReAct-граф с инструментами ↔ интерфейс `Agent` (занятие 1).

Тот же приём «программируем от интерфейсов», что и в занятиях 1-2:
`TicketService` вызывает `agent.handle(request)` и получает `AgentResult` —
не зная, что внутри теперь целый цикл вызовов инструментов с двумя видами
Human-in-the-Loop. `ToolAgent` реализует ДВА узких протокола сверх `Agent`:

  * `DraftResumable`   — унаследовано от занятия 2: если после цикла
    инструментов уверенность средняя, граф всё ещё может остановиться на
    `human_gate` с черновиком ответа (`resolve-draft`, как раньше).
  * `ToolCallApprovable` — НОВОЕ: если модель просит вызвать критический
    инструмент (`create_refund` выше лимита), граф останавливается на
    `tool_approval_gate` — оператор одобряет/отклоняет САМ ВЫЗОВ, до того,
    как инструмент выполнится.

Первый слой защиты — санитайзер (`app/security/sanitizer.py`) — работает
ДО того, как вопрос вообще попадёт в граф: если `decision == "BLOCK"` (и
`sanitizer_mode == "enforce"`), LLM не вызывается вовсе.
"""

import time
from uuid import uuid4

from langgraph.types import Command

from app.agent.base import Agent
from app.agent.schemas import AgentAction, AgentRequest, AgentResult
from app.config import Settings
from app.security.sanitizer import InputSanitizer


class ToolAgent(Agent):
    def __init__(self, graph, settings: Settings) -> None:
        self._graph = graph
        self._settings = settings
        self._sanitizer = InputSanitizer()

    async def handle(self, request: AgentRequest) -> AgentResult:
        sanitizer_result = self._sanitizer.check(request.question)
        sanitizer_meta = {
            "sanitizer_decision": sanitizer_result.decision,
            "sanitizer_risk": sanitizer_result.risk,
            "sanitizer_flags": sanitizer_result.flags,
        }

        if sanitizer_result.decision == "BLOCK" and self._settings.sanitizer_mode == "enforce":
            return AgentResult(
                action=AgentAction.ESCALATE,
                confidence=0.0,
                reason=f"Заблокировано на входе (sanitizer, до вызова LLM): {', '.join(sanitizer_result.flags)}",
                meta=sanitizer_meta,
            )

        thread_id = f"ticket-{request.ticket_id}-{uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {
            "ticket_id": request.ticket_id,
            "question": request.question,
            "history": [turn.model_dump() for turn in request.history],
            "started_at": time.monotonic(),
            "steps_used": 0,
            "tool_calls_used": 0,
        }

        try:
            result = await self._graph.ainvoke(input_state, config=config)
        except Exception as exc:  # noqa: BLE001 — последний safety net, как в занятиях 1-2
            return AgentResult(
                action=AgentAction.ESCALATE,
                confidence=0.0,
                reason=f"Агент не смог обработать запрос (после повторов RetryPolicy): {exc!s}",
                meta={**sanitizer_meta, "thread_id": thread_id},
            )

        return self._handle_result(result, thread_id, sanitizer_meta)

    async def resume_draft(
        self, thread_id: str, *, approve: bool, edited_answer: str | None
    ) -> AgentResult:
        """Резюмирует граф, остановленный на `human_gate` (черновик ответа) —
        тот же узел и тот же механизм, что и в занятии 2.
        """
        config = {"configurable": {"thread_id": thread_id}}
        result = await self._graph.ainvoke(
            Command(resume={"approve": approve, "edited_answer": edited_answer}), config=config
        )
        return self._handle_result(result, thread_id, {}, resumed=True)

    async def resume_tool_approval(self, thread_id: str, *, approve: bool) -> AgentResult:
        """Резюмирует граф, остановленный на `tool_approval_gate` (вызов
        критического инструмента). В отличие от `resume_draft` — нечего
        редактировать, только одобрить/отклонить сам вызов с уже
        зафиксированными аргументами.
        """
        config = {"configurable": {"thread_id": thread_id}}
        result = await self._graph.ainvoke(Command(resume={"approve": approve}), config=config)
        return self._handle_result(result, thread_id, {}, resumed=True)

    def _handle_result(
        self, result: dict, thread_id: str, extra_meta: dict, *, resumed: bool = False
    ) -> AgentResult:
        """Общая точка выхода для `handle`/`resume_draft`/`resume_tool_approval`:
        либо граф остановился на ОДНОМ ИЗ ДВУХ interrupt'ов (может случиться и
        после резюме — например, одобрили один возврат, а цикл дошёл до
        второго критического вызова), либо реально завершился.
        """
        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            meta = {
                **extra_meta,
                "thread_id": thread_id,
                "steps_used": result.get("steps_used", 0),
            }
            if resumed:
                meta["resumed"] = True

            if "tool_name" in payload:
                meta.update(
                    {
                        "requires_tool_approval": True,
                        "pending_tool_name": payload.get("tool_name"),
                        "pending_tool_args": payload.get("tool_args"),
                    }
                )
                reason = f"Требуется подтверждение оператора для вызова инструмента «{payload.get('tool_name')}»."
            else:
                meta.update(
                    {
                        "requires_approval": True,
                        "draft_answer": payload.get("draft_answer"),
                        "model_tier": result.get("model_tier"),
                    }
                )
                reason = payload.get("reason") or "Черновик готов, нужно подтверждение оператора."

            return AgentResult(
                action=AgentAction.ESCALATE,
                confidence=payload.get("confidence", 0.0),
                reason=reason,
                sources=result.get("sources", []),
                meta=meta,
            )

        meta = {
            **extra_meta,
            "thread_id": thread_id,
            "model_tier": result.get("model_tier"),
            "steps_used": result.get("steps_used", 0),
            "tool_calls_used": result.get("tool_calls_used", 0),
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

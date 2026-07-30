"""Вариант 2: полноценный агент, который решает сам.

Отличие от простого пайплайна: после обращения в RAG агент через LLM честно
оценивает, ХВАТАЕТ ли ему контекста, чтобы ответить. Если нет — сам инициирует
эскалацию на человека. Ответ LLM приходит в СТРУКТУРИРОВАННОМ виде (LLMDecision)
и валидируется — «сырую строку» мы наружу не отдаём.

Схема ветвления:
    вопрос ──► RAG ──► LLM(structured decision)
                              │
                    can_answer? ├── да ─► ANSWER (с ответом и уверенностью)
                              └── нет ─► ESCALATE (передаём человеку)
    (любая ошибка LLM/валидации) ─────► ESCALATE (безопасный запасной путь)
"""

from pydantic import ValidationError

from app.agent.base import LLM, Agent, Retriever
from app.agent.prompts import SYSTEM_PROMPT_FULL, build_context, build_user_prompt
from app.agent.schemas import (
    AgentAction,
    AgentRequest,
    AgentResult,
    ChatTurn,
    LLMDecision,
)
from app.config import Settings


class SupportAgent(Agent):
    def __init__(self, llm: LLM, retriever: Retriever, settings: Settings) -> None:
        self._llm = llm
        self._retriever = retriever
        self._settings = settings

    async def handle(self, request: AgentRequest) -> AgentResult:
        # 1. Агент сам идёт в RAG за контекстом.
        chunks = await self._retriever.search(request.question, self._settings.rag_top_k)
        context = build_context(chunks)
        sources = sorted({c.source for c in chunks})

        # 2. Просим LLM принять СТРУКТУРИРОВАННОЕ решение по контексту.
        messages = [
            *[ChatTurn(role=t.role, content=t.content) for t in request.history],
            ChatTurn(role="user", content=build_user_prompt(request.question, context)),
        ]
        try:
            decision = await self._llm.complete_structured(
                SYSTEM_PROMPT_FULL, messages, LLMDecision
            )
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            # LLM недоступна / вернула мусор / не прошла валидацию — не рискуем,
            # отдаём человеку. Это и есть «безопасный запасной путь».
            return AgentResult(
                action=AgentAction.ESCALATE,
                reason=f"Не удалось получить корректный ответ от LLM: {exc!s}",
                confidence=0.0,
                sources=sources,
            )

        # 3. Агент сам решил, что контекста мало → эскалация.
        if not decision.can_answer:
            return AgentResult(
                action=AgentAction.ESCALATE,
                reason=decision.reason or "Недостаточно данных для ответа.",
                confidence=decision.confidence,
                sources=sources,
            )

        # 4. Агент готов ответить. Финальный порог уверенности применит оркестратор
        #    (единое место для этого правила) — здесь просто возвращаем решение.
        return AgentResult(
            action=AgentAction.ANSWER,
            answer=decision.answer,
            confidence=decision.confidence,
            reason=decision.reason,
            sources=sources,
        )

"""Вариант 1: простой пайплайн RAG + LLM.

Логика прямолинейная: нашли контекст → скормили в LLM → вернули ответ.
Агент здесь НЕ рассуждает о своей компетентности: он эскалирует только если
база знаний совсем ничего не нашла. Насколько ответу можно доверять —
решает уже оркестратор по порогу уверенности.

Это «базовый» способ. Ниже, в support.py, — полноценный агент. Оба реализуют
один интерфейс `Agent`, поэтому взаимозаменяемы (см. factory.py).
"""

from app.agent.base import LLM, Agent, Retriever
from app.agent.prompts import SYSTEM_PROMPT_SIMPLE, build_context, build_user_prompt
from app.agent.schemas import AgentAction, AgentRequest, AgentResult, ChatTurn
from app.config import Settings


class SimpleRagAgent(Agent):
    def __init__(self, llm: LLM, retriever: Retriever, settings: Settings) -> None:
        self._llm = llm
        self._retriever = retriever
        self._settings = settings

    async def handle(self, request: AgentRequest) -> AgentResult:
        # 1. Ищем знания в RAG (это делает сам агент).
        chunks = await self._retriever.search(request.question, self._settings.rag_top_k)

        # 2. Нет контекста — отвечать не из чего, передаём человеку.
        if not chunks:
            return AgentResult(
                action=AgentAction.ESCALATE,
                reason="База знаний ничего не нашла по запросу.",
                confidence=0.0,
            )

        # 3. Собираем промпт (контекст + вопрос) и просим LLM ответить.
        context = build_context(chunks)
        messages = [
            *[ChatTurn(role=t.role, content=t.content) for t in request.history],
            ChatTurn(role="user", content=build_user_prompt(request.question, context)),
        ]
        answer = await self._llm.complete(SYSTEM_PROMPT_SIMPLE, messages)

        # 4. Грубая эвристика уверенности по релевантности верхнего фрагмента.
        #    Здесь агент не умеет судить сам себя — в этом и слабость подхода.
        top_score = chunks[0].score
        confidence = top_score / (top_score + 2.0)  # плавно приводим к диапазону (0, 1)

        return AgentResult(
            action=AgentAction.ANSWER,
            answer=answer,
            confidence=round(confidence, 3),
            reason="Ответ сгенерирован по найденному контексту.",
            sources=sorted({c.source for c in chunks}),
        )

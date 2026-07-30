"""Пакет агента.

Публичный API слоя — интерфейс `Agent`, фабрика `build_agent` и доменные схемы.
Остальной код импортирует отсюда, не заглядывая в конкретные реализации.
"""

from app.agent.base import LLM, Agent, Retriever
from app.agent.factory import build_agent
from app.agent.schemas import AgentAction, AgentRequest, AgentResult, ChatTurn

__all__ = [
    "Agent",
    "LLM",
    "Retriever",
    "build_agent",
    "AgentAction",
    "AgentRequest",
    "AgentResult",
    "ChatTurn",
]

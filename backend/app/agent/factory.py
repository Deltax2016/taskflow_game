"""Фабрика агента: собирает нужную реализацию из настроек.

Единственное место, где мы «знаем» про конкретные классы. Весь остальной код
работает с интерфейсом `Agent`. Хотите другой агент/ретривер — меняете .env
(AGENT_TYPE, RETRIEVER_TYPE), а не код приложения.
"""

from langgraph.types import Checkpointer

from app.agent.base import LLM, Agent, Retriever
from app.agent.graph import build_graph
from app.agent.graph.build_tool_graph import build_tool_graph
from app.agent.langgraph_agent import LangGraphSupportAgent
from app.agent.llm import OpenRouterLLM
from app.agent.rag import LocalRetriever, QdrantRetriever
from app.agent.simple import SimpleRagAgent
from app.agent.support import SupportAgent
from app.agent.tool_agent import ToolAgent
from app.agent.tools import build_tool_registry
from app.config import Settings, get_settings


def build_retriever(settings: Settings) -> Retriever:
    if settings.retriever_type == "qdrant":
        return QdrantRetriever(settings)
    return LocalRetriever(settings)


def build_llm(settings: Settings) -> LLM:
    # Пока провайдер один. Здесь же можно вернуть локальную LLM (Ollama) или мок.
    return OpenRouterLLM(settings)


def build_escalated_llm(settings: Settings) -> LLM:
    """LLM для второго (escalated) тира графа LangGraph.

    Своя, отдельная `Settings`-копия — интерфейс `LLM.complete_structured` не
    принимает `model=`/`temperature=` (см. agent/graph/nodes.py), поэтому
    вместо параметров на каждый вызов собираем ВТОРОЙ настроенный клиент.
    По умолчанию — та же модель, но temperature=0 (детерминированный,
    осторожный проход); впишите AGENT_ESCALATION_MODEL в .env для настоящей
    эскалации на более сильную модель.
    """
    escalated_settings = settings.model_copy(
        update={
            "llm_model": settings.agent_escalation_model or settings.llm_model,
            "llm_temperature": 0.0,
        }
    )
    return OpenRouterLLM(escalated_settings)


def build_agent(settings: Settings | None = None, checkpointer: Checkpointer = None) -> Agent:
    settings = settings or get_settings()
    llm = build_llm(settings)
    retriever = build_retriever(settings)

    if settings.agent_type == "simple":
        return SimpleRagAgent(llm, retriever, settings)
    if settings.agent_type == "full":
        return SupportAgent(llm, retriever, settings)
    if settings.agent_type == "langgraph":
        escalated_llm = build_escalated_llm(settings)
        graph = build_graph(llm, escalated_llm, retriever, settings, checkpointer)
        return LangGraphSupportAgent(graph, settings)
    if settings.agent_type == "tooluse":
        escalated_llm = build_escalated_llm(settings)
        registry = build_tool_registry(settings)
        graph = build_tool_graph(llm, escalated_llm, retriever, registry, settings, checkpointer)
        return ToolAgent(graph, settings)

    raise ValueError(f"Unsupported agent_type: {settings.agent_type!r}")

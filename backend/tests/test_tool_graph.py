"""Интеграционные тесты ReAct-графа (build_tool_graph) со скриптованным LLM.

Не ходят в реальную сеть/БД — фейковые инструменты и фейковый LLM позволяют
проверить именно ЛОГИКУ ГРАФА (цикл, бюджет, HITL для критических вызовов) в
изоляции. Реальные 10 инструментов против настоящих API проверяются отдельно
(см. `tests/test_real_tools_smoke.py`, помечен `@pytest.mark.network`).
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.base import LLM
from app.agent.graph.build_tool_graph import build_tool_graph
from app.agent.rag import LocalRetriever
from app.agent.schemas import AgentAction, AgentRequest, LLMDecision, ToolCallDecision
from app.agent.tool_agent import ToolAgent
from app.agent.tools.base import ToolError, ToolRegistry, ToolSpec
from app.config import Settings


class ScriptedToolLLM(LLM):
    """Отдаёт заранее заданные решения по очереди: `tool_decisions` — для
    `complete_with_tools` (цикл сбора фактов), `structured_decisions` — для
    `complete_structured` (финальная проверка уверенности).
    """

    def __init__(self, tool_decisions=None, structured_decisions=None):
        self._tool_decisions = list(tool_decisions or [])
        self._structured_decisions = list(structured_decisions or [])

    async def complete(self, system, messages):
        raise NotImplementedError

    async def complete_structured(self, system, messages, schema):
        return self._structured_decisions.pop(0)

    async def complete_with_tools(self, system, messages, tools, *, tool_transcript=None):
        return self._tool_decisions.pop(0)


class FakeToolCallCounter:
    """Считает реальные вызовы обработчика — доказывает, что отклонённый
    оператором вызов НИКОГДА не выполняется."""

    def __init__(self):
        self.calls: list[dict] = []

    async def handler(self, args: dict) -> str:
        self.calls.append(dict(args))
        return f"executed with {args}"


def make_settings(**overrides) -> Settings:
    base = dict(
        openrouter_api_key="test",
        knowledge_base_dir="knowledge_base",
        rag_top_k=3,
        min_confidence=0.6,
        human_approval_threshold=0.35,
        agent_max_steps=10,
        agent_max_seconds=25.0,
        agent_retry_max_attempts=2,
        agent_retry_initial_interval=0.01,
        tool_max_calls=4,
        refund_auto_limit=1000.0,
        sanitizer_mode="enforce",
    )
    base.update(overrides)
    return Settings(**base)


def build_fake_registry(settings: Settings, counter: FakeToolCallCounter) -> ToolRegistry:
    async def lookup_something(args: dict) -> str:
        return "праздник: нет, обычный день"

    async def unreliable(args: dict) -> str:
        raise ToolError("Инструмент недоступен (симуляция)")

    return ToolRegistry(
        [
            ToolSpec(
                name="check_public_holiday",
                description="test",
                parameters={"type": "object", "properties": {}},
                handler=lookup_something,
                category="public_api",
            ),
            ToolSpec(
                name="broken_tool",
                description="test",
                parameters={"type": "object", "properties": {}},
                handler=unreliable,
                category="public_api",
            ),
            ToolSpec(
                name="create_refund",
                description="test",
                parameters={"type": "object", "properties": {}},
                handler=counter.handler,
                category="server_side",
                requires_approval=lambda args: float(args.get("amount", 0) or 0) > settings.refund_auto_limit,
            ),
        ]
    )


async def run_agent(*, tool_decisions, structured_decisions, settings=None):
    settings = settings or make_settings()
    retriever = LocalRetriever(settings)
    counter = FakeToolCallCounter()
    registry = build_fake_registry(settings, counter)
    llm = ScriptedToolLLM(tool_decisions=tool_decisions, structured_decisions=structured_decisions)
    checkpointer = InMemorySaver()
    graph = build_tool_graph(llm, llm, retriever, registry, settings, checkpointer)
    agent = ToolAgent(graph, settings)

    request = AgentRequest(ticket_id=1, question="Тестовый вопрос", history=[])
    result = await agent.handle(request)
    return agent, result, counter


@pytest.mark.asyncio
async def test_text_answer_without_tools():
    _agent, result, counter = await run_agent(
        tool_decisions=[ToolCallDecision(kind="text", text="просто ответ")],
        structured_decisions=[LLMDecision(can_answer=True, answer="Ответ.", confidence=0.9, reason="есть в контексте")],
    )
    assert result.action == AgentAction.ANSWER
    assert result.confidence == 0.9
    assert not counter.calls


@pytest.mark.asyncio
async def test_one_tool_call_then_answer():
    _agent, result, _counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="check_public_holiday", tool_args={}, tool_call_id="call_1"),
            ToolCallDecision(kind="text", text="сегодня не праздник, вот ответ"),
        ],
        structured_decisions=[
            LLMDecision(can_answer=True, answer="Сегодня рабочий день.", confidence=0.85, reason="проверено инструментом")
        ],
    )
    assert result.action == AgentAction.ANSWER
    assert result.meta["tool_calls_used"] == 1


@pytest.mark.asyncio
async def test_refund_below_limit_executes_immediately():
    _agent, result, counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="create_refund", tool_args={"amount": 500, "reason": "test"}, tool_call_id="call_2"),
            ToolCallDecision(kind="text", text="возврат оформлен"),
        ],
        structured_decisions=[LLMDecision(can_answer=True, answer="Возврат оформлен.", confidence=0.9, reason="ок")],
    )
    assert result.action == AgentAction.ANSWER
    assert len(counter.calls) == 1
    assert counter.calls[0]["amount"] == 500
    assert "_approved_by_human" not in counter.calls[0]


@pytest.mark.asyncio
async def test_refund_above_limit_requires_approval_then_executes():
    agent, result, counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="create_refund", tool_args={"amount": 5000, "reason": "жалоба"}, tool_call_id="call_3"),
            ToolCallDecision(kind="text", text="возврат оформлен после одобрения"),
        ],
        structured_decisions=[LLMDecision(can_answer=True, answer="Возврат 5000 оформлен.", confidence=0.9, reason="ок")],
    )
    assert result.meta.get("requires_tool_approval") is True
    assert result.meta["pending_tool_name"] == "create_refund"
    assert not counter.calls, "до одобрения инструмент не должен вызываться"

    resumed = await agent.resume_tool_approval(result.meta["thread_id"], approve=True)
    assert resumed.action == AgentAction.ANSWER
    assert len(counter.calls) == 1
    assert counter.calls[0]["_approved_by_human"] is True


@pytest.mark.asyncio
async def test_refund_above_limit_rejected_never_executes():
    agent, result, counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="create_refund", tool_args={"amount": 9000, "reason": "подозрительно"}, tool_call_id="call_4"),
        ],
        structured_decisions=[],
    )
    assert result.meta.get("requires_tool_approval") is True

    resumed = await agent.resume_tool_approval(result.meta["thread_id"], approve=False)
    assert resumed.action == AgentAction.ESCALATE
    assert not counter.calls, "отклонённый вызов НЕ должен был выполниться"


@pytest.mark.asyncio
async def test_tool_error_is_recovered_gracefully():
    _agent, result, _counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="broken_tool", tool_args={}, tool_call_id="call_5"),
            ToolCallDecision(kind="text", text="инструмент недоступен, отвечаю без него"),
        ],
        structured_decisions=[LLMDecision(can_answer=False, answer="", confidence=0.2, reason="инструмент недоступен")],
    )
    # Граф не упал на ToolError — дошёл до честной структурированной эскалации.
    assert result.action == AgentAction.ESCALATE


@pytest.mark.asyncio
async def test_tool_call_budget_exhausted():
    tight_settings = make_settings(tool_max_calls=1)
    _agent, result, _counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="check_public_holiday", tool_args={}, tool_call_id="call_6"),
            ToolCallDecision(kind="tool_call", tool_name="check_public_holiday", tool_args={}, tool_call_id="call_7"),
        ],
        structured_decisions=[],
        settings=tight_settings,
    )
    assert result.action == AgentAction.ESCALATE
    assert "бюджет" in result.reason.lower()


@pytest.mark.asyncio
async def test_unknown_tool_name_is_handled_gracefully():
    """Модель (или инъекция) просит инструмент, которого нет в реестре —
    граф не падает, а честно возвращает ошибку в transcript."""
    _agent, result, _counter = await run_agent(
        tool_decisions=[
            ToolCallDecision(kind="tool_call", tool_name="delete_all_users", tool_args={}, tool_call_id="call_8"),
            ToolCallDecision(kind="text", text="такого инструмента нет, отвечаю иначе"),
        ],
        structured_decisions=[LLMDecision(can_answer=False, answer="", confidence=0.1, reason="запрошенный инструмент не существует")],
    )
    assert result.action == AgentAction.ESCALATE

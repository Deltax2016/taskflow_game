"""Слой инструментов (tools) — 10 штук: 5 публичных API + 5 серверных.

Публичный интерфейс пакета: `ToolSpec`/`ToolError`/`ToolRegistry` (контракт) и
`build_tool_registry` (сборка по настройкам). Граф (`agent/graph/tool_nodes.py`)
работает только с `ToolRegistry` — какие конкретно 10 инструментов внутри,
ему знать не нужно.
"""

from app.agent.tools.base import ToolError, ToolRegistry, ToolSpec
from app.agent.tools.registry import build_tool_registry

__all__ = ["ToolError", "ToolRegistry", "ToolSpec", "build_tool_registry"]

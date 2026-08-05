"""Собирает единый реестр из публичных API и серверных инструментов.

Одна точка правды: и граф (какие схемы показать LLM), и `dispatch_tool`
(как реально вызвать) читают отсюда. Добавить 11-й инструмент — значит
дописать его в `public_api.py`/`server_side.py` и включить в список ниже,
без изменений в графе.
"""

from app.agent.tools.base import ToolRegistry
from app.agent.tools.game import build_game_tools
from app.agent.tools.public_api import build_public_api_tools
from app.agent.tools.server_side import build_server_side_tools
from app.config import Settings


def build_tool_registry(settings: Settings) -> ToolRegistry:
    tools = [*build_public_api_tools(settings), *build_server_side_tools(settings)]
    if settings.game_mode:
        # 11-й инструмент только в игровом режиме: чтение приложенных файлов
        # осмысленно там, где файлы вообще можно приложить (см. tools/game.py).
        tools.extend(build_game_tools(settings))
    return ToolRegistry(tools)


__all__ = ["build_tool_registry"]

"""Слой защиты входа — независим от слоя агента.

`sanitizer.py` — детерминированный фильтр (без LLM, поэтому тестируемый как
обычная функция). `spotlighting.py` — разметка недоверенных данных в промпте.
Оба нужны ДО того, как агент вообще получит право звать инструменты — см.
`app/agent/tool_agent.py`.
"""

from app.security.sanitizer import InputSanitizer, SanitizerResult
from app.security.spotlighting import wrap_tool_result, wrap_user_message

__all__ = [
    "InputSanitizer",
    "SanitizerResult",
    "wrap_user_message",
    "wrap_tool_result",
]

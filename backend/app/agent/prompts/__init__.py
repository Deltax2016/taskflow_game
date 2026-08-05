"""Слой промптов.

Промпты — это тоже код, но с особым жизненным циклом: их часто правят, тюнят и
версионируют отдельно от логики. Поэтому держим их в изолированном слое, а не
размазываем строками по агенту.
"""

from app.agent.prompts.support import (
    SYSTEM_PROMPT_ESCALATED,
    SYSTEM_PROMPT_FULL,
    SYSTEM_PROMPT_SIMPLE,
    SYSTEM_PROMPT_TOOLS,
    build_context,
    build_user_prompt,
)

__all__ = [
    "SYSTEM_PROMPT_ESCALATED",
    "SYSTEM_PROMPT_FULL",
    "SYSTEM_PROMPT_SIMPLE",
    "SYSTEM_PROMPT_TOOLS",
    "build_context",
    "build_user_prompt",
]

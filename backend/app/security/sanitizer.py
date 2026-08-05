"""Детерминированный фильтр входа (defense-in-depth, слой 1 из 4).

Никакого LLM. Обычный код: нормализация → детект сигнатур → решение. Поэтому
он быстрый, дешёвый и — главное — тестируемый как обычная функция (см.
tests/test_sanitizer.py), в отличие от промпта, поведение которого нельзя
зафиксировать юнит-тестом.

Это НЕ полное решение — адаптивные атаки (перефразированные, на других
языках, многошаговые) обходят эвристики. Это ОДИН слой, который снимает
очевидное на входе, прежде чем вопрос вообще попадёт в граф агента. Остальные
слои — `spotlighting.py` (промпт), `agent/tools/base.py` (least-privilege в
самом коде инструмента) и `human_gate`/`tool_approval_gate` (HITL).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MAX_LEN = 2000  # клиентское сообщение длиннее — уже подозрительно

# Гомоглифы: часто используют кириллицу вместо латиницы, чтобы обойти фильтры
# по английским словам ("іgnоrе" визуально похоже на "ignore").
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
        "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X",
        "і": "i", "ѕ": "s", "ԁ": "d",
    }
)

# Невидимые/управляющие символы (zero-width и пр.) — частый приём обфускации.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿\x00-\x08\x0b\x0c\x0e-\x1f]")

# Сигнатуры инъекций: (флаг, вес, regex по нормализованному тексту).
_SIGNATURES = [
    ("instruction_override", 3, re.compile(r"\b(ignore|disregard|forget)\b.{0,30}\b(instruction|prompt|rule|above|previous)", re.I)),
    ("instruction_override", 3, re.compile(r"игнорир\w*.{0,30}(инструкц|правил|выше|предыдущ)", re.I)),
    ("role_override", 3, re.compile(r"(you are now|ты (теперь|в режиме)|режим\w* админ|act as|притвор\w*|developer mode|\bDAN\b)", re.I)),
    ("prompt_leak", 2, re.compile(r"(system prompt|систем\w* промпт|повтори .{0,20}(инструкц|промпт)|repeat .{0,20}(prompt|instruction))", re.I)),
    ("refusal_suppression", 2, re.compile(r"(do not refuse|не отказыв\w*|без ограничений|no restrictions)", re.I)),
    ("obfuscation_base64", 2, re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")),
    ("payload_splitting", 2, re.compile(r"(собери|concatenate|join).{0,20}(букв|part|piece|char)", re.I)),
    ("tool_coercion", 3, re.compile(r"(вызови|call)\s+(инструмент|tool)\s*\w*\s*(с параметр|with argument|напрямую|directly)", re.I)),
]

# Командные глаголы «действия» — опасны рядом с крупной суммой (звонок к
# инструменту create_refund с чужого голоса — самый частый сценарий на занятии).
_ACTION = re.compile(r"(оформ\w*|сдела\w*|переведи|верни|issue|refund|transfer|отправ\w*)", re.I)
_AMOUNT = re.compile(r"(\d[\d\s.,]{2,})\s*(?:₽|руб|rub|\$|usd|eur|€)?", re.I)
_BIG_AMOUNT = 1000  # порог «крупной» суммы — эвристика самого фильтра, НЕ лимит инструмента


@dataclass
class SanitizerResult:
    original: str
    cleaned: str
    risk: str  # LOW | MEDIUM | HIGH
    decision: str  # PASS | FLAG | BLOCK
    flags: list[str] = field(default_factory=list)
    score: int = 0

    def __repr__(self) -> str:
        return f"SanitizerResult(risk={self.risk!r}, decision={self.decision!r}, flags={self.flags}, score={self.score})"


def _normalize(text: str) -> str:
    """NFKC + удаление невидимых символов + схлопывание пробелов.

    Гомоглифы здесь НЕ сворачиваем — иначе сломаем настоящую кириллицу
    («Игнорируй» превратится в «Игнoпиpyй»). Свёртка — отдельным шагом (`_fold`).
    """
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _fold(text: str) -> str:
    """Свёртка кириллических гомоглифов в латиницу — чтобы поймать латинские
    команды, замаскированные кириллицей (напр. «іgnоrе» → «ignore»)."""
    return text.translate(_HOMOGLYPHS)


def _max_amount(text: str) -> int:
    best = 0
    for m in _AMOUNT.finditer(text):
        digits = re.sub(r"[^\d]", "", m.group(1))
        if digits:
            best = max(best, int(digits))
    return best


class InputSanitizer:
    """Прогоняет входящее сообщение пользователя через сигнатуры и эвристики."""

    def check(self, text: str) -> SanitizerResult:
        cleaned = _normalize(text)
        folded = _fold(cleaned)
        flags: list[str] = []
        score = 0

        if len(text) > MAX_LEN:
            flags.append(f"too_long:{len(text)}")
            score += 1

        for flag, weight, rx in _SIGNATURES:
            if (rx.search(cleaned) or rx.search(folded)) and flag not in flags:
                flags.append(flag)
                score += weight

        amount = _max_amount(cleaned)
        if _ACTION.search(cleaned) and amount >= _BIG_AMOUNT:
            flags.append("action_with_amount")
            flags.append(f"amount:{amount}")
            score += 3

        if score >= 3:
            risk, decision = "HIGH", "BLOCK"
        elif score >= 1:
            risk, decision = "MEDIUM", "FLAG"
        else:
            risk, decision = "LOW", "PASS"

        return SanitizerResult(original=text, cleaned=cleaned, risk=risk, decision=decision, flags=flags, score=score)

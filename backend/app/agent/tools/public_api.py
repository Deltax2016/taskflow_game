"""5 инструментов на реальных публичных API — без ключей, без регистрации.

Каждый обработчик:
  * ходит по сети через `httpx` с явным таймаутом (см. `Settings.tool_http_timeout_seconds`);
  * НЕ ловит `httpx.HTTPStatusError`/`TimeoutException` сам — пусть всплывает
    из узла `dispatch_tool`, где `RetryPolicy` (см. `agent/resilience.py`)
    решит, стоит ли повторить попытку;
  * для остальных ошибок (невалидные аргументы, пустой ответ) бросает
    `ToolError(...)` — повторять бессмысленно.

Токены не нужны — это сознательный выбор для воркшопа: демо работает у
любого участника без раздачи ключей и без риска забыть один из них в .env.
В проде на публичных API почти всегда есть free-tier лимиты (см.
`lookup_ip_region` — 45 запросов/мин у ip-api.com) — это тоже часть лекции
про rate limits, а не только про то, где взять данные.
"""

from __future__ import annotations

import httpx

from app.agent.tools.base import ToolError, ToolSpec
from app.config import Settings

# --- Статуспейджи известных провайдеров интеграций — БЕЛЫЙ СПИСОК в коде,
# не свободный URL от модели. Это и есть "least-privilege в коде инструмента,
# не в промпте": даже если промпт убедят вызвать инструмент с любым URL,
# сигнатура функции просто не принимает произвольный url — только имя из
# списка ниже. Если понадобится новый провайдер — его явно добавляет
# разработчик, а не решает LLM в рантайме.
_PROVIDER_STATUS_URLS = {
    "github": "https://www.githubstatus.com/api/v2/status.json",
    "slack": "https://slack-status.com/api/v2.0.0/current",
    "stripe": "https://www.stripestatus.com/api/v2/status.json",
    "openai": "https://status.openai.com/api/v2/status.json",
}


def _client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.tool_http_timeout_seconds)


def build_public_api_tools(settings: Settings) -> list[ToolSpec]:
    async def lookup_exchange_rate(args: dict) -> str:
        base = str(args.get("from_currency", "")).upper().strip()
        target = str(args.get("to_currency", "")).upper().strip()
        amount = args.get("amount", 1)
        if not base or not target:
            raise ToolError("Нужны from_currency и to_currency (коды валют, напр. RUB, USD)")

        async with _client(settings) as client:
            resp = await client.get(f"https://open.er-api.com/v6/latest/{base}")
            resp.raise_for_status()
        data = resp.json()
        if data.get("result") != "success":
            raise ToolError(f"Валюта {base!r} не распознана провайдером курсов")
        rates = data["rates"]
        if target not in rates:
            raise ToolError(f"Валюта {target!r} не найдена среди курсов")
        rate = rates[target]
        converted = round(float(amount) * rate, 2)
        return f"{amount} {base} = {converted} {target} (курс {base}->{target}: {rate}, источник: exchangerate-api.com, обновление: {data.get('time_last_update_utc', '?')})"

    async def lookup_ip_region(args: dict) -> str:
        ip = str(args.get("ip", "")).strip()
        if not ip:
            raise ToolError("Нужен ip")
        async with _client(settings) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}")
            resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise ToolError(f"Не удалось определить регион для {ip!r}: {data.get('message', '?')}")
        return (
            f"IP {ip}: страна={data.get('country')} ({data.get('countryCode')}), "
            f"регион={data.get('regionName')}, город={data.get('city')}, "
            f"часовой пояс={data.get('timezone')}"
        )

    async def check_public_holiday(args: dict) -> str:
        country = str(args.get("country_code", "")).upper().strip()
        date = str(args.get("date", "")).strip()
        if not country or not date:
            raise ToolError("Нужны country_code (ISO-2, напр. RU) и date (YYYY-MM-DD)")
        year = date.split("-")[0]
        async with _client(settings) as client:
            resp = await client.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}")
            resp.raise_for_status()
        holidays = resp.json()
        match = next((h for h in holidays if h["date"] == date), None)
        if match:
            return f"{date} в стране {country} — праздничный день: «{match['localName']}» ({match['name']})"
        return f"{date} в стране {country} — обычный рабочий день (по данным nager.at)"

    async def verify_email_domain(args: dict) -> str:
        email = str(args.get("email", "")).strip()
        if "@" not in email:
            raise ToolError("Похоже, это не email (нет @)")
        domain = email.rsplit("@", 1)[-1]
        async with _client(settings) as client:
            resp = await client.get("https://dns.google/resolve", params={"name": domain, "type": "MX"})
            resp.raise_for_status()
        data = resp.json()
        answers = data.get("Answer", [])
        if answers:
            return f"Домен {domain!r} принимает почту — найдено {len(answers)} MX-записей. Опечатка в домене маловероятна."
        return f"У домена {domain!r} НЕТ MX-записей — домен, скорее всего, не может принимать почту (опечатка в адресе?)."

    async def check_integration_provider_status(args: dict) -> str:
        provider = str(args.get("provider", "")).lower().strip()
        if provider not in _PROVIDER_STATUS_URLS:
            known = ", ".join(sorted(_PROVIDER_STATUS_URLS))
            raise ToolError(f"Провайдер {provider!r} не в списке поддерживаемых ({known})")
        async with _client(settings) as client:
            resp = await client.get(_PROVIDER_STATUS_URLS[provider])
            resp.raise_for_status()
        data = resp.json()
        indicator = data.get("status", {}).get("indicator", "unknown")
        description = data.get("status", {}).get("description", "нет данных")
        return f"Статус {provider}: {description} (indicator={indicator})"

    return [
        ToolSpec(
            name="lookup_exchange_rate",
            description="Конвертировать сумму между валютами по актуальному курсу. Используй, когда нужно объяснить цену тарифа в другой валюте.",
            parameters={
                "type": "object",
                "properties": {
                    "from_currency": {"type": "string", "description": "Код валюты-источника, напр. RUB"},
                    "to_currency": {"type": "string", "description": "Код целевой валюты, напр. USD"},
                    "amount": {"type": "number", "description": "Сумма для конвертации"},
                },
                "required": ["from_currency", "to_currency", "amount"],
            },
            handler=lookup_exchange_rate,
            category="public_api",
        ),
        ToolSpec(
            name="lookup_ip_region",
            description="Определить страну/регион по IP-адресу. Используй для вопросов про региональные цены или маршрутизацию поддержки.",
            parameters={
                "type": "object",
                "properties": {"ip": {"type": "string", "description": "IPv4/IPv6-адрес"}},
                "required": ["ip"],
            },
            handler=lookup_ip_region,
            category="public_api",
        ),
        ToolSpec(
            name="check_public_holiday",
            description="Проверить, является ли дата государственным праздником в стране. Используй, чтобы объяснить задержку ответа поддержки.",
            parameters={
                "type": "object",
                "properties": {
                    "country_code": {"type": "string", "description": "ISO-код страны, напр. RU"},
                    "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
                },
                "required": ["country_code", "date"],
            },
            handler=check_public_holiday,
            category="public_api",
        ),
        ToolSpec(
            name="verify_email_domain",
            description="Проверить, что домен email способен принимать почту (есть MX-записи). Используй при жалобах «письмо не пришло», прежде чем эскалировать.",
            parameters={
                "type": "object",
                "properties": {"email": {"type": "string", "description": "Email-адрес пользователя"}},
                "required": ["email"],
            },
            handler=verify_email_domain,
            category="public_api",
        ),
        ToolSpec(
            name="check_integration_provider_status",
            description=f"Проверить публичный статус известного провайдера интеграции. Разрешённые значения provider: {', '.join(sorted(_PROVIDER_STATUS_URLS))}.",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": sorted(_PROVIDER_STATUS_URLS),
                        "description": "Имя провайдера из фиксированного списка",
                    }
                },
                "required": ["provider"],
            },
            handler=check_integration_provider_status,
            category="public_api",
        ),
    ]


__all__ = ["build_public_api_tools"]

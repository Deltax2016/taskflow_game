"""Smoke-тест РЕАЛЬНЫХ инструментов против настоящих публичных API.

Помечен `@pytest.mark.network` — по умолчанию его можно пропускать
(`pytest -m "not network"`), т.к. он зависит от доступности интернета и
чужих сервисов, а не только от нашего кода. Запуск явно:

    pytest -m network tests/test_real_tools_smoke.py

Инструменты с зависимостью от Postgres (`check_service_health`,
`create_refund`) здесь не проверяются — им нужна поднятая БД, это часть
end-to-end проверки через docker compose (см. README), не юнит/smoke-уровня.
"""

import pytest

from app.agent.tools.public_api import build_public_api_tools
from app.agent.tools.server_side import build_server_side_tools
from app.config import Settings

pytestmark = pytest.mark.network


def _settings() -> Settings:
    return Settings(openrouter_api_key="test", tool_http_timeout_seconds=10.0)


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_lookup_exchange_rate_real():
    tools = build_public_api_tools(_settings())
    result = await _tool(tools, "lookup_exchange_rate").handler({"from_currency": "USD", "to_currency": "RUB", "amount": 10})
    assert "USD" in result and "RUB" in result


@pytest.mark.asyncio
async def test_lookup_ip_region_real():
    tools = build_public_api_tools(_settings())
    result = await _tool(tools, "lookup_ip_region").handler({"ip": "8.8.8.8"})
    assert "United States" in result or "US" in result


@pytest.mark.asyncio
async def test_check_public_holiday_real():
    tools = build_public_api_tools(_settings())
    result = await _tool(tools, "check_public_holiday").handler({"country_code": "RU", "date": "2026-01-01"})
    assert "2026-01-01" in result


@pytest.mark.asyncio
async def test_verify_email_domain_real():
    tools = build_public_api_tools(_settings())
    result = await _tool(tools, "verify_email_domain").handler({"email": "test@gmail.com"})
    assert "MX" in result


@pytest.mark.asyncio
async def test_check_integration_provider_status_real():
    tools = build_public_api_tools(_settings())
    result = await _tool(tools, "check_integration_provider_status").handler({"provider": "github"})
    assert "github" in result.lower()


@pytest.mark.asyncio
async def test_validate_integration_config_local_no_network():
    """Не сетевой, но живёт здесь же для полноты картины по server_side."""
    import json

    tools = build_server_side_tools(_settings())
    good_config = json.dumps({"url": "https://example.com/hook", "events": ["ticket.created"], "secret": "x" * 20})
    valid = await _tool(tools, "validate_integration_config").handler({"config_text": good_config})
    assert "валиден" in valid.lower()

    invalid = await _tool(tools, "validate_integration_config").handler({"config_text": "{}"})
    assert "невалиден" in invalid.lower()


@pytest.mark.asyncio
async def test_calculate_prorated_refund_local_no_network():
    tools = build_server_side_tools(_settings())
    result = await _tool(tools, "calculate_prorated_refund").handler(
        {"plan_price": 900, "period_days": 30, "days_used": 10}
    )
    assert "600.0" in result or "600" in result

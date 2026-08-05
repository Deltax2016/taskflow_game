"""Тесты SSRF-защиты (agent/tools/ssrf_guard.py) — используется test_webhook_endpoint.

Проверяем именно то, что перечислено в лекции как классические цели SSRF:
приватные сети, loopback, облачный metadata-эндпоинт (169.254.169.254),
неразрешённые схемы.
"""

import pytest

from app.agent.tools.base import ToolError
from app.agent.tools.ssrf_guard import assert_safe_url


def test_public_https_url_allowed():
    # example.com гарантированно резолвится в публичный IP.
    assert assert_safe_url("https://example.com/webhook") == "https://example.com/webhook"


def test_loopback_blocked():
    with pytest.raises(ToolError):
        assert_safe_url("http://127.0.0.1:8000/webhook")


def test_localhost_hostname_blocked():
    with pytest.raises(ToolError):
        assert_safe_url("http://localhost/webhook")


def test_private_network_blocked():
    with pytest.raises(ToolError):
        assert_safe_url("http://192.168.1.10/webhook")


def test_cloud_metadata_endpoint_blocked():
    """169.254.169.254 — классическая цель SSRF в облаке (временные креды
    инстанса). Попадает под link-local (169.254.0.0/16)."""
    with pytest.raises(ToolError):
        assert_safe_url("http://169.254.169.254/latest/meta-data/")


def test_disallowed_scheme_blocked():
    with pytest.raises(ToolError):
        assert_safe_url("file:///etc/passwd")


def test_url_without_host_blocked():
    with pytest.raises(ToolError):
        assert_safe_url("https://")


def test_unresolvable_host_blocked():
    with pytest.raises(ToolError):
        assert_safe_url("https://this-domain-should-not-exist-workshop4.invalid/webhook")

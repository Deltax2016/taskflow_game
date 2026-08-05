"""SSRF-защита для инструментов, которые сами делают исходящий HTTP-запрос
по URL, полученному от пользователя (см. `test_webhook_endpoint`).

Проверяем:
  1. Схему — только http/https (не file://, gopher://, itms-services:// и т.п.,
     которыми в реальных атаках читают локальные файлы или бьют по внутренним
     протоколам).
  2. Хост резолвится ТОЛЬКО в публичные адреса — не приватная сеть (RFC1918),
     не loopback, не link-local (сюда попадает 169.254.169.254 — классическая
     цель SSRF в облаке: оттуда отдают временные креды инстанса), не
     мультикаст, не reserved.

Честное ограничение (не скрываем, это тоже часть занятия): между проверкой
DNS и реальным запросом теоретически возможен DNS rebinding — адрес мог
поменяться в промежутке. Для учебного примера это приемлемо; в проде
дополнительно закрепляют резолвленный IP на конкретное соединение
(кастомный transport) или заворачивают исходящий трафик через egress-прокси
с allowlist на сетевом уровне — на уровне кода одного Python-инструмента
это не починить полностью, и делать вид, что починили, было бы нечестно.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.agent.tools.base import ToolError

_ALLOWED_SCHEMES = {"http", "https"}


def _is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # покрывает 169.254.0.0/16 — облачный metadata-эндпоинт
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> str:
    """Бросает `ToolError(...)`, если URL небезопасен для запроса
    ОТ ИМЕНИ СЕРВЕРА. Возвращает тот же `url`, если проверка пройдена —
    удобно вызывать инлайн перед реальным запросом.

    Блокирующий вызов (`socket.getaddrinfo`) — вызывайте через
    `asyncio.to_thread(assert_safe_url, url)` из async-обработчика.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ToolError(f"Схема {parsed.scheme!r} не разрешена — только http/https")
    if not parsed.hostname:
        raise ToolError("URL без хоста")

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ToolError(f"Не удалось разрешить хост {parsed.hostname!r}: {exc}") from None

    resolved_ips = {info[4][0] for info in infos}
    if not resolved_ips or not all(_is_public_ip(ip) for ip in resolved_ips):
        raise ToolError(
            f"Хост {parsed.hostname!r} резолвится в приватный/служебный адрес — запрос заблокирован (SSRF-защита)",
        )
    return url

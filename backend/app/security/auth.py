"""Аутентификация игрового режима: сессия участника и вход админа.

Два РАЗНЫХ уровня доверия, и это осознанно:

  * Участник входит по одному логину, без пароля. Аккаунт не защищает
    ничего ценного — баланс вымышленный, это счёт в игре. Но сессия всё
    равно ПОДПИСАНА (HMAC): без подписи участник просто подставил бы чужой
    логин в cookie и присвоил чужие очки, и игра перестала бы существовать.
    Подпись здесь защищает не деньги, а честность таблицы результатов.

  * Админ входит по логину И паролю. Здесь защищать есть что: админка
    одобряет вызовы критических инструментов и управляет игрой. В публичном
    деплое открытая админка означала бы, что участник сам себе одобряет
    любой возврат — то есть игры снова нет.

Никаких внешних зависимостей: `hmac`/`hashlib`/`secrets` из стандартной
библиотеки достаточно, а меньше зависимостей — меньше поверхность атаки.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from app.config import Settings

# Логин: буквы (латиница/кириллица), цифры, пробел, дефис, подчёркивание.
# Никаких управляющих символов и разметки — логин показывается в лидерборде
# и в админке, поэтому он не должен быть каналом для инъекции в UI.
_LOGIN_RE = re.compile(r"^[\w \-]{2,32}$", re.UNICODE)


class AuthError(Exception):
    """Невалидная/просроченная сессия или неверные учётные данные."""


def normalize_login(raw: str) -> tuple[str, str]:
    """Возвращает `(login_key, display_name)`.

    `login_key` — нижний регистр со схлопнутыми пробелами, по нему ищем
    игрока (чтобы «Вася» и «вася » были одним участником, а не двумя).
    `display_name` — как ввёл пользователь, для показа.
    """
    display = re.sub(r"\s+", " ", (raw or "").strip())
    if not _LOGIN_RE.match(display):
        raise AuthError(
            "Логин: 2-32 символа, только буквы, цифры, пробел, дефис или подчёркивание"
        )
    return display.casefold(), display


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes, secret: str) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest())


def issue_session(player_id: int, login: str, settings: Settings) -> str:
    """Подписанный токен сессии: `<payload>.<hmac>`.

    Внутри — id игрока и срок годности. Токен читаемый (base64, не шифр) и
    это нормально: секретов в нём нет, а подделать его без `session_secret`
    нельзя — в этом вся суть подписи.
    """
    payload = {
        "pid": player_id,
        "login": login,
        "exp": int(time.time()) + settings.session_ttl_hours * 3600,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{_b64e(raw)}.{_sign(raw, settings.session_secret)}"


def read_session(token: str, settings: Settings) -> dict:
    """Проверяет подпись и срок годности, возвращает payload.

    Сравнение подписи — `hmac.compare_digest` (константное время): обычный
    `==` выходит из сравнения на первом несовпавшем байте, и по разнице во
    времени ответа подпись можно подобрать побайтово.
    """
    try:
        body, signature = token.split(".", 1)
        raw = _b64d(body)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise AuthError("Повреждённый токен сессии") from exc

    if not hmac.compare_digest(signature, _sign(raw, settings.session_secret)):
        raise AuthError("Подпись сессии не совпала")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError("Повреждённый токен сессии") from exc

    if int(payload.get("exp", 0)) < int(time.time()):
        raise AuthError("Сессия истекла, войдите заново")
    return payload


def verify_admin(username: str, password: str, settings: Settings) -> bool:
    """Проверка админских учётных данных в константном времени.

    Сравниваем не сами строки, а их sha256: `compare_digest` на строках
    разной длины утекает длину пароля, а на дайджестах длина всегда одна.
    """
    expected_user = hashlib.sha256(settings.admin_username.encode("utf-8")).digest()
    expected_pass = hashlib.sha256(settings.admin_password.encode("utf-8")).digest()
    got_user = hashlib.sha256((username or "").encode("utf-8")).digest()
    got_pass = hashlib.sha256((password or "").encode("utf-8")).digest()
    # Оба сравнения выполняем всегда (без short-circuit `and`), чтобы время
    # ответа не зависело от того, угадан ли логин.
    user_ok = hmac.compare_digest(expected_user, got_user)
    pass_ok = hmac.compare_digest(expected_pass, got_pass)
    return user_ok and pass_ok


def issue_admin_session(settings: Settings) -> str:
    """Отдельный токен для админа — со своим маркером роли.

    Роль внутри подписанного payload, а не отдельным незащищённым полем:
    иначе игрок дописал бы себе `role=admin` в cookie.
    """
    payload = {
        "role": "admin",
        "nonce": secrets.token_hex(8),
        "exp": int(time.time()) + settings.session_ttl_hours * 3600,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"{_b64e(raw)}.{_sign(raw, settings.session_secret)}"


def is_admin_session(token: str, settings: Settings) -> bool:
    try:
        return read_session(token, settings).get("role") == "admin"
    except AuthError:
        return False

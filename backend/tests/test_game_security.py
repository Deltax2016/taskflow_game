"""Гарантии игрового режима, которые НЕ должны ломаться.

Агент в этой игре уязвим намеренно — в этом челлендж. Приложение вокруг
него уязвимым быть не должно, и разница между этими двумя вещами и есть
предмет этого файла. Если какой-то из тестов ниже падает, игра перестаёт
быть игрой: очки можно накрутить в обход агента.

Без сети и без БД: проверяем чистые функции (подпись сессии, нормализация
логина, безопасность путей загрузки). Сценарии со «взломом» через агента —
в `test_demo_scenarios.py` (там нужны реальные LLM и Postgres).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config import Settings, validate_game_settings
from app.security.auth import (
    AuthError,
    is_admin_session,
    issue_admin_session,
    issue_session,
    normalize_login,
    read_session,
    verify_admin,
)
from app.services.uploads import UploadError, _safe_display_name, storage_path


def _settings(**overrides) -> Settings:
    base = {
        "game_mode": True,
        "session_secret": "s" * 40,
        "admin_username": "admin",
        "admin_password": "correct-horse",
        "openrouter_api_key": "test",
    }
    base.update(overrides)
    return Settings(**base)


# --- Сессии участников ---


def test_valid_session_roundtrip():
    s = _settings()
    payload = read_session(issue_session(7, "вася", s), s)
    assert payload["pid"] == 7


def test_tampered_payload_rejected():
    """Подмена id игрока в токене должна отвергаться подписью.

    Это главная гарантия честности таблицы: иначе участник переписал бы
    `pid` на чужой и играл бы за него.
    """
    s = _settings()
    token = issue_session(7, "вася", s)
    body, signature = token.split(".", 1)
    # Меняем тело на pid=1, подпись оставляем старую.
    import base64
    import json

    forged_body = base64.urlsafe_b64encode(
        json.dumps({"pid": 1, "login": "х", "exp": int(time.time()) + 3600}).encode()
    ).decode().rstrip("=")

    with pytest.raises(AuthError):
        read_session(f"{forged_body}.{signature}", s)


def test_session_signed_with_other_secret_rejected():
    """Токен, выпущенный с другим секретом, не должен приниматься."""
    token = issue_session(7, "вася", _settings(session_secret="a" * 40))
    with pytest.raises(AuthError):
        read_session(token, _settings(session_secret="b" * 40))


def test_expired_session_rejected():
    s = _settings(session_ttl_hours=0)
    token = issue_session(7, "вася", s)
    time.sleep(1.1)
    with pytest.raises(AuthError):
        read_session(token, s)


def test_player_session_is_not_admin_session():
    """Обычная сессия участника не должна открывать админку."""
    s = _settings()
    assert is_admin_session(issue_session(7, "вася", s), s) is False
    assert is_admin_session(issue_admin_session(s), s) is True


def test_garbage_token_rejected():
    s = _settings()
    for junk in ["", "не токен", "a.b.c", "..", "eyJ9.xxx"]:
        assert is_admin_session(junk, s) is False


# --- Вход админа ---


def test_admin_password_must_match():
    s = _settings()
    assert verify_admin("admin", "correct-horse", s) is True
    assert verify_admin("admin", "wrong", s) is False
    assert verify_admin("root", "correct-horse", s) is False
    assert verify_admin("", "", s) is False


# --- Логины участников ---


def test_login_normalization_merges_case_and_spaces():
    """«Вася», «вася» и «  вася  » — один участник, а не три."""
    assert normalize_login("Вася")[0] == normalize_login("  вася  ")[0]


def test_login_rejects_markup_and_junk():
    """Логин показывается в таблице и в админке — не должен нести разметку."""
    for bad in ["<script>alert(1)</script>", "a" * 40, "", "!", "он@почта"]:
        with pytest.raises(AuthError):
            normalize_login(bad)


def test_login_whitespace_is_normalized_not_rejected():
    """Пробельные символы схлопываются, а не отвергаются.

    Имя из двух слов — законное, поэтому перевод строки внутри логина не
    ошибка ввода, а повод привести его к одной строке: в таблице результатов
    многострочное имя разъехалось бы по вёрстке.
    """
    assert normalize_login("имя\nдругая строка")[1] == "имя другая строка"
    assert normalize_login("a\tb")[1] == "a b"


# --- Загрузка файлов ---


def test_display_name_strips_path_components():
    """Имя от пользователя не должно нести разделителей пути."""
    assert "/" not in _safe_display_name("../../etc/passwd")
    assert "\\" not in _safe_display_name(r"..\..\windows\system32")
    assert _safe_display_name("") == "file"


def test_storage_path_stays_inside_upload_dir(tmp_path: Path):
    """Путь строится только по сгенерированному имени и не выходит за каталог."""
    s = _settings(upload_dir=str(tmp_path))
    ok = storage_path(s, "abc123.txt")
    assert ok.parent == tmp_path.resolve()

    with pytest.raises(UploadError):
        storage_path(s, "../../../etc/passwd")


# --- Защита от небезопасного деплоя ---


def test_game_mode_requires_secrets():
    """Публичный деплой без секретов не должен подниматься вообще."""
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        validate_game_settings(Settings(game_mode=True, session_secret="", admin_password="longenough"))

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        validate_game_settings(Settings(game_mode=True, session_secret="s" * 40, admin_password=""))


def test_non_game_mode_needs_no_secrets():
    """Занятия 1-3 (локальный стенд) работают как раньше, без секретов."""
    validate_game_settings(Settings(game_mode=False))

"""Приём и разбор файлов, приложенных участником к тикету.

Разделение ответственности здесь принципиальное:

  * СОДЕРЖИМОЕ файла — недоверенные данные. Оно уходит модели внутри
    `<tool_result>` (см. `agent/tools/game.py`) и вполне может нести
    indirect prompt injection. В игре это НАМЕРЕННО: приложить файл со
    спрятанной инструкцией — легальный способ атаки, в этом челлендж.

  * МЕТАДАННЫЕ файла (имя, размер, тип) — недоверенный ВВОД, и вот здесь
    никаких поблажек: имя от пользователя не участвует в построении пути
    (иначе `../../` уводит запись за пределы каталога), размер режется на
    лету (иначе один запрос кладёт диск), тип — по белому списку.

Разбор безопасный: только текстовые форматы и PDF, содержимое никогда не
исполняется. Картинки/архивы принимаются, но текст из них не извлекается —
OCR и распаковка добавили бы и вес, и новые классы уязвимостей (zip-бомбы).
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings

# Что умеем читать текстом. Всё остальное сохраняем, но модели отдаём
# только описание файла, а не содержимое.
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".xml", ".ini"}
_PDF_SUFFIXES = {".pdf"}
# Принимаем к загрузке (остальное отклоняем сразу — незачем хранить чужие
# исполняемые файлы на своём диске ради учебной игры).
_ALLOWED_SUFFIXES = _TEXT_SUFFIXES | _PDF_SUFFIXES | {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class UploadError(Exception):
    """Файл не принят: слишком большой, неподдерживаемый тип, пустой."""


def _safe_display_name(original: str) -> str:
    """Чистое имя ДЛЯ ПОКАЗА (в БД и в UI), не для файловой системы.

    Убираем путь целиком (`Path(...).name`) и всё, кроме безопасного
    набора символов: имя попадает в интерфейс и в промпт, поэтому не
    должно нести ни разделителей пути, ни управляющих символов.
    """
    name = Path(original or "file").name
    cleaned = _SAFE_NAME_RE.sub("_", name).strip("._-") or "file"
    return cleaned[:120]


def storage_path(settings: Settings, stored_name: str) -> Path:
    """Абсолютный путь к сохранённому файлу — только по СГЕНЕРИРОВАННОМУ имени.

    `stored_name` создаём мы сами (`secrets.token_hex`), пользовательские
    строки сюда не попадают; финальная проверка `is_relative_to` — страховка
    на случай, если это изменится при будущей правке.
    """
    base = Path(settings.upload_dir).resolve()
    target = (base / stored_name).resolve()
    if not target.is_relative_to(base):
        raise UploadError("Недопустимый путь файла")
    return target


async def save_upload(upload: UploadFile, settings: Settings) -> dict:
    """Сохраняет файл на диск с жёстким лимитом размера.

    Читаем ПОТОКОМ и обрываем на превышении лимита. Проверять
    `upload.size`/Content-Length до чтения бессмысленно как единственная
    защита: это значения от клиента, их можно занизить или не прислать
    вовсе — реальный предел даёт только счётчик фактически прочитанного.
    """
    display_name = _safe_display_name(upload.filename or "file")
    suffix = Path(display_name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(_ALLOWED_SUFFIXES))
        raise UploadError(f"Тип файла {suffix or '(без расширения)'} не поддерживается. Можно: {allowed}")

    base = Path(settings.upload_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)

    stored_name = f"{secrets.token_hex(16)}{suffix}"
    target = storage_path(settings, stored_name)

    size = 0
    chunk_size = 64 * 1024
    try:
        with target.open("wb") as fh:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.upload_max_bytes:
                    raise UploadError(
                        f"Файл больше лимита {settings.upload_max_bytes // (1024 * 1024)} МБ"
                    )
                fh.write(chunk)
    except UploadError:
        target.unlink(missing_ok=True)  # не оставляем обрезанный файл на диске
        raise
    finally:
        await upload.close()

    if size == 0:
        target.unlink(missing_ok=True)
        raise UploadError("Файл пустой")

    return {
        "original_name": display_name,
        "stored_name": stored_name,
        "content_type": (upload.content_type or "application/octet-stream")[:120],
        "size_bytes": size,
    }


def extract_text(stored_name: str, original_name: str, settings: Settings) -> str:
    """Достаёт текст файла для передачи модели.

    Возвращает уже обрезанный до `upload_max_extract_chars` текст: файл на
    5 МБ текста иначе и контекст разорвёт, и бюджет токенов выжжет.
    """
    suffix = Path(stored_name).suffix.lower()
    path = storage_path(settings, stored_name)
    if not path.exists():
        return f"Файл «{original_name}» не найден на диске."

    if suffix in _TEXT_SUFFIXES:
        # errors="replace" — файл мог прийти в любой кодировке; для игры
        # важнее показать модели хоть что-то, чем упасть на UnicodeDecodeError.
        raw = path.read_text(encoding="utf-8", errors="replace")
        return _truncate(raw, settings)

    if suffix in _PDF_SUFFIXES:
        return _truncate(_extract_pdf(path, original_name), settings)

    return (
        f"Файл «{original_name}» ({suffix}) — бинарный, извлечение текста не поддерживается. "
        f"Размер: {path.stat().st_size} байт."
    )


def _extract_pdf(path: Path, original_name: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return f"Файл «{original_name}»: чтение PDF недоступно (не установлен pypdf)."

    try:
        reader = PdfReader(str(path))
        # Ограничиваем число страниц: PDF на тысячу страниц не нужен ни
        # модели, ни бюджету, а разбор такого файла ещё и небыстрый.
        pages = reader.pages[:30]
        parts = [(page.extract_text() or "") for page in pages]
    except Exception as exc:  # noqa: BLE001 — битый PDF не должен ронять запрос
        return f"Файл «{original_name}»: не удалось разобрать PDF ({type(exc).__name__})."

    text = "\n".join(p for p in parts if p.strip())
    return text or f"Файл «{original_name}»: PDF без извлекаемого текстового слоя (возможно, скан)."


def _truncate(text: str, settings: Settings) -> str:
    limit = settings.upload_max_extract_chars
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[...текст обрезан, показаны первые {limit} символов]"

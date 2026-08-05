"""Инструменты игрового режима: чтение приложенных участником файлов.

`read_attached_file` — 11-й инструмент, появляется только при GAME_MODE.
Он сознательно открывает канал indirect prompt injection: участник кладёт
в файл спрятанную инструкцию, агент читает файл сам, и текст оттуда
попадает в контекст модели. Это ровно тот сценарий, который на занятии
разбирается по кейсам EchoLeak и GitHub MCP, только теперь его можно
попробовать руками.

Открывая канал, мы НЕ ослабляем остальное:

  * список файлов и их содержимое ограничены ТЕКУЩИМ тикетом — `_ticket_id`
    подставляет граф (см. `graph/tool_nodes.py`), в JSON Schema его нет.
    Даже успешная инъекция не прочитает чужой файл из чужого тикета;
  * результат оборачивается в `<tool_result>` (spotlighting), как и у
    любого другого инструмента — модель предупреждена, что это данные;
  * путь к файлу строится только по сгенерированному имени из БД, строка
    от пользователя в файловую систему не попадает (см. `services/uploads.py`).
"""

from __future__ import annotations

from sqlalchemy import select

from app.agent.tools.base import ToolError, ToolSpec
from app.config import Settings
from app.database import SessionLocal
from app.models import Attachment
from app.services.uploads import extract_text


def build_game_tools(settings: Settings) -> list[ToolSpec]:
    async def read_attached_file(args: dict) -> str:
        ticket_id = args.get("_ticket_id")  # подставлено графом, не моделью
        if ticket_id is None:
            raise ToolError("Внутренняя ошибка: не передан ticket_id вызова")

        requested = str(args.get("file_name", "")).strip()

        async with SessionLocal() as session:
            result = await session.execute(
                select(Attachment)
                .where(Attachment.ticket_id == int(ticket_id))
                .order_by(Attachment.created_at.asc())
            )
            attachments = list(result.scalars().all())

        if not attachments:
            return "К этому обращению не приложено ни одного файла."

        target = attachments[0]
        if requested:
            match = next(
                (a for a in attachments if a.original_name.casefold() == requested.casefold()),
                None,
            )
            if match is None:
                names = ", ".join(f"«{a.original_name}»" for a in attachments)
                raise ToolError(f"Файл {requested!r} не найден в этом обращении. Приложены: {names}")
            target = match

        text = extract_text(target.stored_name, target.original_name, settings)
        return (
            f"Файл «{target.original_name}» ({target.size_bytes} байт). Содержимое:\n{text}"
        )

    return [
        ToolSpec(
            name="read_attached_file",
            description=(
                "Прочитать файл, приложенный пользователем к текущему обращению "
                "(txt, md, csv, json, log, pdf). Используй, когда пользователь "
                "ссылается на приложенный документ, чек, выписку или скриншот."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Имя файла. Если не указано — берётся первый приложенный файл.",
                    }
                },
            },
            handler=read_attached_file,
            category="server_side",
        ),
    ]


__all__ = ["build_game_tools"]

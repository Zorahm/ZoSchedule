"""What the bot does for the attendance journal in Telegram: the table file and the menu button."""

from __future__ import annotations

import html
import logging

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import BufferedInputFile, MenuButtonWebApp, WebAppInfo

from app import moscow, texts
from app.attendance.models import JournalDay
from app.attendance.report import ReportError, file_name, report_html
from app.bot import errors
from app.bot.context import BotContext
from app.render.renderer import RenderError

logger = logging.getLogger(__name__)


class ReportSender:
    def __init__(self, ctx: BotContext) -> None:
        self._ctx = ctx

    async def send_attendance_report(self, user_id: int, day: JournalDay, *, titles: bool) -> None:
        """Draws the day's full table and sends it to the headman as a file.

        A file, not a photo: Telegram squeezes a photo to 1280 px on its longer side, and a
        table of thirty rows becomes unreadable. The headman forwards it to the curator.
        """
        group = self._ctx.config.group.name
        page = report_html(group, day, titles=titles, sent_at=moscow.now())
        try:
            png = await self._ctx.renderer.render(page)
        except RenderError as error:
            logger.warning("Не удалось нарисовать таблицу посещаемости: %s", error)
            raise ReportError("Не удалось нарисовать картинку: на сервере нет браузера для неё.") from error
        absent = sum(1 for student in day.students for mark in student.marks.values() if mark == "absent")
        caption = (
            f"Посещаемость {html.escape(group)}, {texts.date_long(day.date)}. "
            f"Отсутствий (Н): {absent}."
        )
        try:
            await self._ctx.bot.send_document(
                chat_id=user_id, document=BufferedInputFile(png, file_name(day.date)), caption=caption
            )
        except TelegramForbiddenError as error:
            raise ReportError(
                "Бот не может вам написать. Откройте чат с ботом и нажмите «Старт», потом повторите.", 409
            ) from error
        except errors.TELEGRAM_ERRORS as error:
            logger.warning("Telegram не принял таблицу посещаемости: %s", error)
            raise ReportError("Telegram не принял файл. Попробуйте ещё раз через минуту.") from error

    async def pin_journal_button(self, user_id: int, web_url: str | None) -> None:
        """Makes the journal the menu button of the headman's private chat. Cosmetic: never fails."""
        if web_url is None:
            return
        try:
            await self._ctx.bot.set_chat_menu_button(
                chat_id=user_id,
                menu_button=MenuButtonWebApp(text="Журнал", web_app=WebAppInfo(url=web_url)),
            )
        except errors.TELEGRAM_ERRORS as error:
            logger.warning("Не удалось поставить кнопку журнала для %s: %s", user_id, error)

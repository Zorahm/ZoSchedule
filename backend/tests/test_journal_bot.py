"""Бот и журнал: кнопка мини-приложения для старосты и файл с таблицей."""

from __future__ import annotations

import datetime as dt

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendDocument
from aiogram.types import Chat, Message, Update, User
from pydantic import SecretStr

from app.attendance.models import JournalDay, Pair, StudentRow
from app.attendance.report import ReportError
from app.bot.runner import build_dispatcher
from app.bot.service import BotService
from app.config import AppConfig, BotConfig, WebConfig
from app.db import init_db
from tests.fakes import Clock, FakeRenderer, FakeTelegram

HEADMAN, OWNER, STRANGER = 7, 8, 9
URL = "https://journal.example.org"
DAY = dt.date(2026, 10, 2)
_WHEN = dt.datetime(2026, 10, 2, 10, 0, tzinfo=dt.UTC)


def _config(config: AppConfig, *, url: str = URL) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), headmen=[HEADMAN], trusted_users=[OWNER])
    return config.model_copy(update={"bot": bot, "web": WebConfig(public_url=url)})


def _service(config: AppConfig, telegram: FakeTelegram, renderer: FakeRenderer | None = None) -> BotService:
    return BotService(config, telegram.bot, renderer or FakeRenderer())


def _private(text: str, *, by: int, update_id: int = 1) -> Update:
    message = Message(message_id=500 + update_id, date=_WHEN, chat=Chat(id=by, type="private"),
                      from_user=User(id=by, is_bot=False, first_name="Староста"), text=text,
                      entities=[{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}])  # type: ignore[list-item]
    return Update(update_id=update_id, message=message)


def _day() -> JournalDay:
    pair = Pair(slot="08:30", number=1, start="08:30", end="10:00", title="Математика", kind="Лекция",
                teacher=None, room=None, lessons=1)
    return JournalDay(date=DAY, state="open", opens_at=None, pairs=[pair],
                      students=[StudentRow(id=1, name="Абрамов Илья", marks={"08:30": "absent"})])


def _dispatcher(config: AppConfig, telegram: FakeTelegram, at: Clock, *, url: str = URL) -> Dispatcher:
    at(DAY, "10:00")
    init_db(config.db_path)
    return build_dispatcher(_service(_config(config, url=url), telegram))


async def test_the_headman_gets_a_button_that_opens_the_journal(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    dispatcher = _dispatcher(config, telegram, at)

    await dispatcher.feed_update(telegram.bot, _private("/start", by=HEADMAN))

    [text] = telegram.texts
    assert "Журнал" in text
    assert telegram.menu_buttons == [str(HEADMAN)]  # и кнопка меню в его чате


async def test_the_command_works_under_its_own_name_too(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    dispatcher = _dispatcher(config, telegram, at)

    await dispatcher.feed_update(telegram.bot, _private("/attendance", by=OWNER))

    assert len(telegram.texts) == 1


async def test_a_stranger_gets_no_answer_at_all(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    dispatcher = _dispatcher(config, telegram, at)

    await dispatcher.feed_update(telegram.bot, _private("/start", by=STRANGER))

    assert telegram.calls == [] and telegram.menu_buttons == []


async def test_with_the_journal_off_the_headman_is_told_how_to_turn_it_on(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    dispatcher = _dispatcher(config, telegram, at, url="")

    await dispatcher.feed_update(telegram.bot, _private("/start", by=HEADMAN))

    [text] = telegram.texts
    assert "web.public_url" in text and telegram.menu_buttons == []


async def test_the_table_goes_to_the_headman_as_a_file_not_a_photo(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(DAY, "15:42")
    service = _service(_config(config), telegram)

    await service.send_attendance_report(HEADMAN, _day(), titles=True)

    assert telegram.documents == [(str(HEADMAN), "attendance-2026-10-02.png")]
    [caption] = telegram.captions
    assert caption == "Посещаемость ОККИПд-307, 2 октября. Отсутствий (Н): 1."  # без «перешлите куратору»
    assert "photo" not in telegram.kinds()


async def test_a_headman_who_never_started_the_bot_is_told_to_press_start(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(DAY, "15:42")
    telegram.fail(SendDocument, TelegramForbiddenError(
        SendDocument(chat_id=HEADMAN, document="x"), "Forbidden: bot can't initiate conversation with a user"))
    service = _service(_config(config), telegram)

    with pytest.raises(ReportError, match="Старт") as raised:
        await service.send_attendance_report(HEADMAN, _day(), titles=True)

    assert raised.value.status == 409


async def test_a_missing_browser_is_said_plainly(
    config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    from app.render.renderer import RenderError

    class NoBrowser:
        async def render(self, html: str) -> bytes:
            raise RenderError("нет браузера")

    at(DAY, "15:42")
    service = BotService(_config(config), telegram.bot, NoBrowser())

    with pytest.raises(ReportError, match="браузер"):
        await service.send_attendance_report(HEADMAN, _day(), titles=True)

    assert telegram.documents == []

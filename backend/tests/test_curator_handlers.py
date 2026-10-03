"""Куратор пишет в группе о смене аудитории: кто может, куда это доходит, что отвечает бот."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from aiogram import Dispatcher
from aiogram.types import Chat, Message, Update, User
from pydantic import SecretStr, ValidationError

from app.bot.runner import build_dispatcher
from app.bot.service import BotService
from app.config import AppConfig, BotConfig, load_config
from app.db import connect
from app.snapshots.service import ScheduleService
from tests.fakes import Clock, FakeRenderer, FakeTelegram, save_demo

TUESDAY = dt.date(2026, 9, 29)
GROUP_CHAT = -100777
OTHER_CHAT = -100888
CURATOR = 21
OWNER = 7
STRANGER = 8
NOTICE = "добрый день\nв 13.50 у ОККИПд-306,307 пара будет в 314 аудитории"
_WHEN = dt.datetime(2026, 9, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def curator_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), trusted_users=[OWNER], curators=[CURATOR])
    return config.model_copy(update={"bot": bot})


@pytest.fixture
def service(curator_config: AppConfig, telegram: FakeTelegram, at: Clock) -> BotService:
    at(TUESDAY, "10:00")
    save_demo(curator_config, TUESDAY)
    schedule = ScheduleService(curator_config)
    return BotService(curator_config, telegram.bot, FakeRenderer(), corrector=schedule)


@pytest.fixture
def dispatcher(service: BotService) -> Dispatcher:
    return build_dispatcher(service)


async def _working(service: BotService, telegram: FakeTelegram, *chats: int) -> None:
    """The bot is at work in these groups: pictures up, the change feed read to its end."""
    for chat in chats:
        assert await service.go(str(chat), None) is None
    await service.announce_changes()  # the baseline of each chat
    telegram.calls.clear()
    telegram.texts.clear()
    telegram.chats.clear()
    telegram.text_chats.clear()
    telegram.silent.clear()


def _said(text: str, *, by: int | None = CURATOR, chat: int = GROUP_CHAT, update_id: int = 1) -> Update:
    message = Message(
        message_id=500 + update_id,
        date=_WHEN,
        chat=Chat(id=chat, type="supergroup"),
        from_user=User(id=by, is_bot=False, first_name="Куратор") if by is not None else None,
        text=text,
    )
    return Update(update_id=update_id, message=message)


def _snapshots(config: AppConfig) -> int:
    with connect(config.db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])


async def test_a_curators_notice_changes_the_room_and_the_chat_hears_about_it(
    dispatcher: Dispatcher, service: BotService, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT)

    await dispatcher.feed_update(telegram.bot, _said(NOTICE))

    [text] = telegram.texts
    assert "314" in text and "305" in text  # from which room to which
    assert telegram.kinds().count("edit") == 2  # the week's and the day's pictures were redrawn
    assert telegram.text_chats == [str(GROUP_CHAT)]


async def test_every_working_group_hears_about_it(
    dispatcher: Dispatcher, service: BotService, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT, OTHER_CHAT)

    await dispatcher.feed_update(telegram.bot, _said(NOTICE))  # written in one of them

    assert sorted(telegram.text_chats) == sorted([str(OTHER_CHAT), str(GROUP_CHAT)])


async def test_the_bots_owner_may_correct_too(
    dispatcher: Dispatcher, service: BotService, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT)

    await dispatcher.feed_update(telegram.bot, _said(NOTICE, by=OWNER))

    assert len(telegram.texts) == 1


async def test_a_stranger_cannot_change_the_schedule_and_is_logged(
    dispatcher: Dispatcher,
    service: BotService,
    curator_config: AppConfig,
    telegram: FakeTelegram,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _working(service, telegram, GROUP_CHAT)
    before = _snapshots(curator_config)

    with caplog.at_level("WARNING"):
        await dispatcher.feed_update(telegram.bot, _said(NOTICE, by=STRANGER))

    assert telegram.calls == []  # not a word
    assert _snapshots(curator_config) == before
    assert f"пользователя {STRANGER}" in caplog.text  # the owner can find the id there


async def test_an_anonymous_admin_is_not_a_curator(
    dispatcher: Dispatcher, service: BotService, curator_config: AppConfig, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT)
    before = _snapshots(curator_config)

    await dispatcher.feed_update(telegram.bot, _said(NOTICE, by=None))

    assert _snapshots(curator_config) == before and telegram.calls == []


async def test_a_chat_the_bot_does_not_work_in_is_ignored(
    dispatcher: Dispatcher, service: BotService, curator_config: AppConfig, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT)
    before = _snapshots(curator_config)

    await dispatcher.feed_update(telegram.bot, _said(NOTICE, chat=OTHER_CHAT))

    assert _snapshots(curator_config) == before and telegram.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "в 13.50 у ОККИПд-306,308 пара будет в 314 аудитории",  # other groups
        "в 15.00 родительское собрание в 205 аудитории",
        "добрый день",
    ],
)
async def test_what_is_not_about_our_group_is_left_alone(
    dispatcher: Dispatcher, service: BotService, curator_config: AppConfig, telegram: FakeTelegram, text: str
) -> None:
    await _working(service, telegram, GROUP_CHAT)
    before = _snapshots(curator_config)

    await dispatcher.feed_update(telegram.bot, _said(text))

    assert _snapshots(curator_config) == before and telegram.calls == []


async def test_a_notice_that_finds_no_lesson_is_answered_to_the_curator(
    dispatcher: Dispatcher, service: BotService, curator_config: AppConfig, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT)
    before = _snapshots(curator_config)

    await dispatcher.feed_update(telegram.bot, _said("в 09.00 у ОККИПд-307 пара в 314 аудитории"))

    [answer] = telegram.texts
    assert "Не нашёл пару в 09:00" in answer and "не менял" in answer
    assert telegram.silent == [True]
    assert _snapshots(curator_config) == before


async def test_the_same_notice_in_the_second_group_is_not_announced_twice(
    dispatcher: Dispatcher, service: BotService, telegram: FakeTelegram
) -> None:
    await _working(service, telegram, GROUP_CHAT, OTHER_CHAT)
    await dispatcher.feed_update(telegram.bot, _said(NOTICE, update_id=1))
    telegram.texts.clear()
    telegram.calls.clear()

    await dispatcher.feed_update(telegram.bot, _said(NOTICE, chat=OTHER_CHAT, update_id=2))

    assert telegram.calls == []  # the room already is 314: nothing to say


async def test_a_command_is_still_a_command(
    dispatcher: Dispatcher, service: BotService, curator_config: AppConfig, telegram: FakeTelegram
) -> None:
    # /stop reaches its handler, not the notice parser, even with a notice-like tail.
    await _working(service, telegram, GROUP_CHAT)
    before = _snapshots(curator_config)

    await dispatcher.feed_update(telegram.bot, _said("/stop в 13.50 ОККИПд-307 в 314 ауд.", by=OWNER))

    assert service.load_targets() == []  # stopped, as /stop says
    assert _snapshots(curator_config) == before  # and the room was not touched


def _load_curators(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None) -> BotConfig:
    base = Path(__file__).resolve().parents[2] / "config.toml"
    config_file = tmp_path / "config.toml"
    config_file.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("ZOSCHEDULE_CONFIG", str(config_file))
    monkeypatch.setenv("ZOSCHEDULE_ENV_FILE", "")
    monkeypatch.setenv("ZOSCHEDULE_BOT_TOKEN", "4242:TEST")
    if value is None:
        monkeypatch.delenv("ZOSCHEDULE_BOT_CURATORS", raising=False)
    else:
        monkeypatch.setenv("ZOSCHEDULE_BOT_CURATORS", value)
    return load_config().bot


def test_nobody_is_a_curator_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _load_curators(monkeypatch, tmp_path, None).curators == []


def test_curators_come_from_the_env_comma_separated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _load_curators(monkeypatch, tmp_path, " 30, 20 ;30,,").curators == [20, 30]


@pytest.mark.parametrize("value", ["abc", "20,x", "-100123", "0"])
def test_a_bad_curator_id_is_refused_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        _load_curators(monkeypatch, tmp_path, value)

    assert "ожидается список положительных Telegram-id" in str(raised.value)

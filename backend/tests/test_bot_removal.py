"""Deleting the bot's messages by hand: /del in the chat and the service behind the console command."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Message, Update, User
from pydantic import SecretStr

from app.bot import store as bot_store
from app.bot.runner import build_dispatcher
from app.bot.service import BotService
from app.config import AppConfig, BotConfig
from app.db import connect, init_db
from app.snapshots.service import ScheduleService
from tests.fakes import BOT_ID, Clock, FakeRenderer, FakeTelegram, save_demo

GROUP_CHAT = -100777
CHAT = str(GROUP_CHAT)
TRUSTED = 7
STRANGER = 8
TUESDAY = dt.date(2026, 9, 29)
_WHEN = dt.datetime(2026, 9, 29, 10, 0, tzinfo=dt.UTC)
_KINDS: tuple[bot_store.Kind, ...] = ("week", "today", "changes")


@pytest.fixture
def bot_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), chat_id=CHAT, trusted_users=[TRUSTED])
    return config.model_copy(update={"bot": bot})


@pytest.fixture
def bot(bot_config: AppConfig, telegram: FakeTelegram) -> BotService:
    ScheduleService(bot_config).prepare()
    return BotService(bot_config, telegram.bot, FakeRenderer())


@pytest.fixture
def dispatcher(bot: BotService) -> Dispatcher:
    return build_dispatcher(bot)


def _del(*, replying_to: int | None, author: int = BOT_ID, sender: int = TRUSTED) -> Update:
    chat = Chat(id=GROUP_CHAT, type="supergroup")
    replied = (
        None
        if replying_to is None
        else Message(
            message_id=replying_to,
            date=_WHEN,
            chat=chat,
            from_user=User(id=author, is_bot=author == BOT_ID, first_name="Someone"),
        )
    )
    message = Message(
        message_id=900,
        date=_WHEN,
        chat=chat,
        from_user=User(id=sender, is_bot=False, first_name="Someone"),
        text="/del",
        reply_to_message=replied,
    )
    return Update(update_id=1, message=message)


def _ledger(config: AppConfig, kind: bot_store.Kind) -> list[bot_store.PostedMessage]:
    with connect(config.db_path) as conn:
        return bot_store.all_of_kind(conn, chat_id=CHAT, kind=kind, include_removed=True)


async def _post_week_and_day(bot: BotService, config: AppConfig, at: Clock) -> None:
    at(TUESDAY, "10:00")
    save_demo(config, TUESDAY)
    await bot.post_week(force=True)
    await bot.post_today()
    with connect(config.db_path) as conn:
        bot_store.record(conn, chat_id=CHAT, kind="changes", day=TUESDAY, message_id=555)


# -- the service ------------------------------------------------------------


async def test_remove_everything_takes_down_all_kinds(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    posted = [m.message_id for kind in _KINDS for m in _ledger(bot_config, kind)]
    telegram.calls.clear()

    assert await bot.remove_posts() == 3

    assert sorted(m for kind, m in telegram.calls if kind == "delete") == sorted(posted)
    assert all(m.removed for kind in _KINDS for m in _ledger(bot_config, kind))


async def test_remove_one_kind_leaves_the_rest(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)

    assert await bot.remove_posts(kind="today") == 1

    assert [m.removed for m in _ledger(bot_config, "today")] == [True]
    assert [m.removed for m in _ledger(bot_config, "week")] == [False]


async def test_a_removed_picture_is_not_posted_again_by_the_cycle(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    await bot.remove_posts()
    telegram.calls.clear()

    await bot.tick()

    assert telegram.calls == []  # no repost, and no edit of what is gone


async def test_removed_ones_are_swept_with_the_morning_cleanup(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    await bot.remove_posts()
    telegram.calls.clear()

    at(dt.date(2026, 9, 30), "08:00")
    await bot.cleanup(dt.date(2026, 9, 30))

    assert _ledger(bot_config, "today") == [] and _ledger(bot_config, "changes") == []
    assert telegram.calls == []  # only the rows go: Telegram was already told


async def test_a_new_week_sweeps_a_removed_one(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    await bot.remove_posts(kind="week")

    at(dt.date(2026, 10, 4), "12:00")
    await bot.post_week()

    assert [m.day for m in _ledger(bot_config, "week")] == [dt.date(2026, 10, 5)]


async def test_a_message_the_ledger_does_not_know_is_still_deleted(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)

    await bot.remove_message(CHAT, 4321)

    assert ("delete", 4321) in telegram.calls
    assert not any(m.removed for kind in _KINDS for m in _ledger(bot_config, kind))


async def test_a_message_already_gone_counts_as_removed(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    gone = TelegramBadRequest(
        DeleteMessage(chat_id=CHAT, message_id=1), "Bad Request: message to delete not found"
    )
    telegram.fail(DeleteMessage, gone)

    assert await bot.remove_posts(kind="today") == 1
    assert [m.removed for m in _ledger(bot_config, "today")] == [True]


async def test_unreachable_telegram_leaves_the_ledger_untouched(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    down = TelegramNetworkError(DeleteMessage(chat_id=CHAT, message_id=1), "down")
    telegram.fail(DeleteMessage, down)

    with pytest.raises(TelegramNetworkError):
        await bot.remove_posts(kind="today")

    assert [m.removed for m in _ledger(bot_config, "today")] == [False]  # a retry still finds it


# -- /del in the chat -------------------------------------------------------


async def test_del_as_a_reply_removes_the_message_and_the_command(
    dispatcher: Dispatcher,
    bot: BotService,
    bot_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    await _post_week_and_day(bot, bot_config, at)
    picture = _ledger(bot_config, "today")[0].message_id
    telegram.calls.clear()

    await dispatcher.feed_update(telegram.bot, _del(replying_to=picture))

    assert telegram.calls == [("delete", picture), ("delete", 900)]
    assert _ledger(bot_config, "today")[0].removed


async def test_del_works_on_a_message_outside_the_ledger(
    dispatcher: Dispatcher, bot_config: AppConfig, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _del(replying_to=4321))

    assert telegram.calls == [("delete", 4321), ("delete", 900)]


async def test_del_by_a_stranger_does_nothing(
    dispatcher: Dispatcher, bot_config: AppConfig, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _del(replying_to=4321, sender=STRANGER))

    assert telegram.calls == []


@pytest.mark.parametrize(
    "update", [_del(replying_to=None), _del(replying_to=4321, author=STRANGER)]
)
async def test_del_without_a_bot_message_to_point_at_explains_itself(
    dispatcher: Dispatcher, bot_config: AppConfig, telegram: FakeTelegram, update: Update
) -> None:
    await dispatcher.feed_update(telegram.bot, update)

    assert telegram.kinds() == ["message"] and "Ответьте" in telegram.texts[0]


async def test_del_that_telegram_refuses_is_reported_and_keeps_the_command(
    dispatcher: Dispatcher, bot_config: AppConfig, telegram: FakeTelegram
) -> None:
    refused = TelegramBadRequest(
        DeleteMessage(chat_id=CHAT, message_id=1), "Bad Request: message can't be deleted for everyone"
    )
    telegram.fail(DeleteMessage, refused)

    await dispatcher.feed_update(telegram.bot, _del(replying_to=4321))

    assert telegram.kinds() == ["message"] and "Не вышло" in telegram.texts[0]


# -- an existing database ---------------------------------------------------


def test_an_existing_ledger_gains_the_removed_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE bot_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,"
            " kind TEXT NOT NULL, day TEXT NOT NULL, message_id INTEGER NOT NULL,"
            " created_at TEXT NOT NULL, fingerprint TEXT)"
        )
        conn.execute("INSERT INTO bot_messages VALUES (1, 'c', 'week', '2026-09-28', 5, 'x', NULL)")

    init_db(path)

    with connect(path) as conn:
        [message] = bot_store.all_of_kind(conn, chat_id="c", kind="week")
    assert message.removed is False

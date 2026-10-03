"""Бот в нескольких группах сразу: одно расписание, у каждой группы свои сообщения."""

from __future__ import annotations

import datetime as dt
from collections import Counter

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from pydantic import SecretStr

from app.bot.dev import demo
from app.bot import store as bot_store
from app.bot.service import BotService
from app.bot.store import Target
from app.config import AppConfig, BotConfig, GroupConfig, PollConfig
from app.db import connect
from app.snapshots import store
from app.snapshots.service import ScheduleService
from tests.conftest import GROUP
from tests.fakes import Clock, FakeRenderer, FakeTelegram, save_demo

FIRST = "-1001"
SECOND = "-1002"
SUNDAY = dt.date(2026, 9, 27)
TUESDAY = dt.date(2026, 9, 29)
WEDNESDAY = dt.date(2026, 9, 30)


@pytest.fixture
def two_groups(config: AppConfig, telegram: FakeTelegram) -> BotService:
    """Two chats saved, none configured: the way /go leaves the bot."""
    quiet = config.model_copy(update={"bot": BotConfig(token=SecretStr("token"))})
    ScheduleService(quiet).prepare()
    service = BotService(quiet, telegram.bot, FakeRenderer())
    service.add_target(FIRST, None)
    service.add_target(SECOND, 5)
    return service


def _schedule_config(service: BotService) -> AppConfig:
    return AppConfig(group=GroupConfig(name=GROUP), poll=PollConfig(), db_path=service.db_path)


def _new_change(service: BotService, today: dt.date) -> None:
    with connect(service.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        current = store.load_lessons(conn, latest.id)
    ScheduleService(_schedule_config(service)).store_lessons(demo.apply_changes(current, today))


def test_both_groups_are_known_in_the_order_they_were_added(two_groups: BotService) -> None:
    assert two_groups.load_targets() == [Target(FIRST, None), Target(SECOND, 5)]


async def test_go_in_the_second_group_keeps_the_first_one_and_its_posts(
    two_groups: BotService, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    save_demo(_schedule_config(two_groups), TUESDAY)

    assert await two_groups.go(FIRST, None) is None
    assert await two_groups.go(SECOND, 5) is None

    # A week and a day in each; nothing was deleted: the second /go did not touch the first chat.
    assert Counter(telegram.chats) == {FIRST: 2, SECOND: 2}
    assert "delete" not in telegram.kinds()
    assert telegram.threads.count(5) == 2  # the second group's topic got its own posts


async def test_the_tick_posts_the_day_to_every_group_once(
    two_groups: BotService, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(_schedule_config(two_groups), TUESDAY)

    await two_groups.tick()
    await two_groups.tick()

    assert Counter(telegram.chats) == {FIRST: 1, SECOND: 1}  # the second tick posts nothing new


async def test_a_change_is_announced_in_every_group(
    two_groups: BotService, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(_schedule_config(two_groups), TUESDAY)
    await two_groups.announce_changes()  # baseline in both
    _new_change(two_groups, TUESDAY)

    assert await two_groups.announce_changes() == 8  # four events, two groups
    assert await two_groups.announce_changes() == 0

    assert sorted(telegram.chats) == [FIRST, SECOND]
    assert all("Изменения в расписании" in text for text in telegram.texts)


async def test_a_new_group_does_not_get_the_old_changes_as_news(
    two_groups: BotService, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(_schedule_config(two_groups), TUESDAY)
    await two_groups.announce_changes()
    _new_change(two_groups, TUESDAY)
    await two_groups.announce_changes()
    telegram.chats.clear()

    two_groups.add_target("-1003", None)

    assert await two_groups.announce_changes() == 0  # history for the newcomer
    assert telegram.chats == []


async def test_a_group_that_rejects_the_bot_does_not_hold_up_or_duplicate_for_the_other(
    two_groups: BotService, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(_schedule_config(two_groups), TUESDAY)
    await two_groups.announce_changes()  # baseline in both
    _new_change(two_groups, TUESDAY)
    forbidden = TelegramForbiddenError(SendMessage(chat_id=FIRST, text="x"), "Forbidden: kicked")
    telegram.fail(SendMessage, forbidden, chat=FIRST)

    await two_groups.tick()
    await two_groups.tick()  # the first chat is on its pause, the second has nothing new

    assert Counter(telegram.text_chats) == {SECOND: 1}  # no text for the rejecting chat, none repeated
    assert telegram.chats.count(FIRST) == 1  # its picture still went: only the text job failed

    telegram.fail(SendMessage, None)
    at(TUESDAY, "08:10")  # past the five-minute pause
    await two_groups.tick()

    assert Counter(telegram.text_chats) == {FIRST: 1, SECOND: 1}  # caught up; no duplicate for the other


async def test_retiring_in_one_group_leaves_the_other_groups_posts(
    two_groups: BotService, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(_schedule_config(two_groups), TUESDAY)
    await two_groups.post_today()

    await two_groups.cleanup(WEDNESDAY, target=Target(FIRST, None))

    with connect(two_groups.db_path) as conn:
        assert bot_store.all_of_kind(conn, chat_id=FIRST, kind="today") == []
        assert len(bot_store.all_of_kind(conn, chat_id=SECOND, kind="today")) == 1
    assert telegram.kinds().count("delete") == 1


def test_stopping_one_group_leaves_the_other(two_groups: BotService) -> None:
    assert two_groups.remove_target(FIRST) is True

    assert two_groups.load_targets() == [Target(SECOND, 5)]
    assert two_groups.remove_target(FIRST) is False  # already gone


def test_a_repeated_go_in_a_known_group_only_changes_the_topic(two_groups: BotService) -> None:
    two_groups.add_target(FIRST, 9)

    assert two_groups.load_targets() == [Target(FIRST, 9), Target(SECOND, 5)]  # same order, no copy


def test_the_configured_chat_stands_in_only_while_none_is_saved(
    config: AppConfig, telegram: FakeTelegram
) -> None:
    configured = config.model_copy(
        update={"bot": BotConfig(token=SecretStr("token"), chat_id="-1009", thread_id=3)}
    )
    ScheduleService(configured).prepare()
    service = BotService(configured, telegram.bot, FakeRenderer())

    assert service.load_targets() == [Target("-1009", 3)]

    service.add_target(FIRST, None)
    assert service.load_targets() == [Target("-1009", 3), Target(FIRST, None)]  # kept: it has its own place in the feed

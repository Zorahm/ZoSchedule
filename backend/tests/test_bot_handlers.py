"""The /go command and the bot's housekeeping in the chat, through a real Dispatcher.

Updates are genuine aiogram objects fed through the real routing and filters;
only Telegram itself (`FakeTelegram`) and the browser are replaced.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from aiogram import Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import DeleteMessage, LeaveChat
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)
from pydantic import SecretStr

from app.bot import store as bot_store
from app.bot import handlers
from app.bot.handlers import NOT_A_GROUP
from app.bot.runner import build_dispatcher
from app.bot.service import BotService
from app.config import AppConfig, BotConfig
from app.bot.store import Target
from app.db import connect
from app.snapshots.service import ScheduleService
from tests.fakes import BOT_ID, Clock, FakeRenderer, FakeTelegram, save_demo

TUESDAY = dt.date(2026, 9, 29)
SUNDAY = dt.date(2026, 9, 27)
GROUP_CHAT = -100777
TRUSTED = 7  # the default sender of `_go`
OTHER_TRUSTED = 9
STRANGER = 8
_WHEN = dt.datetime(2026, 9, 29, 10, 0, tzinfo=dt.UTC)


def _user(user_id: int, *, bot: bool = False) -> User:
    return User(id=user_id, is_bot=bot, first_name="Someone")


def _go(
    *,
    chat_type: str = "supergroup",
    text: str = "/go",
    thread: int | None = None,
    sender: int = 7,
    message_id: int = 900,
    update_id: int = 1,
    sender_chat: Chat | None = None,
) -> Update:
    message = Message(
        message_id=message_id,
        date=_WHEN,
        chat=Chat(id=GROUP_CHAT, type=chat_type),
        from_user=_user(sender),
        sender_chat=sender_chat,
        text=text,
        message_thread_id=thread,
        is_topic_message=True if thread is not None else None,
    )
    return Update(update_id=update_id, message=message)


@pytest.fixture
def fresh_config(config: AppConfig) -> AppConfig:
    """No chat configured: the bot has to be told /go."""
    bot = BotConfig(token=SecretStr("4242:TEST"), trusted_users=[TRUSTED, OTHER_TRUSTED])
    quiet = config.model_copy(update={"bot": bot})
    ScheduleService(quiet).prepare()
    return quiet


@pytest.fixture
def fresh_bot(fresh_config: AppConfig, telegram: FakeTelegram) -> BotService:
    return BotService(fresh_config, telegram.bot, FakeRenderer())


@pytest.fixture
def dispatcher(fresh_bot: BotService) -> Dispatcher:
    return build_dispatcher(fresh_bot)


async def test_go_by_an_admin_posts_the_week_then_the_day(
    dispatcher: Dispatcher,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)

    await dispatcher.feed_update(telegram.bot, _go(thread=2))

    assert telegram.kinds() == ["photo", "pin", "photo", "delete"]  # week, pin, day, own command
    assert telegram.captions[0].startswith("📅 Расписание на неделю")
    assert telegram.captions[1].startswith("📅 Сегодня · вторник")
    assert set(telegram.threads) == {2}  # everything went into the topic
    assert telegram.calls[-1] == ("delete", 900)  # the /go message itself was removed


async def test_a_stranger_gets_no_answer_and_nothing_happens(
    dispatcher: Dispatcher,
    fresh_bot: BotService,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)

    with caplog.at_level("WARNING"):
        await dispatcher.feed_update(telegram.bot, _go(sender=STRANGER))

    assert telegram.calls == []  # not a word, not even "no"
    assert fresh_bot.load_targets() == []  # the chat was not adopted
    assert f"пользователя {STRANGER}" in caplog.text  # but the owner can find the id in the log


async def test_an_anonymous_group_admin_is_not_trusted(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)

    # Writing "on behalf of the group" arrives without a person: nobody to trust.
    await dispatcher.feed_update(
        telegram.bot, _go(sender_chat=Chat(id=GROUP_CHAT, type="supergroup"), sender=STRANGER)
    )

    assert telegram.calls == []


async def test_every_trusted_person_may_run_the_bot(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)

    await dispatcher.feed_update(telegram.bot, _go(sender=OTHER_TRUSTED))

    assert "photo" in telegram.kinds()


async def test_go_remembers_the_chat_for_later_ticks(
    dispatcher: Dispatcher,
    fresh_bot: BotService,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    await fresh_bot.tick()  # no chat yet: silent
    assert telegram.calls == []

    await dispatcher.feed_update(telegram.bot, _go())
    telegram.calls.clear()
    at(dt.date(2026, 9, 30), "07:00")
    await fresh_bot.tick()  # next morning the usual cycle runs in that chat

    assert telegram.kinds()[0] in ("delete", "photo")
    assert "photo" in telegram.kinds()


async def test_go_in_the_evening_shows_tomorrow(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "22:30")  # today's lessons are over
    save_demo(fresh_config, TUESDAY)

    await dispatcher.feed_update(telegram.bot, _go())

    assert telegram.captions[1].startswith("📅 Завтра · среда")


async def test_go_skips_a_day_off_and_takes_the_next_working_day(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "12:00")
    save_demo(fresh_config, SUNDAY)

    await dispatcher.feed_update(telegram.bot, _go())

    assert telegram.captions[1].startswith("📅 Завтра · понедельник")  # Sunday has no lessons


async def test_go_in_a_private_chat_explains_itself_to_a_trusted_person(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _go(chat_type="private"))
    assert telegram.texts == [NOT_A_GROUP]


async def test_a_stranger_in_a_private_chat_is_ignored(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _go(chat_type="private", sender=STRANGER))
    assert telegram.calls == []


def _elsewhere(*, by: int, update_id: int) -> Update:
    message = Message(
        message_id=50,
        date=_WHEN,
        chat=Chat(id=-100999, type="supergroup"),
        from_user=_user(by),
        text="/go",
    )
    return Update(update_id=update_id, message=message)


async def test_a_trusted_go_in_another_group_adds_it_next_to_the_first(
    dispatcher: Dispatcher,
    fresh_bot: BotService,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    await dispatcher.feed_update(telegram.bot, _go(update_id=1))
    assert fresh_bot.load_targets() == [Target(str(GROUP_CHAT), None)]

    await dispatcher.feed_update(telegram.bot, _elsewhere(by=TRUSTED, update_id=2))

    assert fresh_bot.load_targets() == [
        Target(str(GROUP_CHAT), None),
        Target("-100999", None),
    ]  # two groups at once: the second did not push the first out


async def test_a_stranger_cannot_add_a_group_to_the_bot(
    dispatcher: Dispatcher,
    fresh_bot: BotService,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    await dispatcher.feed_update(telegram.bot, _go(update_id=1))
    telegram.calls.clear()

    await dispatcher.feed_update(telegram.bot, _elsewhere(by=STRANGER, update_id=2))

    assert fresh_bot.load_targets() == [Target(str(GROUP_CHAT), None)]  # only the first group
    assert telegram.calls == []


async def test_stop_removes_the_group_and_leaves_the_others(
    dispatcher: Dispatcher, fresh_bot: BotService, telegram: FakeTelegram
) -> None:
    fresh_bot.add_target(str(GROUP_CHAT), None)
    fresh_bot.add_target("-100999", None)

    await dispatcher.feed_update(telegram.bot, _go(text="/stop"))

    assert fresh_bot.load_targets() == [Target("-100999", None)]
    assert telegram.kinds() == ["message"]  # one quiet confirmation
    assert telegram.silent == [True]


async def test_stop_in_a_group_the_bot_does_not_post_to_says_so(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _go(text="/stop"))

    assert telegram.texts and "Начать: /go" in telegram.texts[0]


async def test_a_stranger_cannot_stop_the_bot(
    dispatcher: Dispatcher, fresh_bot: BotService, telegram: FakeTelegram
) -> None:
    fresh_bot.add_target(str(GROUP_CHAT), None)

    await dispatcher.feed_update(telegram.bot, _go(text="/stop", sender=STRANGER))

    assert fresh_bot.load_targets() == [Target(str(GROUP_CHAT), None)]
    assert telegram.calls == []


def _removed(*, chat_id: int, status: str = "kicked", update_id: int = 40) -> Update:
    user = _user(BOT_ID, bot=True)
    new = (
        ChatMemberBanned(status=ChatMemberStatus.KICKED, user=user, until_date=_WHEN)
        if status == "kicked"
        else ChatMemberLeft(status=ChatMemberStatus.LEFT, user=user)
    )
    event = ChatMemberUpdated(
        chat=Chat(id=chat_id, type="supergroup"),
        from_user=_user(STRANGER),
        date=_WHEN,
        old_chat_member=ChatMemberMember(status=ChatMemberStatus.MEMBER, user=user),
        new_chat_member=new,
    )
    return Update(update_id=update_id, my_chat_member=event)


@pytest.mark.parametrize("status", ["kicked", "left"])
async def test_a_group_the_bot_was_removed_from_is_forgotten(
    dispatcher: Dispatcher, fresh_bot: BotService, telegram: FakeTelegram, status: str
) -> None:
    fresh_bot.add_target(str(GROUP_CHAT), None)
    fresh_bot.add_target("-100999", None)

    await dispatcher.feed_update(telegram.bot, _removed(chat_id=GROUP_CHAT, status=status))

    assert fresh_bot.load_targets() == [Target("-100999", None)]
    assert telegram.calls == []  # nothing to say in a chat that no longer has the bot


async def test_leaving_a_strangers_group_does_not_disturb_the_known_ones(
    dispatcher: Dispatcher, fresh_bot: BotService, telegram: FakeTelegram
) -> None:
    fresh_bot.add_target(str(GROUP_CHAT), None)

    await dispatcher.feed_update(telegram.bot, _removed(chat_id=-100321, status="left"))

    assert fresh_bot.load_targets() == [Target(str(GROUP_CHAT), None)]


# -- being added to groups ------------------------------------------------------------------


def _administrator(user: User) -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        status=ChatMemberStatus.ADMINISTRATOR,
        user=user,
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_send_welcome_messages=False,
    )


def _added(
    *,
    by: int,
    chat_id: int = -100555,
    chat_type: str = "supergroup",
    was: str = "left",
    now: str = "member",
    update_id: int = 30,
) -> Update:
    user = _user(BOT_ID, bot=True)
    old = (
        ChatMemberLeft(status=ChatMemberStatus.LEFT, user=user)
        if was == "left"
        else ChatMemberMember(status=ChatMemberStatus.MEMBER, user=user)
    )
    new = (
        _administrator(user)
        if now == "administrator"
        else ChatMemberMember(status=ChatMemberStatus.MEMBER, user=user)
    )
    event = ChatMemberUpdated(
        chat=Chat(id=chat_id, type=chat_type),
        from_user=_user(by),
        date=_WHEN,
        old_chat_member=old,
        new_chat_member=new,
    )
    return Update(update_id=update_id, my_chat_member=event)


async def test_a_group_a_stranger_added_the_bot_to_is_left_at_once(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _added(by=STRANGER))

    assert telegram.calls == [("leave", -100555)]


async def test_a_group_a_trusted_person_added_the_bot_to_is_kept(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _added(by=TRUSTED))

    assert telegram.calls == []  # it waits there for that person's /go


@pytest.fixture
def whitelisted_dispatcher(fresh_config: AppConfig, telegram: FakeTelegram) -> Dispatcher:
    bot = fresh_config.bot.model_copy(update={"trusted_chats": [-100555, -100777]})
    config = fresh_config.model_copy(update={"bot": bot})
    return build_dispatcher(BotService(config, telegram.bot, FakeRenderer()))


@pytest.mark.parametrize("chat_id", [-100555, -100777])
async def test_a_whitelisted_group_is_kept_even_if_a_stranger_added_the_bot(
    whitelisted_dispatcher: Dispatcher, telegram: FakeTelegram, chat_id: int
) -> None:
    await whitelisted_dispatcher.feed_update(telegram.bot, _added(by=STRANGER, chat_id=chat_id))

    assert telegram.calls == []


async def test_a_group_outside_the_whitelist_is_still_left(
    whitelisted_dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await whitelisted_dispatcher.feed_update(telegram.bot, _added(by=STRANGER, chat_id=-100999))

    assert telegram.calls == [("leave", -100999)]


async def test_a_stranger_still_cannot_command_the_bot_in_a_whitelisted_group(
    fresh_config: AppConfig, telegram: FakeTelegram
) -> None:
    bot = fresh_config.bot.model_copy(update={"trusted_chats": [GROUP_CHAT]})
    config = fresh_config.model_copy(update={"bot": bot})
    dispatcher = build_dispatcher(BotService(config, telegram.bot, FakeRenderer()))

    await dispatcher.feed_update(telegram.bot, _go(sender=STRANGER))

    with connect(config.db_path) as conn:
        assert bot_store.targets(conn) == []
    assert telegram.calls == []


async def test_a_promotion_in_a_group_the_bot_already_belongs_to_is_not_being_added(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    # Already a member; a stranger with admin rights makes it an administrator.
    await dispatcher.feed_update(
        telegram.bot, _added(by=STRANGER, was="member", now="administrator")
    )

    assert telegram.calls == []


async def test_being_added_to_a_channel_or_a_private_chat_changes_nothing(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(
        telegram.bot, _added(by=STRANGER, chat_type="channel", update_id=31)
    )
    await dispatcher.feed_update(
        telegram.bot, _added(by=STRANGER, chat_type="private", update_id=32)
    )

    assert telegram.calls == []


async def test_failing_to_leave_is_logged_not_a_crash(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    forbidden = TelegramForbiddenError(LeaveChat(chat_id=-100555), "Forbidden: bot was kicked")
    telegram.fail(LeaveChat, forbidden)

    await dispatcher.feed_update(telegram.bot, _added(by=STRANGER))

    assert telegram.tried == ["LeaveChat"]


@pytest.mark.parametrize("text", ["/go@other_bot", "привет", "/start"])
async def test_what_is_not_ours_is_ignored(
    dispatcher: Dispatcher, telegram: FakeTelegram, text: str
) -> None:
    await dispatcher.feed_update(telegram.bot, _go(text=text))
    assert telegram.calls == []


async def test_go_addressed_to_this_bot_works(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    await dispatcher.feed_update(telegram.bot, _go(text="/go@ZoScheduleBot"))
    assert "photo" in telegram.kinds()


async def test_go_without_any_schedule_says_so(
    dispatcher: Dispatcher, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    await dispatcher.feed_update(telegram.bot, _go())
    assert telegram.texts and "нет расписания" in telegram.texts[0]


async def test_go_works_even_if_the_command_cannot_be_deleted(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    telegram.fail(
        DeleteMessage,
        TelegramForbiddenError(DeleteMessage(chat_id=GROUP_CHAT, message_id=900), "Forbidden"),
    )

    await dispatcher.feed_update(telegram.bot, _go())

    assert "photo" in telegram.kinds()


async def test_go_again_replaces_the_previous_posts(
    dispatcher: Dispatcher,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    await dispatcher.feed_update(telegram.bot, _go())
    first_week, first_day = telegram.calls[0][1], telegram.calls[2][1]

    await dispatcher.feed_update(telegram.bot, _go(message_id=901, update_id=2))

    assert ("delete", first_week) in telegram.calls and ("delete", first_day) in telegram.calls
    with connect(fresh_config.db_path) as conn:
        assert len(bot_store.all_of_kind(conn, chat_id=str(GROUP_CHAT), kind="week")) == 1
        assert len(bot_store.all_of_kind(conn, chat_id=str(GROUP_CHAT), kind="today")) == 1


# -- the "pinned a message" notice ------------------------------------------


def _pin_notice(*, by: int, pinned: int, notice_id: int = 950, update_id: int = 2) -> Update:
    chat = Chat(id=GROUP_CHAT, type="supergroup")
    message = Message(
        message_id=notice_id,
        date=_WHEN,
        chat=chat,
        from_user=_user(by, bot=by == BOT_ID),
        pinned_message=Message(message_id=pinned, date=_WHEN, chat=chat),
    )
    return Update(update_id=update_id, message=message)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "_DELETE_PAUSE", 0.0)


def _delete() -> DeleteMessage:
    return DeleteMessage(chat_id=GROUP_CHAT, message_id=950)


async def test_the_bots_own_pin_notice_is_deleted(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=500))

    assert telegram.calls == [("delete", 950)]


async def test_the_notice_goes_even_if_the_bots_ledger_never_heard_of_the_picture(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram
) -> None:
    # Pinned by another process (a simulation, a manual post-week) or already retired
    # by a second /go before its notice was read: the ledger has no such week.
    with connect(fresh_config.db_path) as conn:
        assert bot_store.all_of_kind(conn, chat_id=str(GROUP_CHAT), kind="week") == []

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=12345))

    assert telegram.calls == [("delete", 950)]


async def test_a_persons_pin_notice_is_left_alone(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    await dispatcher.feed_update(telegram.bot, _pin_notice(by=777, pinned=500))

    assert telegram.calls == [] and telegram.tried == []


async def test_a_network_blip_does_not_leave_the_notice_for_good(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    telegram.fail(DeleteMessage, TelegramNetworkError(_delete(), "Request timeout error"), times=2)

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=500))

    assert telegram.tried == ["DeleteMessage"] * 3 and telegram.calls == [("delete", 950)]


async def test_flood_control_is_waited_out(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    flood = TelegramRetryAfter(_delete(), "flood", 0)
    telegram.fail(DeleteMessage, flood, times=1)

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=500))

    assert telegram.calls == [("delete", 950)]


async def test_a_refusal_is_not_retried_and_does_not_break_the_bot(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    telegram.fail(DeleteMessage, TelegramForbiddenError(_delete(), "Forbidden"))  # no right to delete

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=500))

    assert telegram.tried == ["DeleteMessage"]  # once: it will not fix itself


async def test_giving_up_after_the_last_attempt_is_not_a_crash(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    telegram.fail(DeleteMessage, TelegramNetworkError(_delete(), "Telegram is down"))

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=500))

    assert telegram.tried == ["DeleteMessage"] * 3


async def test_a_notice_that_is_already_gone_is_fine(
    dispatcher: Dispatcher, telegram: FakeTelegram
) -> None:
    gone = TelegramBadRequest(_delete(), "Bad Request: message to delete not found")
    telegram.fail(DeleteMessage, gone)

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=500))

    assert telegram.tried == ["DeleteMessage"]


async def test_the_notice_of_a_first_go_survives_a_second_go_that_retires_its_week(
    dispatcher: Dispatcher, fresh_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    """The race behind the notices left in the chat: /go twice, the first pin's notice read last."""
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    await dispatcher.feed_update(telegram.bot, _go(update_id=1))
    first_week = int(telegram.calls[0][1])
    await dispatcher.feed_update(telegram.bot, _go(message_id=901, update_id=2))
    with connect(fresh_config.db_path) as conn:  # the first week is already forgotten
        assert first_week not in [
            m.message_id for m in bot_store.all_of_kind(conn, chat_id=str(GROUP_CHAT), kind="week")
        ]
    telegram.calls.clear()

    await dispatcher.feed_update(telegram.bot, _pin_notice(by=BOT_ID, pinned=first_week, update_id=10))

    assert telegram.calls == [("delete", 950)]


# -- the offset that survives a restart -------------------------------------


async def test_a_handled_update_is_remembered_and_not_replayed(
    dispatcher: Dispatcher,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)

    await dispatcher.feed_update(telegram.bot, _go(update_id=41))
    with connect(fresh_config.db_path) as conn:
        assert bot_store.update_offset(conn) == 42

    posted = len(telegram.calls)
    # A restart hands the last batch out again: the same /go must not run twice.
    await dispatcher.feed_update(telegram.bot, _go(update_id=41))
    assert len(telegram.calls) == posted


async def test_real_polling_runs_startup_handles_a_command_and_stops(
    dispatcher: Dispatcher,
    fresh_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
) -> None:
    at(TUESDAY, "10:00")
    save_demo(fresh_config, TUESDAY)
    telegram.updates = [[_go(update_id=41)]]

    polling = asyncio.create_task(
        dispatcher.start_polling(  # pyright: ignore[reportUnknownMemberType]
            telegram.bot,
            allowed_updates=["message"],
            handle_as_tasks=False,
            handle_signals=False,
            close_bot_session=False,
        )
    )
    try:
        async with asyncio.timeout(5):
            while "photo" not in telegram.kinds():
                await asyncio.sleep(0.02)
    finally:
        await dispatcher.stop_polling()
        await polling

    with connect(fresh_config.db_path) as conn:
        assert bot_store.update_offset(conn) == 42  # a restart will not replay /go

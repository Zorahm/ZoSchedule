"""The Telegram bot: texts, layout and the whole post/edit/delete cycle.

Telegram and the browser are replaced by fakes; the database is real (temporary).
Time is pinned by patching `moscow.now`, which `moscow.today` reads too.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import DeleteMessage, EditMessageMedia, SendPhoto, UnpinChatMessage
from aiogram.types import InputMediaPhoto
from pydantic import SecretStr

from app import moscow
from app.render import templates
from app.bot.dev import demo
from app import texts
from app.bot import store as bot_store
from app.render.pictures import PictureBuilder, target_monday
from app.render.renderer import PlaywrightRenderer, find_browser
from app.bot.service import BotService
from app.render.view import build_days
from app.config import AppConfig, BotConfig
from app.models.changes import Added, Cancelled, Moved, TeacherChanged
from app.db import connect
from app.models.domain import Lesson
from app.snapshots import store
from app.snapshots.service import RefreshOutcome, ScheduleService
from tests.fakes import Clock, FakeRenderer, FakeTelegram, save_demo

CHAT = "-100500"
GROUP = "ОККИПд-307"
_DELETE = DeleteMessage(chat_id=CHAT, message_id=7)
SUNDAY = dt.date(2026, 9, 27)
TUESDAY = dt.date(2026, 9, 29)
NEXT_SUNDAY = dt.date(2026, 10, 4)

@pytest.fixture
def bot_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("token"), chat_id=CHAT)
    return config.model_copy(update={"bot": bot})


@pytest.fixture
def bot(bot_config: AppConfig, telegram: FakeTelegram) -> BotService:
    ScheduleService(bot_config).prepare()
    return BotService(bot_config, telegram.bot, FakeRenderer())


# -- texts ------------------------------------------------------------------


def test_plural_follows_russian_rules() -> None:
    assert [texts.lessons_count(n) for n in (1, 2, 5, 11, 21)] == [
        "1 пара",
        "2 пары",
        "5 пар",
        "11 пар",
        "21 пара",
    ]


def test_range_collapses_inside_one_month() -> None:
    assert texts.range_long(dt.date(2026, 10, 5), dt.date(2026, 10, 10)) == "5–10 октября"
    assert texts.range_long(dt.date(2026, 9, 28), dt.date(2026, 10, 3)) == "28 сентября – 3 октября"


def test_room_word_only_for_numbers() -> None:
    assert texts.format_room("208") == "ауд. 208"
    assert texts.format_room("Спортзал") == "Спортзал"
    assert texts.format_room("  ") is None


def _events() -> list[Moved | Cancelled | Added | TeacherChanged]:
    when = dt.datetime(2026, 9, 29, 9, 0, tzinfo=moscow.MOSCOW)
    day = dt.date(2026, 9, 30)
    return [
        Moved(id=1, detected_at=when, date=day, discipline="Физика", from_time="10:20-11:50",
              to_time="12:10-13:40", from_room="112", to_room="205"),
        Cancelled(id=2, detected_at=when, date=day, discipline="Химия", at_time="13:50-15:20"),
        Added(id=3, detected_at=when, date=day, discipline="Философия", at_time="15:30-17:00",
              room="210", teacher="Кузнецов А. А."),
        TeacherChanged(id=4, detected_at=when, date=day, discipline="БД", at_time="09:00-10:30",
                       from_teacher=None, to_teacher="Орлов П. П."),
    ]


def test_change_message_describes_every_kind() -> None:
    (message,) = texts.format_changes(_events())
    assert message.count("Среда, 30 сентября") == 1  # one day header for all four
    assert "Перенос</b>: Физика" in message
    assert "Было: 10:20–11:50, ауд. 112" in message and "Стало: 12:10–13:40, ауд. 205" in message
    assert "Отменена</b>: Химия (13:50)" in message
    assert "Добавлена</b>: Философия" in message and "15:30–17:00 · ауд. 210 · Кузнецов А. А." in message
    assert "Было: не указан на сайте" in message and "Стало: Орлов П. П." in message


def test_room_only_change_says_so_and_names_the_time() -> None:
    event = Moved(
        id=1,
        detected_at=dt.datetime(2026, 9, 29, tzinfo=moscow.MOSCOW),
        date=dt.date(2026, 9, 30),
        discipline="Физика",
        from_time="10:20-11:50",
        to_time="10:20-11:50",
        from_room="112",
        to_room="205",
    )
    (message,) = texts.format_changes([event])
    assert "Другая аудитория</b>: Физика (10:20)" in message
    assert "Было: ауд. 112" in message and "Стало: ауд. 205" in message
    assert "10:20–11:50" not in message  # the time did not change, so it is not a was/now


def test_changes_of_different_days_get_their_own_headers() -> None:
    when = dt.datetime(2026, 9, 29, tzinfo=moscow.MOSCOW)
    events = [
        Cancelled(id=1, detected_at=when, date=dt.date(2026, 10, 2), discipline="Б", at_time="08:30-10:00"),
        Cancelled(id=2, detected_at=when, date=dt.date(2026, 9, 30), discipline="А", at_time="08:30-10:00"),
    ]
    (message,) = texts.format_changes(events)
    assert message.index("Среда, 30 сентября") < message.index("Пятница, 2 октября")


def test_change_message_escapes_site_text() -> None:
    event = Cancelled(
        id=1,
        detected_at=dt.datetime(2026, 9, 29, tzinfo=moscow.MOSCOW),
        date=dt.date(2026, 9, 30),
        discipline="<b>Химия</b> & Ко",
        at_time="08:30-10:00",
    )
    (message,) = texts.format_changes([event])
    assert "&lt;b&gt;Химия&lt;/b&gt; &amp; Ко" in message


def test_long_batch_splits_between_events() -> None:
    day = dt.date(2026, 9, 30)
    when = dt.datetime(2026, 9, 29, tzinfo=moscow.MOSCOW)
    many = [
        Cancelled(id=i, detected_at=when, date=day, discipline=f"Предмет {i} " + "х" * 120,
                  at_time="08:30-10:00")
        for i in range(60)
    ]
    messages = texts.format_changes(many)
    assert len(messages) > 1
    assert all(len(message) <= 4096 for message in messages)
    assert all(message.startswith(texts.CHANGES_TITLE) for message in messages)
    assert all("📅" in message for message in messages)  # a continuation still names its day


@pytest.mark.parametrize(
    ("full", "short"),
    [
        ("Иностранный язык в профессиональной деятельности", "Иностранный язык"),
        ("Иностранный язык", "Иностранный язык"),
        ("Дискретная математика с элементами математической логики", "Дискретная математика"),
        ("Проектирование и дизайн информационных систем", "Проектирование и дизайн ИС"),
        ("Внедрение информационных систем", "Внедрение ИС"),
        ("Разработка кода информационных систем", "Разработка кода ИС"),
        ("Информационная система предприятия", "ИС предприятия"),
        ("Теория информационных систем и процессов", "Теория ИС и процессов"),
        ("Теория вероятностей и математическая статистика", "Теория вероятностей"),
        ("Физическая культура/ Адаптивная физическая культура", "Физкультура"),
        ("Физическая культура и спорт", "Физкультура"),
        ("Менеджмент в профессиональной деятельности", "Менеджмент в проф. деятельности"),
        (
            "Инженерно-техническая поддержка сопровождения информационных систем",
            "Техподдержка сопровождения ИС",
        ),
        (
            "Производственная практика | ПМ.04 | Сопровождение информационных систем",
            "Производственная практика",
        ),
        ("Технологическая (проектно-технологическая) практика", "Технологическая практика"),
        ("Математический анализ", "Математический анализ"),  # nothing to shorten
        ("Компьютерные сети", "Компьютерные сети"),
    ],
)
def test_long_titles_are_shortened_for_pictures(full: str, short: str) -> None:
    assert texts.short_title(full) == short


def test_only_the_week_picture_shortens_titles(bot_config: AppConfig) -> None:
    from app.render.view import Header

    save_demo(bot_config, TUESDAY)
    with connect(bot_config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        lessons = store.load_lessons(conn, latest.id)
    week = build_days(lessons, latest, dt.date(2026, 9, 28), 6, group=GROUP)
    header = Header("ОККИПд-307", TUESDAY)
    long_title = "Иностранный язык в профессиональной деятельности"

    week_page = templates.week_html(week, header)
    day_page = templates.day_html(week[1], week, header)  # Tuesday has the language lesson

    assert long_title not in week_page and ">Иностранный язык<" in week_page
    # the day picture keeps the full name (short words glued to the next with nbsp)
    assert long_title.replace("в ", "в ") in day_page


def test_change_texts_keep_the_full_title() -> None:
    event = Cancelled(
        id=1,
        detected_at=dt.datetime(2026, 9, 29, tzinfo=moscow.MOSCOW),
        date=dt.date(2026, 9, 30),
        discipline="Внедрение информационных систем",
        at_time="08:30-10:00",
    )
    (message,) = texts.format_changes([event])
    assert "Внедрение информационных систем" in message  # exact, as on the site


def test_no_events_no_messages() -> None:
    assert texts.format_changes([]) == []


# -- layout -----------------------------------------------------------------


def test_week_starts_from_the_coming_monday_on_sunday() -> None:
    assert target_monday(SUNDAY) == dt.date(2026, 9, 28)
    assert target_monday(TUESDAY) == dt.date(2026, 9, 28)


def test_unpublished_and_day_off_are_told_apart(bot_config: AppConfig) -> None:
    save_demo(bot_config, TUESDAY)
    with connect(bot_config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        lessons = store.load_lessons(conn, latest.id)
    days = build_days(lessons, latest, dt.date(2026, 10, 3), 2, group=GROUP)  # Sat, then Sun inside the range
    assert days[0].coverage == "published" and days[0].lessons
    assert days[1].coverage == "published" and not days[1].lessons  # a day off

    beyond = build_days(lessons, latest, dt.date(2026, 10, 11), 1, group=GROUP)  # past the last published day
    assert beyond[0].coverage == "unpublished"


def test_day_template_shows_lessons_and_stream(bot_config: AppConfig) -> None:
    save_demo(bot_config, TUESDAY)
    with connect(bot_config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        lessons = store.load_lessons(conn, latest.id)
    week = build_days(lessons, latest, dt.date(2026, 9, 28), 6, group=GROUP)
    from app.render.view import Header

    html = templates.day_html(week[1], week, Header("ОККИПд-307", TUESDAY))
    assert "Вторник" in html and "Базы данных" in html
    assert "гр. 306" in html  # the tag on the stream lecture
    assert "пара вместе с группой ОККИПд-306" in html  # and its legend, with the full name
    assert "не указан на сайте" in html  # lesson without teacher


# -- posting ----------------------------------------------------------------


async def test_week_is_posted_pinned_and_not_duplicated(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)

    assert await bot.post_week() is True
    assert await bot.post_week() is False  # already posted for this week
    assert telegram.kinds() == ["photo", "pin"]


async def test_new_week_replaces_the_old_one(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.post_week()
    first = telegram.calls[0][1]

    at(NEXT_SUNDAY)
    await bot.post_week()

    assert ("unpin", first) in telegram.calls and ("delete", first) in telegram.calls
    with connect(bot_config.db_path) as conn:
        remaining = bot_store.all_of_kind(conn, chat_id=CHAT, kind="week")
    assert [m.day for m in remaining] == [dt.date(2026, 10, 5)]


async def test_old_week_is_deleted_even_if_unpin_is_refused(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.post_week()
    first = telegram.calls[0][1]

    # The bot may have the right to delete but not to pin: the old week must still go.
    refused = TelegramBadRequest(
        UnpinChatMessage(chat_id=CHAT, message_id=first),
        "Bad Request: not enough rights to manage pinned messages in the chat",
    )
    telegram.fail(UnpinChatMessage, refused)
    at(NEXT_SUNDAY)
    await bot.post_week()

    assert ("delete", first) in telegram.calls
    with connect(bot_config.db_path) as conn:
        remaining = bot_store.all_of_kind(conn, chat_id=CHAT, kind="week")
    assert [m.day for m in remaining] == [dt.date(2026, 10, 5)]


async def test_today_skipped_on_day_off_and_when_unpublished(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "08:00")
    save_demo(bot_config, SUNDAY)
    assert await bot.post_today() is False  # Sunday: no lessons

    at(dt.date(2026, 11, 20), "08:00")  # far beyond the published range
    assert await bot.post_today() is False
    assert telegram.calls == []


async def test_today_posted_once_and_yesterdays_messages_removed(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY)
    save_demo(bot_config, TUESDAY)
    assert await bot.post_today() is True
    assert await bot.post_today() is False
    yesterday_photo = telegram.calls[0][1]

    with connect(bot_config.db_path) as conn:
        bot_store.record(conn, chat_id=CHAT, kind="changes", day=TUESDAY, message_id=555)

    at(dt.date(2026, 9, 30))
    await bot.cleanup(dt.date(2026, 9, 30))

    assert ("delete", yesterday_photo) in telegram.calls and ("delete", 555) in telegram.calls
    with connect(bot_config.db_path) as conn:
        assert bot_store.all_of_kind(conn, chat_id=CHAT, kind="today") == []
        assert bot_store.all_of_kind(conn, chat_id=CHAT, kind="changes") == []


async def test_unreachable_telegram_keeps_the_record_for_a_retry(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY)
    with connect(bot_config.db_path) as conn:
        bot_store.record(conn, chat_id=CHAT, kind="today", day=TUESDAY, message_id=7)

    telegram.fail(DeleteMessage, TelegramNetworkError(_DELETE, "Request timeout error"))
    await bot.cleanup(dt.date(2026, 9, 30))
    with connect(bot_config.db_path) as conn:
        assert len(bot_store.all_of_kind(conn, chat_id=CHAT, kind="today")) == 1

    # A definite answer from Telegram (message gone, no rights) ends the matter.
    telegram.fail(DeleteMessage, TelegramForbiddenError(_DELETE, "Forbidden: bot was kicked"))
    await bot.cleanup(dt.date(2026, 9, 30))
    with connect(bot_config.db_path) as conn:
        assert bot_store.all_of_kind(conn, chat_id=CHAT, kind="today") == []


# -- changes ----------------------------------------------------------------


async def test_history_is_not_announced_on_first_run(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY)
    save_demo(bot_config, TUESDAY)
    _change(bot_config, TUESDAY)  # an event exists before the bot ever ran

    assert await bot.announce_changes() == 0
    assert telegram.texts == []


def _change(config: AppConfig, today: dt.date) -> int:
    with connect(config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        current = store.load_lessons(conn, latest.id)
    return ScheduleService(config).store_lessons(demo.apply_changes(current, today))


async def test_new_changes_are_announced_once_and_pictures_redrawn(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.announce_changes()  # baseline
    await bot.post_week()
    at(TUESDAY)
    await bot.post_today()
    telegram.calls.clear()

    assert _change(bot_config, TUESDAY) == 4

    assert await bot.announce_changes() == 4
    assert await bot.announce_changes() == 0  # nothing new the second time
    assert len(telegram.texts) == 1 and "Изменения в расписании" in telegram.texts[0]

    assert await bot.sync_pictures() == 2  # the week and today both changed
    assert telegram.kinds().count("edit") == 2
    assert await bot.sync_pictures() == 0  # now in sync


async def test_a_picture_deleted_in_the_chat_is_forgotten_and_does_not_block_the_others(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.announce_changes()  # baseline
    await bot.post_week()
    at(TUESDAY)
    await bot.post_today()
    telegram.calls.clear()
    _change(bot_config, TUESDAY)
    lost = TelegramBadRequest(
        EditMessageMedia(media=InputMediaPhoto(media="x"), chat_id=CHAT, message_id=1),
        "Bad Request: message to edit not found",
    )
    telegram.fail(EditMessageMedia, lost, times=1)  # the week's picture is gone, today's is there

    assert await bot.sync_pictures() == 1  # today's still got its edit

    with connect(bot_config.db_path) as conn:
        assert bot_store.all_of_kind(conn, chat_id=CHAT, kind="week") == []  # forgotten
        assert len(bot_store.all_of_kind(conn, chat_id=CHAT, kind="today")) == 1
    assert await bot.sync_pictures() == 0  # and nothing left to fail on


async def test_other_edit_errors_still_surface(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.announce_changes()
    await bot.post_week()
    _change(bot_config, SUNDAY)
    telegram.fail(
        EditMessageMedia,
        TelegramBadRequest(
            EditMessageMedia(media=InputMediaPhoto(media="x"), chat_id=CHAT, message_id=1),
            "Bad Request: chat not found",
        ),
    )

    with pytest.raises(TelegramBadRequest):
        await bot.sync_pictures()
    with connect(bot_config.db_path) as conn:
        assert len(bot_store.all_of_kind(conn, chat_id=CHAT, kind="week")) == 1  # kept


async def test_events_in_the_past_are_not_announced(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.announce_changes()
    _change(bot_config, SUNDAY)

    at(dt.date(2026, 10, 20))  # every changed lesson is long gone
    assert await bot.announce_changes() == 0
    assert telegram.texts == []


async def test_only_this_weeks_changes_are_announced(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY)
    save_demo(bot_config, TUESDAY)
    await bot.announce_changes()  # baseline

    def lesson_on(day: dt.date) -> Lesson:
        with connect(bot_config.db_path) as conn:
            latest = store.latest_ok(conn)
            assert latest is not None
            return next(item for item in store.load_lessons(conn, latest.id) if item.date == day)

    this_week = lesson_on(dt.date(2026, 10, 1))  # Thursday, same week as TUESDAY
    later = lesson_on(dt.date(2026, 10, 8))  # next week
    with connect(bot_config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        current = store.load_lessons(conn, latest.id)
    changed = [
        item.model_copy(update={"teacher": "Новиков Д. Е."})
        if item.source_id in (this_week.source_id, later.source_id)
        else item
        for item in current
    ]
    assert ScheduleService(bot_config).store_lessons(changed) == 2

    assert await bot.announce_changes() == 1
    assert "Четверг, 1 октября" in telegram.texts[0]
    assert "8 октября" not in telegram.texts[0]
    assert await bot.announce_changes() == 0  # the far one is not queued for later


async def test_on_sunday_the_window_is_the_coming_week(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.announce_changes()  # baseline
    assert _change(bot_config, dt.date(2026, 9, 28)) == 4  # all on Monday 28.09

    assert await bot.announce_changes() == 4


async def test_a_day_published_later_updates_the_picture_without_events(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    """The diff ignores newly published days; the fingerprint must catch them."""
    at(SUNDAY)
    partial = [lesson for lesson in demo.build_schedule(SUNDAY) if lesson.date < dt.date(2026, 10, 1)]
    ScheduleService(bot_config).store_lessons(partial)
    await bot.post_week()  # Thursday..Saturday not published yet
    telegram.calls.clear()

    ScheduleService(bot_config).store_lessons(demo.build_schedule(SUNDAY))
    assert await bot.sync_pictures() == 1


async def test_changing_the_look_redraws_pictures_already_posted(
    bot: BotService,
    bot_config: AppConfig,
    telegram: FakeTelegram,
    at: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new abbreviation rule must reach the picture in the chat, with no data change."""
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.post_week()
    telegram.calls.clear()
    assert await bot.sync_pictures() == 0

    rules = (*texts._TITLE_RULES, (re.compile("Веб-программирование"), "Веб"))  # noqa: SLF001
    monkeypatch.setattr(texts, "_TITLE_RULES", rules)

    assert await bot.sync_pictures() == 1
    assert telegram.kinds() == ["edit"]


async def test_after_an_update_a_restarted_bot_edits_what_is_posted_and_posts_nothing_again(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    """Updating the code on a bot that is already running in a chat."""
    at(TUESDAY, "07:00")
    save_demo(bot_config, TUESDAY)
    before = BotService(bot_config, telegram.bot, FakeRenderer())
    await before.post_week(force=True)
    await before.post_today()
    with connect(bot_config.db_path) as conn:
        # What the old version left behind: pictures drawn by other code, other fingerprints.
        conn.execute("UPDATE bot_messages SET fingerprint = 'drawn-by-the-old-version'")
        ledger = [(m.kind, m.message_id) for k in ("week", "today") for m in bot_store.all_of_kind(conn, chat_id=CHAT, kind=k)]
    telegram.calls.clear()

    restarted = BotService(bot_config, telegram.bot, FakeRenderer())  # the process after the update
    at(TUESDAY, "07:01")
    await restarted.tick()

    assert telegram.kinds() == ["edit", "edit"]  # the week and today, in place: no new posts, no pin
    await restarted.tick()
    assert telegram.kinds() == ["edit", "edit"]  # and then quiet
    with connect(bot_config.db_path) as conn:
        after = [(m.kind, m.message_id) for k in ("week", "today") for m in bot_store.all_of_kind(conn, chat_id=CHAT, kind=k)]
    assert after == ledger  # the same messages, nothing re-posted


async def test_a_restart_at_the_posting_time_does_not_post_twice(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "07:00")
    save_demo(bot_config, SUNDAY)
    await BotService(bot_config, telegram.bot, FakeRenderer()).tick()
    assert telegram.kinds() == ["photo", "pin"]  # the week, posted on time

    at(SUNDAY, "07:02")
    await BotService(bot_config, telegram.bot, FakeRenderer()).tick()  # restarted two minutes later

    assert telegram.kinds() == ["photo", "pin"]


async def _post_everything(config: AppConfig, telegram: FakeTelegram, at: Clock) -> None:
    """A week, a day and a change text: every kind of message the bot sends by itself."""
    at(SUNDAY)
    save_demo(config, SUNDAY)
    service = BotService(config, telegram.bot, FakeRenderer())
    await service.announce_changes()  # baseline
    await service.post_week()
    at(TUESDAY)
    await service.post_today()
    _change(config, TUESDAY)
    assert await service.announce_changes() == 4


async def test_by_default_the_bot_posts_with_sound(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    await _post_everything(bot_config, telegram, at)

    assert len(telegram.silent) == 3  # week, day, changes
    assert not any(telegram.silent)  # None: the flag is not even sent


async def test_in_silent_mode_every_post_goes_without_sound(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    quiet = bot_config.model_copy(
        update={"bot": bot_config.bot.model_copy(update={"silent": True})}
    )

    await _post_everything(quiet, telegram, at)

    assert telegram.silent == [True, True, True]  # the week, the day and the change text


async def test_past_days_dropped_by_the_site_stay_on_the_picture(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    save_demo(bot_config, SUNDAY)
    await bot.post_week()
    telegram.calls.clear()

    at(dt.date(2026, 9, 30))  # Wednesday: the site now starts from today
    trimmed = [lesson for lesson in demo.build_schedule(SUNDAY) if lesson.date >= dt.date(2026, 9, 30)]
    ScheduleService(bot_config).store_lessons(trimmed)

    assert await bot.sync_pictures() == 0  # Monday and Tuesday did not turn into holes


# -- loop -------------------------------------------------------------------


async def test_a_failing_job_backs_off_instead_of_hammering(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY)
    save_demo(bot_config, TUESDAY)

    photo = SendPhoto(chat_id=CHAT, photo="x")
    telegram.fail(SendPhoto, TelegramBadRequest(photo, "Bad Request: chat not found"))
    await bot.tick()
    await bot.tick()  # inside the retry window: must not try again

    assert telegram.calls == []


async def test_tick_stays_quiet_before_the_morning_time(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "06:59")
    save_demo(bot_config, TUESDAY)
    await bot.tick()
    assert telegram.calls == []

    at(TUESDAY, "07:00")
    await bot.tick()
    assert telegram.kinds() == ["photo"]  # today's picture; the week waits for Sunday
    await bot.tick()
    assert telegram.kinds() == ["photo"]  # and only once


async def test_tick_does_not_post_a_day_that_is_already_over(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "22:00")  # e.g. the bot was restarted in the evening
    save_demo(bot_config, TUESDAY)
    await bot.tick()
    assert telegram.calls == []

    at(TUESDAY, "15:00")  # the last lesson ends at 15:20: still worth posting
    await bot.tick()
    assert telegram.kinds() == ["photo"]


async def test_tick_posts_the_week_on_sunday_only(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "07:00")
    save_demo(bot_config, SUNDAY)
    await bot.tick()
    assert telegram.kinds() == ["photo", "pin"]  # the week; Sunday has no "today"


async def test_sunday_week_goes_before_mondays_day(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    bot_config.bot.day_ahead = True
    at(SUNDAY, "07:00")
    save_demo(bot_config, SUNDAY)
    await bot.tick()
    assert telegram.kinds() == ["photo", "pin", "photo"]  # the week, then Monday


class FakeSchedule:
    """Stands in for ScheduleService: every run leaves an attempt in the database."""

    def __init__(self, config: AppConfig, *, ok: bool = True) -> None:
        self._config = config
        self.runs = 0
        self._ok = ok

    async def refresh(self) -> RefreshOutcome:
        self.runs += 1
        with connect(self._config.db_path) as conn:
            store.save_failure(conn, taken_at=moscow.now(), error="fake attempt")
        return RefreshOutcome(ok=self._ok, changes_detected=0, error=None if self._ok else "down")


def _with_refresh(config: AppConfig, telegram: FakeTelegram, *, ok: bool = True) -> tuple[BotService, FakeSchedule]:
    ScheduleService(config).prepare()
    schedule = FakeSchedule(config, ok=ok)
    return BotService(config, telegram.bot, FakeRenderer(), schedule), schedule


async def test_bot_polls_the_site_on_the_poll_interval(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "01:00")
    save_demo(bot_config, SUNDAY)  # the last snapshot is from 01:00
    bot, schedule = _with_refresh(bot_config, telegram)

    at(SUNDAY, "03:00")  # before the morning posts, so only the interval rule acts
    await bot.tick()
    assert schedule.runs == 1  # the last attempt is two hours old
    at(SUNDAY, "03:30")
    await bot.tick()
    assert schedule.runs == 1  # 30 min < the 60 min interval
    at(SUNDAY, "04:01")
    await bot.tick()
    assert schedule.runs == 2


async def test_first_post_of_the_day_takes_a_fresh_snapshot_once(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "04:00")
    save_demo(bot_config, TUESDAY)
    bot, schedule = _with_refresh(bot_config, telegram)
    at(TUESDAY, "06:50")
    await bot.tick()  # the interval poll at 06:50
    assert schedule.runs == 1

    at(TUESDAY, "07:00")  # only 10 min later, but the morning post forces a refresh
    await bot.tick()
    assert schedule.runs == 2 and telegram.kinds() == ["photo"]

    at(TUESDAY, "07:01")
    await bot.tick()
    assert schedule.runs == 2  # once per day, not once per minute


async def test_week_on_sunday_is_preceded_by_a_fresh_snapshot(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "07:00")
    save_demo(bot_config, SUNDAY)
    bot, schedule = _with_refresh(bot_config, telegram)

    await bot.tick()

    assert schedule.runs >= 1 and telegram.kinds() == ["photo", "pin"]


async def test_site_failure_does_not_stop_the_post(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "07:00")
    save_demo(bot_config, TUESDAY)
    bot, _ = _with_refresh(bot_config, telegram, ok=False)

    await bot.tick()

    assert telegram.kinds() == ["photo"]  # posted from the last good snapshot


async def test_refresh_can_be_switched_off(bot_config: AppConfig, telegram: FakeTelegram, at: Clock) -> None:
    at(TUESDAY, "07:00")
    save_demo(bot_config, TUESDAY)
    off = bot_config.model_copy(update={"bot": bot_config.bot.model_copy(update={"refresh": False})})
    bot, schedule = _with_refresh(off, telegram)

    await bot.tick()

    assert schedule.runs == 0


# -- rendering --------------------------------------------------------------


@pytest.mark.skipif(find_browser() is None, reason="нет установленного Chrome/Edge")
async def test_real_browser_renders_a_png_of_the_right_width(
    bot_config: AppConfig, at: Clock, tmp_path: Path
) -> None:
    at(TUESDAY)
    save_demo(bot_config, TUESDAY)
    builder = PictureBuilder(bot_config, PlaywrightRenderer())

    picture = await builder.today(TUESDAY)

    assert picture is not None
    assert picture.png[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(picture.png[16:20], "big")
    assert width == 1080

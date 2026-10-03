"""Retakes ("пересдача"): marked with the template text, and a day that has nothing
but a retake is a day off for the group.

Lessons are synthetic: the site shows one retake at most, and never in summer.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import SecretStr

from app import moscow
from app.bot.dev import demo
from app import texts
from app.render.pictures import PictureBuilder
from app.bot.service import BotService
from app.render.view import build_days
from app.config import AppConfig, BotConfig
from app.models.changes import Added, Cancelled, Moved, TeacherChanged, build_event
from app.models.domain import Lesson, SnapshotMeta
from app.parsing.normalize import kind_and_badge
from app.snapshots.diff import diff
from app.snapshots.service import ScheduleService
from tests.fakes import Clock, FakeRenderer, FakeTelegram

SUNDAY = dt.date(2026, 9, 27)
MONDAY = dt.date(2026, 9, 28)
TUESDAY = dt.date(2026, 9, 29)
GROUP = "ОККИПд-307"
TEACHER = "Радонежская Нина Владиславовна"
SENTENCE = f"Пересдача по предмету Компьютерные сети у {TEACHER} в аудитории 308"


def _at(day: dt.date, clock: str) -> dt.datetime:
    hours, minutes = clock.split(":")
    return dt.datetime(day.year, day.month, day.day, int(hours), int(minutes), tzinfo=moscow.MOSCOW)


def _retake(
    day: dt.date,
    *,
    start: str = "15:30",
    end: str = "17:00",
    title: str = "Компьютерные сети",
    teacher: str | None = TEACHER,
    room: str | None = "308",
    source_id: str = "retake-1",
) -> Lesson:
    template = demo.build_schedule(day)[0]
    return template.model_copy(
        update={
            "source_id": source_id,
            "date": day,
            "starts_at": _at(day, start),
            "ends_at": _at(day, end),
            "time_label": f"{start}-{end}",
            "discipline": title,
            "kind": "пересдача",
            "badge": "ПЕР",
            "teacher": teacher,
            "room": room,
            "stream": (),
            "position": 1,
        }
    )


def _week_with(retake_only_on: dt.date | None, extra: list[Lesson] | None = None) -> list[Lesson]:
    """The demo weeks, with `retake_only_on` reduced to a single retake."""
    lessons = demo.build_schedule(SUNDAY)
    if retake_only_on is not None:
        lessons = [item for item in lessons if item.date != retake_only_on]
        lessons.append(_retake(retake_only_on))
    return lessons + (extra or [])


def _snapshot(lessons: list[Lesson]) -> SnapshotMeta:
    days = [item.date for item in lessons]
    return SnapshotMeta(
        id=1,
        taken_at=_at(SUNDAY, "06:00"),
        status="ok",
        covered_from=min(days),
        covered_to=max(days),
        lesson_count=len(lessons),
    )


# -- recognising it ------------------------------------------------------------


@pytest.mark.parametrize("raw", ["пересдача", "Пересдача", " ПЕРЕСДАЧА "])
def test_the_sites_type_becomes_a_retake_whatever_the_case(raw: str) -> None:
    assert kind_and_badge(raw) == ("пересдача", "ПЕР")
    assert _retake(TUESDAY).model_copy(update={"kind": raw}).is_retake


def test_an_ordinary_lesson_and_an_exam_are_not_retakes() -> None:
    lesson = demo.build_schedule(TUESDAY)[0]
    assert not lesson.is_retake
    assert not lesson.model_copy(update={"kind": "экзамен"}).is_retake


# -- the template ------------------------------------------------------------------


def test_the_sentence_follows_the_template_exactly() -> None:
    assert texts.format_retake("Компьютерные сети", TEACHER, "308") == SENTENCE


def test_missing_teacher_or_room_is_said_not_dropped() -> None:
    assert texts.format_retake("X", None, "308") == (
        "Пересдача по предмету X, преподаватель не указан на сайте, в аудитории 308"
    )
    assert texts.format_retake("X", "Иванов И. И.", None) == (
        "Пересдача по предмету X, у Иванов И. И., аудитория не указана"
    )
    assert texts.format_retake("X", "  ", "") == (
        "Пересдача по предмету X, преподаватель не указан на сайте, аудитория не указана"
    )


# -- what a day looks like ---------------------------------------------------------


def test_a_day_with_only_a_retake_has_no_lessons_and_is_a_day_off() -> None:
    lessons = _week_with(TUESDAY)
    day = build_days(lessons, _snapshot(lessons), TUESDAY, 1, group=GROUP)[0]

    assert day.lessons == () and len(day.retakes) == 1
    assert day.is_retake_only
    assert day.retakes[0].sentence() == SENTENCE
    assert day.span is None


def test_a_retake_beside_lessons_does_not_count_as_one_of_them() -> None:
    early = _retake(MONDAY, start="07:30", end="09:00")
    lessons = _week_with(None, [early])
    day = build_days(lessons, _snapshot(lessons), MONDAY, 1, group=GROUP)[0]

    assert len(day.lessons) == 3 and len(day.retakes) == 2  # the demo's own, and the early one
    assert not day.is_retake_only
    assert day.lessons[0].number == 1  # not 2, though the retake starts earlier
    assert day.span == "08:30–13:40"  # the retake is not in the span either


def test_an_unpublished_day_is_not_called_a_day_off() -> None:
    lessons = _week_with(TUESDAY)
    far = dt.date(2026, 12, 1)
    day = build_days(lessons, _snapshot(lessons), far, 1, group=GROUP)[0]
    assert day.coverage == "unpublished" and not day.is_retake_only


# -- pictures ------------------------------------------------------------------------


@pytest.fixture
def bot_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), chat_id="-100500")
    return config.model_copy(update={"bot": bot})


def _store(config: AppConfig, lessons: list[Lesson]) -> None:
    service = ScheduleService(config)
    service.prepare()
    service.store_lessons(lessons)


async def test_the_week_shows_the_day_off_and_the_retake_apart(
    bot_config: AppConfig, at: Clock
) -> None:
    at(SUNDAY)
    _store(bot_config, _week_with(TUESDAY))

    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)

    assert picture is not None
    page = picture.png.decode("utf-8")
    assert texts.NO_LESSONS in page and texts.RETAKE_TAG in page
    assert "Компьютерные сети" in page  # the subject, on the picture
    assert "🔁 Вт, 29 сен · 15:30–17:00 — " + SENTENCE in picture.caption  # and as text


async def test_on_the_week_picture_the_retake_is_the_subject_and_the_room_and_the_caption_has_it_all(
    bot_config: AppConfig, at: Clock
) -> None:
    at(SUNDAY)
    _store(bot_config, _week_with(TUESDAY))

    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)

    assert picture is not None
    page = picture.png.decode("utf-8")
    body = page[page.index("<body>") :]  # the fonts in <style> are base64: digits turn up there
    assert "Компьютерные сети" in body and texts.RETAKE_TAG in body
    assert ">ауд. 308<" in body  # the room, in its column like a lesson's
    assert "Радонежская" not in body  # the teacher stays out
    assert "Пересдача по предмету" not in body  # and so does the sentence
    assert SENTENCE in picture.caption  # the caption has everything


async def test_the_week_shows_a_big_abbreviation_and_the_date_without_the_weekday_name(
    bot_config: AppConfig, at: Clock
) -> None:
    at(SUNDAY)
    _store(bot_config, _week_with(TUESDAY))

    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)

    assert picture is not None
    page = picture.png.decode("utf-8")
    assert '<div class="dow">Пн</div>' in page
    assert '<div class="dat">28 сентября</div>' in page
    assert "понедельник" not in page  # "Пн" over the date says it already


async def test_a_day_off_is_just_the_word(bot_config: AppConfig, at: Clock) -> None:
    at(SUNDAY)
    _store(bot_config, _week_with(TUESDAY))

    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)

    assert picture is not None
    page = picture.png.decode("utf-8")
    assert f'<div class="none">{texts.NO_LESSONS}</div>' in page
    assert "<i>" not in page  # no round icon beside it


async def test_the_week_total_does_not_count_the_retake(bot_config: AppConfig, at: Clock) -> None:
    at(SUNDAY)
    plain = demo.build_schedule(SUNDAY)
    _store(bot_config, plain + [_retake(MONDAY, start="07:30", end="09:00")])
    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)
    assert picture is not None
    week = [
        item
        for item in plain
        if MONDAY <= item.date < MONDAY + dt.timedelta(days=6) and not item.is_retake
    ]
    assert texts.lessons_count(len(week)) in picture.png.decode("utf-8")


async def test_the_day_picture_of_a_retake_day_says_day_off(
    bot_config: AppConfig, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    _store(bot_config, _week_with(TUESDAY))

    picture = await PictureBuilder(bot_config, FakeRenderer()).today(TUESDAY, force=True)

    assert picture is not None
    page = picture.png.decode("utf-8")
    body = page[page.index("<body>") :]
    assert texts.NO_LESSONS in body  # the day is a day off
    assert '<span class="badge red">Пересдача</span>' in body  # the retake is a red card
    assert "Компьютерные сети" in body and TEACHER in body
    assert '<div class="lbl">аудитория</div><div class="num">308</div>' in body
    assert "Пересдача по предмету" not in body  # the full sentence is the caption's
    assert "🔁 15:30–17:00 — " + SENTENCE in picture.caption
    assert picture.last_end is None  # nothing of the group's to wait for


async def test_site_text_in_a_retake_is_escaped(bot_config: AppConfig, at: Clock) -> None:
    at(TUESDAY, "10:00")
    lessons = _week_with(None)
    lessons = [i for i in lessons if i.date != TUESDAY] + [
        _retake(TUESDAY, title="<b>Сети</b> & <i>ИС</i>")
    ]
    _store(bot_config, lessons)

    picture = await PictureBuilder(bot_config, FakeRenderer()).today(TUESDAY, force=True)

    assert picture is not None
    assert "<b>Сети</b>" not in picture.png.decode("utf-8")
    assert "&lt;b&gt;Сети&lt;/b&gt; &amp; &lt;i&gt;ИС&lt;/i&gt;" in picture.caption


def test_notes_beyond_the_caption_limit_are_counted_not_lost() -> None:
    notes = [texts.retake_note("Пересдача по предмету " + "я" * 90, "08:30", "10:00") for _ in range(20)]

    caption = texts.with_notes("📅 Расписание на неделю", notes)

    assert len(caption) <= texts.CAPTION_LIMIT
    assert caption.splitlines()[-1].startswith("…и ещё ")
    assert texts.with_notes("подпись", []) == "подпись"


# -- the bot ---------------------------------------------------------------------------


@pytest.fixture
def bot(bot_config: AppConfig, telegram: FakeTelegram) -> BotService:
    ScheduleService(bot_config).prepare()
    return BotService(bot_config, telegram.bot, FakeRenderer())


async def test_a_retake_only_day_gets_no_today_picture(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "07:00")
    _store(bot_config, _week_with(TUESDAY))

    assert await bot.post_today() is False
    assert telegram.calls == []


async def test_go_skips_a_retake_only_day_like_any_day_off(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    _store(bot_config, _week_with(TUESDAY))

    day = await bot.post_next_day()

    assert day == dt.date(2026, 9, 30)  # Wednesday: the first day with lessons


async def test_the_week_post_carries_the_retake_text(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    _store(bot_config, _week_with(TUESDAY))

    assert await bot.post_week() is True

    assert SENTENCE in telegram.captions[0]


async def test_a_retake_that_appears_later_redraws_the_posted_week(
    bot: BotService, bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY)
    _store(bot_config, demo.build_schedule(SUNDAY))
    await bot.post_week()
    telegram.calls.clear()

    at(SUNDAY, "12:00")
    _store(bot_config, _week_with(TUESDAY))

    assert await bot.sync_pictures() == 1
    assert telegram.kinds() == ["edit"]


# -- changes -----------------------------------------------------------------------------


def test_a_new_retake_is_announced_as_a_retake_not_as_a_lesson() -> None:
    before = demo.build_schedule(SUNDAY)
    after = [*before, _retake(MONDAY, start="15:30", end="17:00")]

    drafts = diff(before, after)

    assert [(d.kind, d.retake) for d in drafts] == [("added", True)]
    event = build_event(drafts[0].model_dump(), id=1, detected_at=_at(SUNDAY, "12:00"))
    assert isinstance(event, Added)
    message = texts.format_changes([event])[0]
    assert "🔁 <b>" + SENTENCE + "</b>" in message
    assert "Добавлена" not in message


def test_an_ordinary_lesson_event_is_not_marked_as_a_retake() -> None:
    before = demo.build_schedule(SUNDAY)
    drafts = diff(before, before[:-1])
    assert [(d.kind, d.retake) for d in drafts] == [("cancelled", False)]


def _events(retake: bool) -> list[Moved | Cancelled | TeacherChanged]:
    moment = _at(SUNDAY, "12:00")
    title = "Компьютерные сети"
    return [
        Moved(
            id=1, detected_at=moment, date=TUESDAY, discipline=title, retake=retake,
            from_time="15:30-17:00", to_time="13:50-15:20",
        ),
        Moved(
            id=2, detected_at=moment, date=TUESDAY, discipline=title, retake=retake,
            from_time="15:30-17:00", to_time="15:30-17:00", from_room="308", to_room="309",
        ),
        Cancelled(
            id=3, detected_at=moment, date=TUESDAY, discipline=title, retake=retake,
            at_time="15:30-17:00",
        ),
        TeacherChanged(
            id=4, detected_at=moment, date=TUESDAY, discipline=title, retake=retake,
            at_time="15:30-17:00",
        ),
    ]


def test_every_change_of_a_retake_says_retake() -> None:
    text = "\n".join(texts.format_changes(_events(True)))

    for phrase in (
        "Пересдача перенесена",
        "Пересдача: другая аудитория",
        "Пересдача отменена",
        "Пересдача: другой преподаватель",
    ):
        assert phrase in text


def test_changes_of_ordinary_lessons_keep_their_wording() -> None:
    text = "\n".join(texts.format_changes(_events(False)))

    assert "Пересдача" not in text
    for phrase in ("Перенос", "Другая аудитория", "Отменена", "Другой преподаватель"):
        assert phrase in text


def test_events_saved_before_retakes_existed_still_load() -> None:
    old: dict[str, object] = {
        "kind": "cancelled",
        "date": "2026-09-29",
        "discipline": "Компьютерные сети",
        "at_time": "15:30-17:00",
        "room": "308",
    }
    event = build_event(old, id=5, detected_at=_at(SUNDAY, "12:00"))
    assert isinstance(event, Cancelled) and event.retake is False


# -- the demo data --------------------------------------------------------------------------


def test_the_demo_week_has_a_retake_only_day_and_a_plain_day_off() -> None:
    lessons = demo.build_schedule(SUNDAY)
    days = build_days(lessons, _snapshot(lessons), MONDAY, 6, group=GROUP)
    monday, thursday, friday = days[0], days[3], days[4]

    assert monday.lessons and len(monday.retakes) == 1 and not monday.is_retake_only
    assert thursday.is_retake_only and thursday.retakes[0].start == "15:30"
    assert friday.coverage == "published" and not friday.lessons and not friday.retakes
    assert all(day.lessons for day in (days[1], days[2], days[5]))


def test_demo_changes_never_touch_the_retake() -> None:
    lessons = demo.build_schedule(SUNDAY)
    changed = demo.apply_changes(lessons, MONDAY)

    def retakes(items: list[Lesson]) -> list[dict[str, object]]:
        # `position` is renumbered for the whole day and never shown for a retake.
        return [item.model_dump(exclude={"position"}) for item in items if item.is_retake]

    assert retakes(changed) == retakes(lessons)

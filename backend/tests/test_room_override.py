"""Правка аудитории по сообщению куратора: снимки, события, устойчивость к прогону парсера."""

from __future__ import annotations

import datetime as dt

import pytest

from app.bot import demo
from app.config import AppConfig
from app.models.changes import Moved
from app.models.db import connect
from app.parsing.curator import RoomNotice, parse_room_notice
from app.snapshots import store
from app.snapshots.service import ScheduleService
from tests.fakes import Clock

TUESDAY = dt.date(2026, 9, 29)  # the demo's Tuesday 13:50 is "Операционные системы", room 305
WEDNESDAY = dt.date(2026, 9, 30)  # and Wednesday 13:50 is the language class, room 402
TEXT = "в 13.50 у ОККИПд-307 пара будет в 314 аудитории"


@pytest.fixture
def schedule(config: AppConfig, at: Clock) -> ScheduleService:
    at(TUESDAY, "10:00")
    service = ScheduleService(config)
    service.prepare()
    service.store_lessons(demo.build_schedule(TUESDAY))
    return service


def _notice(text: str = TEXT) -> RoomNotice:
    notice = parse_room_notice(text)
    assert notice is not None
    return notice


def _room(config: AppConfig, day: dt.date, start: str) -> str | None:
    with connect(config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        lessons = store.load_lessons(conn, latest.id)
    [lesson] = [item for item in lessons if item.date == day and item.slot == start]
    return lesson.room


async def _correct(schedule: ScheduleService, text: str = TEXT) -> str:
    result = await schedule.correct_room(_notice(text), author_id=7, chat_id="-1", text=text)
    return result.status


async def test_the_room_changes_and_the_change_is_an_event_with_both_rooms(
    schedule: ScheduleService, config: AppConfig
) -> None:
    assert await _correct(schedule) == "applied"

    assert _room(config, TUESDAY, "13:50") == "314"
    with connect(config.db_path) as conn:
        events = store.events_after(conn, 0)
    [moved] = [event for event in events if isinstance(event, Moved)]
    assert (moved.from_room, moved.to_room) == ("305", "314")
    assert moved.date == TUESDAY


async def test_the_same_notice_twice_changes_nothing_the_second_time(
    schedule: ScheduleService, config: AppConfig
) -> None:
    await _correct(schedule)
    with connect(config.db_path) as conn:
        before = len(store.events_after(conn, 0))

    assert await _correct(schedule) == "unchanged"  # e.g. posted in both groups' chats

    with connect(config.db_path) as conn:
        assert len(store.events_after(conn, 0)) == before


async def test_the_next_site_run_does_not_undo_the_correction(
    schedule: ScheduleService, config: AppConfig
) -> None:
    await _correct(schedule)
    with connect(config.db_path) as conn:
        before = len(store.events_after(conn, 0))

    # The site knows nothing about the curator and still says 305.
    assert schedule.store_lessons(demo.build_schedule(TUESDAY)) == 0

    assert _room(config, TUESDAY, "13:50") == "314"
    with connect(config.db_path) as conn:
        assert len(store.events_after(conn, 0)) == before  # and no "moved back" event


async def test_a_newer_notice_for_the_same_lesson_replaces_the_older(
    schedule: ScheduleService, config: AppConfig
) -> None:
    await _correct(schedule)

    await _correct(schedule, "в 13.50 у ОККИПд-307 пара будет в 215 аудитории")

    assert _room(config, TUESDAY, "13:50") == "215"
    schedule.store_lessons(demo.build_schedule(TUESDAY))
    assert _room(config, TUESDAY, "13:50") == "215"
    with connect(config.db_path) as conn:
        last = store.events_after(conn, 0)[-1]
    assert isinstance(last, Moved) and (last.from_room, last.to_room) == ("314", "215")


async def test_without_a_day_the_nearest_lesson_that_has_not_ended_is_taken(
    schedule: ScheduleService, config: AppConfig, at: Clock
) -> None:
    at(TUESDAY, "16:00")  # today's 13:50 lesson is over

    assert await _correct(schedule) == "applied"

    assert _room(config, WEDNESDAY, "13:50") == "314"
    assert _room(config, TUESDAY, "13:50") == "305"


async def test_a_day_word_picks_the_day(schedule: ScheduleService, config: AppConfig) -> None:
    assert await _correct(schedule, "завтра в 13.50 у ОККИПд-307 пара в 314 аудитории") == "applied"

    assert _room(config, WEDNESDAY, "13:50") == "314"
    assert _room(config, TUESDAY, "13:50") == "305"


async def test_today_after_the_lesson_is_over_finds_nothing(
    schedule: ScheduleService, at: Clock
) -> None:
    at(TUESDAY, "16:00")

    assert await _correct(schedule, "сегодня в 13.50 у ОККИПд-307 пара в 314 аудитории") == "no_lesson"


async def test_a_time_with_no_lesson_changes_nothing(
    schedule: ScheduleService, config: AppConfig
) -> None:
    with connect(config.db_path) as conn:
        before = store.latest_ok(conn)

    assert await _correct(schedule, "в 09.00 у ОККИПд-307 пара в 314 аудитории") == "no_lesson"

    with connect(config.db_path) as conn:
        assert store.latest_ok(conn) == before  # no new snapshot


async def test_a_retake_is_not_the_groups_lesson(schedule: ScheduleService) -> None:
    # The demo's Monday and Thursday retakes start at 15:30; the group has nothing then.
    assert await _correct(schedule, "в 15.30 у ОККИПд-307 пара в 314 аудитории") == "no_lesson"


async def test_two_lessons_in_one_slot_are_not_guessed(
    config: AppConfig, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    service = ScheduleService(config)
    service.prepare()
    lessons = demo.build_schedule(TUESDAY)
    twin = next(l for l in lessons if l.date == TUESDAY and l.slot == "13:50")
    service.store_lessons([*lessons, twin.model_copy(update={"source_id": "twin", "room": "999"})])

    assert await _correct(service) == "ambiguous"


async def test_without_any_schedule_there_is_nothing_to_correct(
    config: AppConfig, at: Clock
) -> None:
    at(TUESDAY, "10:00")
    service = ScheduleService(config)
    service.prepare()

    assert await _correct(service) == "no_schedule"

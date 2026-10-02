"""Разбор сообщений куратора: что считается сообщением о смене аудитории."""

from __future__ import annotations

import datetime as dt

import pytest

from app.parsing.college import normalize_name
from app.parsing.curator import parse_room_notice

EXAMPLE = "добрый день \nв 13.50 у ОККИПд-306,307 пара будет в 314 аудитории"


def test_the_curators_own_example() -> None:
    notice = parse_room_notice(EXAMPLE)

    assert notice is not None
    assert notice.groups == (normalize_name("ОККИПд-306"), normalize_name("ОККИПд-307"))
    assert notice.start == dt.time(13, 50)
    assert notice.start_label == "13:50"
    assert notice.room == "314"
    assert notice.day_hint is None


def test_the_group_is_compared_the_way_the_parser_does() -> None:
    notice = parse_room_notice(EXAMPLE)

    assert notice is not None
    assert notice.mentions("ОККИПд-307")
    assert notice.mentions("оккипд‑307")  # case and the kind of dash do not matter
    assert not notice.mentions("ОККИПд-308")


@pytest.mark.parametrize(
    ("text", "start", "room", "hint"),
    [
        ("в 13:50 у ОККИПд-307 пара в ауд. 314", dt.time(13, 50), "314", None),
        ("Завтра в 8.30 у ОККИПд-307 пара в 104/2 кабинете", dt.time(8, 30), "104/2", "tomorrow"),
        ("в 12.10 у ОККИПд-306 и 307 аудитория 314а", dt.time(12, 10), "314А", None),
        ("послезавтра в 10.10 ОККИПд-307: кабинет № 215", dt.time(10, 10), "215", "day_after"),
        ("Сегодня в 13.50 ОККИПд-307 в аудитории 402", dt.time(13, 50), "402", "today"),
    ],
)
def test_other_wordings(text: str, start: dt.time, room: str, hint: str | None) -> None:
    notice = parse_room_notice(text)

    assert notice is not None
    assert (notice.start, notice.room, notice.day_hint) == (start, room, hint)


def test_the_time_is_not_taken_for_the_room_and_a_group_number_is_not_either() -> None:
    notice = parse_room_notice("в 13.50 аудитория 314 у ОККИПд-306 и 307")

    assert notice is not None and notice.room == "314"


@pytest.mark.parametrize(
    "text",
    [
        "в 15.00 родительское собрание в 205 аудитории",  # no group
        "в 13.50 у ОККИПд-307 пара будет",  # no room
        "у ОККИПд-307 пара в 314 аудитории",  # no time
        "ОККИПд-307 12.10.2026 в 314 ауд.",  # a date is not a time
        "добрый день",
        "",
    ],
)
def test_what_is_not_a_notice_is_none(text: str) -> None:
    assert parse_room_notice(text) is None

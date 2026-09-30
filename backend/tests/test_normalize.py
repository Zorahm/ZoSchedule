"""Разбор сырья: то, что сайт отдаёт криво, а модель обязана отдавать ровно."""

from __future__ import annotations

import datetime as dt

from app.models.domain import Lesson
from app.parsing.college import RawDay
from app.parsing.normalize import parse_days

from tests.conftest import GROUP


def _on(lessons: list[Lesson], day: str) -> list[Lesson]:
    return [lesson for lesson in lessons if lesson.date == dt.date.fromisoformat(day)]


def test_yo_folding_maps_zachet_to_badge(lessons_b: list[Lesson]) -> None:
    """Сайт пишет «зачет», словарь парсера знает «зачёт» — маппинг обязан сработать."""
    exam = next(lesson for lesson in lessons_b if lesson.date == dt.date(2026, 9, 9))
    assert exam.kind == "зачёт"
    assert exam.badge == "ЗАЧ"
    assert exam.is_exam


def test_missing_teacher_becomes_none_and_lesson_survives(lessons_a: list[Lesson]) -> None:
    """Пустое поле и отсутствие пары — разные вещи: строку не выбрасываем."""
    discrete = next(
        lesson
        for lesson in _on(lessons_a, "2026-09-07")
        if lesson.discipline == "Дискретная математика"
    )
    assert discrete.teacher is None
    assert discrete.room == "415"


def _two_at_once() -> list[Lesson]:
    def entry(lesson_id: str, room: str) -> dict[str, object]:
        return {
            "id": lesson_id,
            "startAt": "2026-09-07 08:30:00",
            "endAt": "2026-09-07 10:00:00",
            "time": "08:30-10:00",
            "type": "практическое занятие",
            "room": room,
            "discipline": {"name": "Иностранный язык"},
        }

    day: list[RawDay] = [
        {"date": "2026-09-07T00:00:00+03:00", "lessons": [entry("a", "310"), entry("b", "311")]}
    ]
    return parse_days(day, group_name=GROUP)


def test_slot_shares_one_position(lessons_a: list[Lesson]) -> None:
    """Записи в одно время — это одна пара по счёту, а не две."""
    monday = _on(lessons_a, "2026-09-07")
    assert [lesson.position for lesson in sorted(monday, key=lambda l: l.starts_at)] == [1, 2, 3, 4]

    lessons = _two_at_once()
    assert {lesson.position for lesson in lessons} == {1}
    assert [lesson.dedup_index for lesson in lessons] == [0, 1]


def test_building_short_name_preferred_for_density(lessons_a: list[Lesson]) -> None:
    lesson = _on(lessons_a, "2026-09-04")[0]
    assert lesson.building == "Варшавская"
    assert lesson.building_short == "В"


def test_times_are_moscow_aware(lessons_a: list[Lesson]) -> None:
    lesson = _on(lessons_a, "2026-09-07")[0]
    assert lesson.starts_at.utcoffset() == dt.timedelta(hours=3)
    assert lesson.starts_at.hour == 8


def test_lesson_without_time_is_skipped_not_crashing() -> None:
    broken: list[RawDay] = [
        {
            "date": "2026-09-07T00:00:00+03:00",
            "lessons": [
                {"id": "x", "discipline": {"name": "Без времени"}},
                {
                    "id": "y",
                    "time": "09:00-10:30",
                    "type": "лекция",
                    "discipline": {"name": "С временем"},
                },
            ],
        }
    ]
    lessons = parse_days(broken, group_name=GROUP)
    assert [lesson.discipline for lesson in lessons] == ["С временем"]


def test_repeated_discipline_gets_distinct_dedup_index() -> None:
    """Две пары одного предмета подряд не должны схлопнуться в ключе диффа."""
    day: list[RawDay] = [
        {
            "date": "2026-09-07T00:00:00+03:00",
            "lessons": [
                {
                    "id": "a",
                    "startAt": "2026-09-07 08:30:00",
                    "endAt": "2026-09-07 10:00:00",
                    "time": "08:30-10:00",
                    "type": "практическое занятие",
                    "room": "208",
                    "discipline": {"name": "Основы алгоритмизации"},
                },
                {
                    "id": "b",
                    "startAt": "2026-09-07 10:10:00",
                    "endAt": "2026-09-07 11:40:00",
                    "time": "10:10-11:40",
                    "type": "практическое занятие",
                    "room": "208",
                    "discipline": {"name": "Основы алгоритмизации"},
                },
            ],
        }
    ]
    lessons = parse_days(day, group_name=GROUP)
    assert [lesson.dedup_index for lesson in lessons] == [0, 1]


def test_stream_lecture_lists_the_other_group() -> None:
    """Поточная лекция на две группы: сайт кладёт в `groups[]` обе."""
    day: list[RawDay] = [
        {
            "date": "2026-09-07T00:00:00+03:00",
            "lessons": [
                {
                    "id": "a",
                    "startAt": "2026-09-07 08:30:00",
                    "endAt": "2026-09-07 10:00:00",
                    "time": "08:30-10:00",
                    "type": "лекция",
                    "discipline": {"name": "Экономика отрасли"},
                    "groups": [{"name": "ОККИПд-306"}, {"name": GROUP}],
                }
            ],
        }
    ]
    lesson = parse_days(day, group_name=GROUP)[0]
    assert lesson.stream == ("306",)


def test_dash_instead_of_teacher_reads_as_missing() -> None:
    """Прочерк вместо имени — это отсутствие имени, а не преподаватель «-»."""
    day: list[RawDay] = [
        {
            "date": "2026-09-07T00:00:00+03:00",
            "lessons": [
                {
                    "id": "a",
                    "startAt": "2026-09-07 08:30:00",
                    "endAt": "2026-09-07 10:00:00",
                    "time": "08:30-10:00",
                    "type": "мастер класс",
                    "discipline": {"name": "ПРО-Развитие"},
                    "teacher": {"name": "-"},
                }
            ],
        }
    ]
    assert parse_days(day, group_name=GROUP)[0].teacher is None


def _one(groups: list[str], *, discipline: str = "Экономика отрасли") -> Lesson:
    day: list[RawDay] = [
        {
            "date": "2026-09-07T00:00:00+03:00",
            "lessons": [
                {
                    "id": "a",
                    "startAt": "2026-09-07 08:30:00",
                    "endAt": "2026-09-07 10:00:00",
                    "time": "08:30-10:00",
                    "type": "лекция",
                    "discipline": {"name": discipline},
                    "groups": [{"name": name} for name in groups],
                }
            ],
        }
    ]
    return parse_days(day, group_name=GROUP)[0]


def test_stream_names_the_other_group_by_number() -> None:
    """Поток объясняет чужие лица в аудитории — состав стоит показать."""
    lesson = _one(["ОККИПд-306", GROUP])
    assert lesson.stream == ("306",)


def test_stream_from_another_programme_keeps_full_name() -> None:
    """Общий у групп только номер — тогда одна цифра ничего не объясняет."""
    assert _one(["ОКТПд-201", GROUP]).stream == ("ОКТПд-201",)


def test_lesson_only_for_us_has_no_stream() -> None:
    assert _one([GROUP]).stream == ()

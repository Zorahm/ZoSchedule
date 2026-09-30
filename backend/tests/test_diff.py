"""Диффы на паре подготовленных снимков — главная логика приложения."""

from __future__ import annotations

import datetime as dt

from app.models.changes import AddedDraft, CancelledDraft, MovedDraft, TeacherChangedDraft
from app.models.domain import Lesson
from app.snapshots.diff import diff


def test_detects_exactly_the_five_expected_events(
    lessons_a: list[Lesson], lessons_b: list[Lesson]
) -> None:
    drafts = diff(lessons_a, lessons_b)
    assert sorted(draft.kind for draft in drafts) == [
        "added",
        "cancelled",
        "moved",
        "moved",
        "teacher_changed",
    ]


def test_moved_carries_both_old_and_new(lessons_a: list[Lesson], lessons_b: list[Lesson]) -> None:
    """«Перенесена» без «откуда» бесполезна — лента изменений ради этого и есть."""
    moved = next(
        draft
        for draft in diff(lessons_a, lessons_b)
        if isinstance(draft, MovedDraft) and draft.discipline == "Инженерная графика"
    )
    assert (moved.from_time, moved.to_time) == ("15:30-17:00", "13:50-15:20")
    assert (moved.from_room, moved.to_room) == ("305", "112")


def test_room_change_keeps_time_and_reports_both_rooms(
    lessons_a: list[Lesson], lessons_b: list[Lesson]
) -> None:
    moved = next(
        draft
        for draft in diff(lessons_a, lessons_b)
        if isinstance(draft, MovedDraft) and draft.discipline == "Иностранный язык"
    )
    assert (moved.from_room, moved.to_room) == ("310", "311")
    assert moved.from_time == moved.to_time


def test_teacher_appearing_is_a_change_not_an_addition(
    lessons_a: list[Lesson], lessons_b: list[Lesson]
) -> None:
    changed = next(
        draft for draft in diff(lessons_a, lessons_b) if isinstance(draft, TeacherChangedDraft)
    )
    assert changed.discipline == "Дискретная математика"
    assert changed.from_teacher is None
    assert changed.to_teacher == "Синицына Т. В."


def test_cancelled_and_added(lessons_a: list[Lesson], lessons_b: list[Lesson]) -> None:
    drafts = diff(lessons_a, lessons_b)
    cancelled = next(draft for draft in drafts if isinstance(draft, CancelledDraft))
    added = next(draft for draft in drafts if isinstance(draft, AddedDraft))

    assert cancelled.discipline == "Архитектура компьютерных систем"
    assert cancelled.date == dt.date(2026, 9, 8)
    assert added.discipline == "Физическая культура"
    assert added.teacher is None


def test_days_outside_the_overlap_produce_nothing(
    lessons_a: list[Lesson], lessons_b: list[Lesson]
) -> None:
    """Сдвиг горизонта публикации — не изменение расписания.

    04.09 есть только в старом снимке, 09.09 — только в новом. Ни «отменили»,
    ни «добавили» тут быть не должно.
    """
    drafts = diff(lessons_a, lessons_b)
    touched = {draft.date for draft in drafts}
    assert dt.date(2026, 9, 4) not in touched
    assert dt.date(2026, 9, 9) not in touched


def test_identical_snapshots_have_no_changes(lessons_a: list[Lesson]) -> None:
    assert diff(lessons_a, lessons_a) == []


def test_empty_snapshot_does_not_cancel_everything(lessons_a: list[Lesson]) -> None:
    """Пустой ответ сайта не должен читаться как отмена всех пар семестра."""
    assert diff(lessons_a, []) == []
    assert diff([], lessons_a) == []


def test_regenerated_ids_still_match_by_discipline(
    lessons_a: list[Lesson], lessons_b: list[Lesson]
) -> None:
    """Если сайт перевыдал id, дифф обязан удержать пары через запасной ключ."""
    renumbered = [lesson.model_copy(update={"source_id": f"new-{index}"}) for index, lesson in enumerate(lessons_b)]
    drafts = diff(lessons_a, renumbered)
    assert sorted(draft.kind for draft in drafts) == [
        "added",
        "cancelled",
        "moved",
        "moved",
        "teacher_changed",
    ]


def test_events_are_sorted_by_date_then_time(
    lessons_a: list[Lesson], lessons_b: list[Lesson]
) -> None:
    drafts = diff(lessons_a, lessons_b)
    dates = [draft.date for draft in drafts]
    assert dates == sorted(dates)

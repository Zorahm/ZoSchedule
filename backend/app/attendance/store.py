"""SQL журнала посещаемости: список группы и отметки. Другого SQL у журнала нет."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from app import moscow
from app.attendance.models import Mark, MarkChange, RosterEntry


class RosterError(ValueError):
    """Список группы нельзя сохранить; текст показывается старосте как есть."""


@dataclass(frozen=True, slots=True)
class Student:
    id: int
    name: str


def sort_key(name: str) -> str:
    """Алфавитный порядок журнала: без регистра и с «ё» среди «е»."""
    return name.casefold().replace("ё", "е")


def active_students(conn: sqlite3.Connection) -> list[Student]:
    rows = conn.execute("SELECT id, full_name FROM roster WHERE active = 1").fetchall()
    students = [Student(int(row["id"]), str(row["full_name"])) for row in rows]
    return sorted(students, key=lambda student: (sort_key(student.name), student.id))


def _matching_rows(conn: sqlite3.Connection) -> tuple[dict[int, str], dict[str, int]]:
    """Все записи списка и поиск по имени. Живая запись побеждает убранную с тем же ФИО."""
    names: dict[int, str] = {}
    by_key: dict[str, int] = {}
    rows = conn.execute("SELECT id, full_name, active FROM roster ORDER BY id DESC").fetchall()
    for row in rows:
        student_id, name = int(row["id"]), str(row["full_name"])
        names[student_id] = name
        if row["active"] or sort_key(name) not in by_key:
            by_key[sort_key(name)] = student_id
    return names, by_key


def save_roster(
    conn: sqlite3.Connection, entries: Sequence[RosterEntry], *, now: dt.datetime
) -> list[Student]:
    """Приводит список группы к присланному.

    Запись с известным ``id`` переименовывается на месте, чтобы поправленная опечатка не
    оторвала студента от его отметок. Новое ФИО, совпавшее с когда-то убранным, оживляет
    ту запись. Пропавших из списка гасит, не удаляя.
    """
    keys = [sort_key(entry.name) for entry in entries]
    for entry, key in zip(entries, keys, strict=True):
        if keys.count(key) > 1:
            raise RosterError(f"Дважды в списке: {entry.name}")

    existing, by_key = _matching_rows(conn)
    conn.execute("BEGIN")
    try:
        claimed: dict[int, str] = {}
        loose: list[RosterEntry] = []
        for entry in entries:
            if entry.id is not None and entry.id in existing and entry.id not in claimed:
                claimed[entry.id] = entry.name
            else:
                loose.append(entry)
        for entry in loose:
            matched = by_key.get(sort_key(entry.name))
            if matched is not None and matched not in claimed:
                claimed[matched] = entry.name
                continue
            cursor = conn.execute(
                "INSERT INTO roster (full_name, active, added_at) VALUES (?, 1, ?)",
                (entry.name, moscow.isoformat(now)),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite не вернул id вставленной строки")
            claimed[cursor.lastrowid] = entry.name
        for student_id, name in claimed.items():
            conn.execute(
                "UPDATE roster SET full_name = ?, active = 1 WHERE id = ?", (name, student_id)
            )
        placeholders = ",".join("?" * len(claimed))
        conn.execute(
            f"UPDATE roster SET active = 0 WHERE active = 1 AND id NOT IN ({placeholders})",
            list(claimed),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return active_students(conn)


def marks_on(conn: sqlite3.Connection, day: dt.date) -> dict[tuple[int, str], Mark]:
    """Отметки дня: (студент, время начала пары) -> отметка."""
    rows = conn.execute(
        "SELECT student_id, starts, mark FROM attendance WHERE date = ?", (day.isoformat(),)
    ).fetchall()
    result: dict[tuple[int, str], Mark] = {}
    for row in rows:
        key = (int(row["student_id"]), str(row["starts"]))
        # CHECK в схеме пускает только эти два значения; ветки нужны типам, не данным.
        if row["mark"] == "present":
            result[key] = "present"
        elif row["mark"] == "absent":
            result[key] = "absent"
    return result


def write_marks(
    conn: sqlite3.Connection,
    day: dt.date,
    changes: Sequence[MarkChange],
    *,
    by: int | None,
    now: dt.datetime,
) -> None:
    """Ставит и снимает отметки одним действием: или все, или ни одной."""
    stamp = moscow.isoformat(now)
    conn.execute("BEGIN")
    try:
        for change in changes:
            if change.mark is None:
                conn.execute(
                    "DELETE FROM attendance WHERE date = ? AND starts = ? AND student_id = ?",
                    (day.isoformat(), change.slot, change.student_id),
                )
                continue
            conn.execute(
                "INSERT INTO attendance (date, starts, student_id, mark, marked_at, marked_by)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (date, starts, student_id) DO UPDATE SET"
                " mark = excluded.mark, marked_at = excluded.marked_at, marked_by = excluded.marked_by",
                (day.isoformat(), change.slot, change.student_id, change.mark, stamp, by),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

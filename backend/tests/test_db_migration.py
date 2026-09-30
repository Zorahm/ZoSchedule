"""Обновление схемы на уже существующей базе.

Тесты работают на пустых временных базах и путь миграции не задевают, поэтому
он проверяется отдельно: база пользователя пересозданию не подлежит, в ней вся
история снимков.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.db import init_db

# Схема снимков до появления второго источника расписания.
_OLD_SCHEMA = """
CREATE TABLE snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at      TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK (status IN ('ok', 'failed')),
    error         TEXT,
    raw_json      TEXT,
    covered_from  TEXT,
    covered_to    TEXT,
    lesson_count  INTEGER NOT NULL DEFAULT 0
);
"""

# Пары до отказа от подгрупп: подгруппа выводилась из состава потока и врала.
_OLD_LESSONS = """
CREATE TABLE lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL,
    date            TEXT    NOT NULL,
    starts_at       TEXT    NOT NULL,
    ends_at         TEXT    NOT NULL,
    time_label      TEXT    NOT NULL,
    discipline      TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    badge           TEXT    NOT NULL,
    subgroup        INTEGER,
    subgroup_source TEXT    NOT NULL,
    dedup_index     INTEGER NOT NULL,
    position        INTEGER NOT NULL
);
"""


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def test_existing_database_gains_the_source_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO snapshots (taken_at, status, lesson_count)"
            " VALUES ('2026-09-01T10:00:00+03:00', 'ok', 7)"
        )

    init_db(path)

    assert "source" in _columns(path, "snapshots")
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT source, lesson_count FROM snapshots").fetchone()
    # Всё, что было в базе до появления импорта, снято парсером сайта.
    assert row == ("site", 7)


def test_existing_lessons_lose_subgroup_columns_but_keep_data(tmp_path: Path) -> None:
    path = tmp_path / "old-lessons.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_OLD_LESSONS)
        conn.execute(
            "INSERT INTO lessons (snapshot_id, date, starts_at, ends_at, time_label, discipline,"
            " kind, badge, subgroup, subgroup_source, dedup_index, position)"
            " VALUES (1, '2026-09-07', '2026-09-07T08:30:00+03:00', '2026-09-07T10:00:00+03:00',"
            " '08:30-10:00', 'Математика', 'лекция', 'ЛЕК', 1, 'slot', 0, 1)"
        )

    init_db(path)

    columns = _columns(path, "lessons")
    assert not {"subgroup", "subgroup_source"} & columns
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT discipline, position FROM lessons").fetchone()
    assert row == ("Математика", 1)


def test_tables_the_schema_no_longer_defines_are_left_alone(tmp_path: Path) -> None:
    """The web app's "Варианты" tables are gone from the schema, not from users' databases."""
    path = tmp_path / "with-variants.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO students (name) VALUES ('Аня Смирнова')")

    init_db(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM students").fetchall() == [("Аня Смирнова",)]


def test_init_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    init_db(path)
    init_db(path)
    assert "source" in _columns(path, "snapshots")

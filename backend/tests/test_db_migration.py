"""Обновление схемы на уже существующей базе.

Тесты работают на пустых временных базах и путь миграции не задевают, поэтому
он проверяется отдельно: база пользователя пересозданию не подлежит, в ней вся
история снимков.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.bot import store as bot_store
from app.bot.store import Target
from app.db import connect, init_db

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


def _old_bot_state(path: Path, *, thread: str) -> None:
    """База бота из времён одного чата: чат, тема и курсор лежали в `bot_state`."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO bot_state (key, value) VALUES (?, ?)",
            [
                ("chat_id", "-100500"),
                ("thread_id", thread),
                ("last_event_id", "12"),
                ("update_offset", "77"),
            ],
        )


def test_the_single_remembered_chat_becomes_the_first_target(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_bot_state(path, thread="7")

    init_db(path)

    with connect(path) as conn:
        assert bot_store.targets(conn) == [Target("-100500", 7)]
        assert bot_store.last_event_id(conn, "-100500") == 12  # the feed position came along
        assert bot_store.update_offset(conn) == 77  # what is not about the chat is untouched
        keys = {row["key"] for row in conn.execute("SELECT key FROM bot_state")}
    assert keys == {"update_offset"}


def test_a_chat_without_a_topic_migrates_without_one(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_bot_state(path, thread="")  # the old code wrote an empty string for "no topic"

    init_db(path)

    with connect(path) as conn:
        assert bot_store.targets(conn) == [Target("-100500", None)]


def test_the_migration_runs_once(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_bot_state(path, thread="7")
    init_db(path)
    with connect(path) as conn:
        bot_store.add_target(conn, "-100600", None)
        bot_store.remove_target(conn, "-100500")

    init_db(path)  # a restart: the removed chat must not come back from the old keys

    with connect(path) as conn:
        assert bot_store.targets(conn) == [Target("-100600", None)]


def test_a_database_without_any_bot_state_gets_no_targets(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"

    init_db(path)

    with connect(path) as conn:
        assert bot_store.targets(conn) == []

"""Схема SQLite и доступ к соединению.

Снимок хранится дважды: сырой ответ сайта в ``snapshots.raw_json`` и
нормализованная проекция в ``lessons``. Сырьё — страховка: если сайт добавит
поле или мы поменяем нормализацию, проекцию можно пересобрать задним числом,
не потеряв историю.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from app import moscow

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at      TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK (status IN ('ok', 'failed')),
    error         TEXT,
    raw_json      TEXT,
    covered_from  TEXT,
    covered_to    TEXT,
    lesson_count  INTEGER NOT NULL DEFAULT 0,
    source        TEXT    NOT NULL DEFAULT 'site' CHECK (source IN ('site', 'import'))
);

CREATE INDEX IF NOT EXISTS idx_snapshots_status_taken
    ON snapshots (status, taken_at DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_source_taken
    ON snapshots (source, status, taken_at DESC);

CREATE TABLE IF NOT EXISTS lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    source_id       TEXT,
    date            TEXT    NOT NULL,
    starts_at       TEXT    NOT NULL,
    ends_at         TEXT    NOT NULL,
    time_label      TEXT    NOT NULL,
    discipline      TEXT    NOT NULL,
    discipline_id   TEXT,
    kind            TEXT    NOT NULL,
    badge           TEXT    NOT NULL,
    room            TEXT,
    building        TEXT,
    building_short  TEXT,
    teacher         TEXT,
    stream          TEXT    NOT NULL DEFAULT '[]',
    dedup_index     INTEGER NOT NULL,
    position        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lessons_snapshot_date
    ON lessons (snapshot_id, date, starts_at);

CREATE TABLE IF NOT EXISTS change_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at      TEXT    NOT NULL,
    snapshot_id      INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    prev_snapshot_id INTEGER REFERENCES snapshots (id) ON DELETE SET NULL,
    kind             TEXT    NOT NULL,
    date             TEXT    NOT NULL,
    payload_json     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_events_detected
    ON change_events (detected_at DESC);

-- Сообщения бота в Telegram. `day` — ключ идемпотентности: для недели это
-- понедельник, для «Сегодня» и изменений — дата. По нему бот не шлёт дубль после
-- перезапуска и знает, что удалять на следующее утро.
CREATE TABLE IF NOT EXISTS bot_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT    NOT NULL,
    kind       TEXT    NOT NULL CHECK (kind IN ('week', 'today', 'changes')),
    day        TEXT    NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    -- Отпечаток пар, которые нарисованы на картинке. Расходится с текущим —
    -- картинку надо перерисовать. Дифф для этого не годится: он не видит, что
    -- расписание на субботу опубликовали позже воскресной картинки.
    fingerprint TEXT
);

CREATE INDEX IF NOT EXISTS idx_bot_messages_kind_day
    ON bot_messages (chat_id, kind, day);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Правки куратора: аудитория пары, названная в чате. Накладывается на пары каждого
-- нового снимка сайта, иначе следующий прогон парсера откатил бы её. По строке на
-- пару (день + начало), новая правка той же пары заменяет прежнюю. Текст сообщения
-- и автор хранятся, чтобы было видно, откуда взялась аудитория.
CREATE TABLE IF NOT EXISTS room_overrides (
    date         TEXT    NOT NULL,
    starts       TEXT    NOT NULL,
    room         TEXT    NOT NULL,
    set_at       TEXT    NOT NULL,
    set_by       INTEGER,
    chat_id      TEXT,
    message_text TEXT    NOT NULL,
    PRIMARY KEY (date, starts)
);

-- Чаты, куда бот пишет: по строке на каждую группу, где доверенный написал /go.
-- Порядок вставки — порядок рассылки. `last_event_id` у каждого чата свой: сбой
-- Telegram в одной группе не должен ни терять изменения для неё, ни слать их
-- повторно в остальные. NULL — новый чат, прошлое для него не новости.
CREATE TABLE IF NOT EXISTS bot_targets (
    chat_id       TEXT    PRIMARY KEY,
    thread_id     INTEGER,
    added_at      TEXT    NOT NULL,
    last_event_id INTEGER
);

"""


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL: планировщик пишет снимки, пока API читает. Без него читатели
    # блокируются на время записи.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")


# Добавленные позже колонки: CREATE TABLE IF NOT EXISTS их в уже созданную
# таблицу не внесёт, а базу пересоздавать нельзя — в ней вся история снимков.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (
        "snapshots",
        "source",
        "ALTER TABLE snapshots ADD COLUMN source TEXT NOT NULL DEFAULT 'site'",
    ),
    ("lessons", "stream", "ALTER TABLE lessons ADD COLUMN stream TEXT NOT NULL DEFAULT '[]'"),
)


# Колонки, которых больше нет в схеме. Деления на подгруппы у группы не бывает:
# `subgroup` была выведена из состава потока и врала. Старые значения нигде не
# читаются, а `subgroup_source NOT NULL` без значения сломал бы вставку.
_DROPPED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("lessons", "subgroup"),
    ("lessons", "subgroup_source"),
)


def _drop_removed_columns(conn: sqlite3.Connection) -> None:
    for table, column in _DROPPED_COLUMNS:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column in {row["name"] for row in rows}:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, statement in _ADDED_COLUMNS:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # Пустой ответ — таблицы ещё нет вообще: её создаст SCHEMA уже с этой
        # колонкой, а ALTER по несуществующей таблице упал бы.
        if not rows:
            continue
        if column not in {row["name"] for row in rows}:
            conn.execute(statement)


def _migrate_single_target(conn: sqlite3.Connection) -> None:
    """Бот помнил один чат в `bot_state`; теперь чаты живут в `bot_targets`.

    Старый чат и общий курсор прочитанных изменений становятся первой строкой.
    Ключи удаляются, чтобы повторный запуск ничего не воскресил.
    """
    state = {
        str(row["key"]): str(row["value"])
        for row in conn.execute(
            "SELECT key, value FROM bot_state WHERE key IN ('chat_id', 'thread_id', 'last_event_id')"
        )
    }
    chat = state.get("chat_id")
    if chat is not None:
        thread = state.get("thread_id")
        cursor = state.get("last_event_id")
        conn.execute(
            "INSERT OR IGNORE INTO bot_targets (chat_id, thread_id, added_at, last_event_id)"
            " VALUES (?, ?, ?, ?)",
            (
                chat,
                int(thread) if thread else None,
                moscow.isoformat(moscow.now()),
                int(cursor) if cursor else None,
            ),
        )
    conn.execute("DELETE FROM bot_state WHERE key IN ('chat_id', 'thread_id', 'last_event_id')")


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        _configure(conn)
        # Строго до SCHEMA: там есть индексы по добавленным позже колонкам, и на
        # уже существующей базе они не создадутся, пока колонки нет.
        _add_missing_columns(conn)
        _drop_removed_columns(conn)
        conn.executescript(SCHEMA)
        _migrate_single_target(conn)


@contextmanager
def connect(path: Path) -> Generator[sqlite3.Connection, None, None]:
    # check_same_thread=False: FastAPI выполняет синхронную зависимость и
    # обработчик в разных потоках пула, и соединение, созданное в одном,
    # используется в другом. Гонки это не создаёт — соединение живёт внутри
    # одного запроса и используется строго последовательно.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    try:
        _configure(conn)
        yield conn
    finally:
        conn.close()

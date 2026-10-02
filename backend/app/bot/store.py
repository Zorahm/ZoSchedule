"""Bot state in SQLite: the chats it posts to, its messages there, and how far each has read events."""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Literal

from app import moscow

Kind = Literal["week", "today", "changes"]

_OFFSET_KEY = "update_offset"


@dataclass(frozen=True, slots=True)
class Target:
    """A chat the bot posts to; `thread` is the forum topic, if the group has topics."""

    chat: str
    thread: int | None


@dataclass(frozen=True, slots=True)
class PostedMessage:
    id: int
    kind: Kind
    day: dt.date
    message_id: int
    fingerprint: str | None


def _row(row: sqlite3.Row) -> PostedMessage:
    kind = str(row["kind"])
    if kind not in ("week", "today", "changes"):
        raise ValueError(f"Неизвестный вид сообщения бота: {kind!r}")
    return PostedMessage(
        id=int(row["id"]),
        kind=kind,  # type: ignore[arg-type]  # narrowed by the check above
        day=dt.date.fromisoformat(str(row["day"])),
        message_id=int(row["message_id"]),
        fingerprint=None if row["fingerprint"] is None else str(row["fingerprint"]),
    )


def record(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    kind: Kind,
    day: dt.date,
    message_id: int,
    fingerprint: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO bot_messages (chat_id, kind, day, message_id, created_at, fingerprint)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, kind, day.isoformat(), message_id, moscow.isoformat(moscow.now()), fingerprint),
    )


def set_fingerprint(conn: sqlite3.Connection, record_id: int, fingerprint: str) -> None:
    conn.execute("UPDATE bot_messages SET fingerprint = ? WHERE id = ?", (fingerprint, record_id))


def find(conn: sqlite3.Connection, *, chat_id: str, kind: Kind, day: dt.date) -> list[PostedMessage]:
    rows = conn.execute(
        "SELECT * FROM bot_messages WHERE chat_id = ? AND kind = ? AND day = ? ORDER BY id",
        (chat_id, kind, day.isoformat()),
    ).fetchall()
    return [_row(row) for row in rows]


def all_of_kind(conn: sqlite3.Connection, *, chat_id: str, kind: Kind) -> list[PostedMessage]:
    rows = conn.execute(
        "SELECT * FROM bot_messages WHERE chat_id = ? AND kind = ? ORDER BY id",
        (chat_id, kind),
    ).fetchall()
    return [_row(row) for row in rows]


def forget(conn: sqlite3.Connection, record_id: int) -> None:
    conn.execute("DELETE FROM bot_messages WHERE id = ?", (record_id,))


def last_event_id(conn: sqlite3.Connection, chat_id: str) -> int | None:
    """None for a chat that has not read the feed yet: history is not announced retroactively."""
    row = conn.execute(
        "SELECT last_event_id FROM bot_targets WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    return None if row is None or row["last_event_id"] is None else int(row["last_event_id"])


def set_last_event_id(conn: sqlite3.Connection, chat_id: str, value: int) -> None:
    conn.execute("UPDATE bot_targets SET last_event_id = ? WHERE chat_id = ?", (value, chat_id))


def _state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def targets(conn: sqlite3.Connection) -> list[Target]:
    """Every chat where /go was written, in the order they were added."""
    rows = conn.execute("SELECT chat_id, thread_id FROM bot_targets ORDER BY rowid").fetchall()
    return [
        Target(str(row["chat_id"]), None if row["thread_id"] is None else int(row["thread_id"]))
        for row in rows
    ]


def add_target(conn: sqlite3.Connection, chat_id: str, thread_id: int | None) -> None:
    """Adds the chat; a repeated /go in a known chat only moves it to another topic."""
    conn.execute(
        "INSERT INTO bot_targets (chat_id, thread_id, added_at) VALUES (?, ?, ?)"
        " ON CONFLICT (chat_id) DO UPDATE SET thread_id = excluded.thread_id",
        (chat_id, thread_id, moscow.isoformat(moscow.now())),
    )


def remove_target(conn: sqlite3.Connection, chat_id: str) -> bool:
    """Stops posting to the chat. Its message ledger stays: the old posts are still there."""
    return conn.execute("DELETE FROM bot_targets WHERE chat_id = ?", (chat_id,)).rowcount > 0


def update_offset(conn: sqlite3.Connection) -> int | None:
    """Where getUpdates resumes; persisted so a restart does not replay old /go commands."""
    raw = _state(conn, _OFFSET_KEY)
    return int(raw) if raw else None


def set_update_offset(conn: sqlite3.Connection, value: int) -> None:
    _set_state(conn, _OFFSET_KEY, str(value))

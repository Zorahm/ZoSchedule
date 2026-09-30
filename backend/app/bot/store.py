"""Bot state in SQLite: which messages it has posted and how far it has read events."""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Literal

from app import moscow

Kind = Literal["week", "today", "changes"]

_LAST_EVENT_KEY = "last_event_id"
_CHAT_KEY = "chat_id"
_THREAD_KEY = "thread_id"
_OFFSET_KEY = "update_offset"


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


def last_event_id(conn: sqlite3.Connection) -> int | None:
    """None until the first run: history must not be announced retroactively."""
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (_LAST_EVENT_KEY,)).fetchone()
    return int(row["value"]) if row else None


def set_last_event_id(conn: sqlite3.Connection, value: int) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (_LAST_EVENT_KEY, str(value)),
    )


def _state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def target(conn: sqlite3.Connection) -> tuple[str, int | None] | None:
    """The chat (and forum topic) where /go was last written, if any."""
    chat = _state(conn, _CHAT_KEY)
    if chat is None:
        return None
    thread = _state(conn, _THREAD_KEY)
    return (chat, int(thread) if thread else None)


def set_target(conn: sqlite3.Connection, chat_id: str, thread_id: int | None) -> None:
    _set_state(conn, _CHAT_KEY, chat_id)
    _set_state(conn, _THREAD_KEY, "" if thread_id is None else str(thread_id))


def update_offset(conn: sqlite3.Connection) -> int | None:
    """Where getUpdates resumes; persisted so a restart does not replay old /go commands."""
    raw = _state(conn, _OFFSET_KEY)
    return int(raw) if raw else None


def set_update_offset(conn: sqlite3.Connection, value: int) -> None:
    _set_state(conn, _OFFSET_KEY, str(value))

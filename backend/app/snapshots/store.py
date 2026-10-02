"""Запись и чтение снимков. SQL живёт только здесь."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Sequence
from typing import cast

from app import moscow
from app.models.changes import ChangeDraft, ChangeEvent, build_event
from app.models.domain import (
    Lesson,
    SnapshotMeta,
    SnapshotSource,
    SnapshotStatus,
)
from app.snapshots.overrides import RoomOverride

_LESSON_COLUMNS = (
    "snapshot_id, source_id, date, starts_at, ends_at, time_label, discipline, "
    "discipline_id, kind, badge, room, building, building_short, teacher, "
    "stream, dedup_index, position"
)


def _text(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise ValueError(f"Поле {key!r} ожидалось строкой, получено {type(value).__name__}")
    return value


def _opt_text(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Поле {key!r} ожидалось строкой или NULL")
    return value


def _int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if not isinstance(value, int):
        raise ValueError(f"Поле {key!r} ожидалось числом")
    return value


def _opt_date(row: sqlite3.Row, key: str) -> dt.date | None:
    raw = _opt_text(row, key)
    return dt.date.fromisoformat(raw) if raw else None


def _meta_from_row(row: sqlite3.Row) -> SnapshotMeta:
    raw_status = _text(row, "status")
    if raw_status == "ok":
        status: SnapshotStatus = "ok"
    elif raw_status == "failed":
        status = "failed"
    else:
        raise ValueError(f"Неизвестный статус снимка: {raw_status!r}")

    raw_source = _text(row, "source")
    source: SnapshotSource = "import" if raw_source == "import" else "site"

    return SnapshotMeta(
        id=_int(row, "id"),
        taken_at=moscow.parse_isoformat(_text(row, "taken_at")),
        status=status,
        source=source,
        error=_opt_text(row, "error"),
        covered_from=_opt_date(row, "covered_from"),
        covered_to=_opt_date(row, "covered_to"),
        lesson_count=_int(row, "lesson_count"),
    )


def _stream(row: sqlite3.Row) -> tuple[str, ...]:
    # Снимки, записанные до появления колонки, приходят с дефолтом '[]'.
    raw = row["stream"]
    if not isinstance(raw, str) or raw == "":
        return ()
    value: object = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("Поле 'stream' ожидалось списком")
    return tuple(item for item in cast("list[object]", value) if isinstance(item, str))


def _lesson_from_row(row: sqlite3.Row) -> Lesson:
    return Lesson(
        source_id=_opt_text(row, "source_id"),
        date=dt.date.fromisoformat(_text(row, "date")),
        starts_at=moscow.parse_isoformat(_text(row, "starts_at")),
        ends_at=moscow.parse_isoformat(_text(row, "ends_at")),
        time_label=_text(row, "time_label"),
        discipline=_text(row, "discipline"),
        discipline_id=_opt_text(row, "discipline_id"),
        kind=_text(row, "kind"),
        badge=_text(row, "badge"),
        room=_opt_text(row, "room"),
        building=_opt_text(row, "building"),
        building_short=_opt_text(row, "building_short"),
        teacher=_opt_text(row, "teacher"),
        stream=_stream(row),
        dedup_index=_int(row, "dedup_index"),
        position=_int(row, "position"),
    )


def save_snapshot(
    conn: sqlite3.Connection,
    *,
    taken_at: dt.datetime,
    raw: object,
    lessons: Sequence[Lesson],
    source: SnapshotSource = "site",
) -> SnapshotMeta:
    """Сохраняет успешный прогон целиком: сырьё плюс нормализованные пары."""
    dates = [lesson.date for lesson in lessons]
    covered_from = min(dates).isoformat() if dates else None
    covered_to = max(dates).isoformat() if dates else None

    cursor = conn.execute(
        "INSERT INTO snapshots"
        " (taken_at, status, raw_json, covered_from, covered_to, lesson_count, source)"
        " VALUES (?, 'ok', ?, ?, ?, ?, ?)",
        (
            moscow.isoformat(taken_at),
            json.dumps(raw, ensure_ascii=False),
            covered_from,
            covered_to,
            len(lessons),
            source,
        ),
    )
    snapshot_id = cursor.lastrowid
    if snapshot_id is None:
        raise RuntimeError("SQLite не вернул id снимка")

    conn.executemany(
        f"INSERT INTO lessons ({_LESSON_COLUMNS}) VALUES ({', '.join('?' * 17)})",
        [
            (
                snapshot_id,
                lesson.source_id,
                lesson.date.isoformat(),
                moscow.isoformat(lesson.starts_at),
                moscow.isoformat(lesson.ends_at),
                lesson.time_label,
                lesson.discipline,
                lesson.discipline_id,
                lesson.kind,
                lesson.badge,
                lesson.room,
                lesson.building,
                lesson.building_short,
                lesson.teacher,
                json.dumps(list(lesson.stream), ensure_ascii=False),
                lesson.dedup_index,
                lesson.position,
            )
            for lesson in lessons
        ],
    )
    return _require(get_snapshot(conn, snapshot_id))


def save_failure(conn: sqlite3.Connection, *, taken_at: dt.datetime, error: str) -> SnapshotMeta:
    """Записывает провалившийся прогон.

    Снимком он не становится и в выдачу не попадает, но по нему видно время
    последней попытки — иначе «обновлено час назад» врёт при лежащем сайте.
    """
    cursor = conn.execute(
        "INSERT INTO snapshots (taken_at, status, error) VALUES (?, 'failed', ?)",
        (moscow.isoformat(taken_at), error),
    )
    snapshot_id = cursor.lastrowid
    if snapshot_id is None:
        raise RuntimeError("SQLite не вернул id снимка")
    return _require(get_snapshot(conn, snapshot_id))


def _require(meta: SnapshotMeta | None) -> SnapshotMeta:
    if meta is None:
        raise RuntimeError("Снимок исчез сразу после вставки")
    return meta


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> SnapshotMeta | None:
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return _meta_from_row(row) if row else None


def latest_ok(
    conn: sqlite3.Connection, *, source: SnapshotSource | None = None
) -> SnapshotMeta | None:
    """Последний успешный снимок. ``source`` сужает поиск до одного источника.

    Без сужения побеждает просто самый свежий: заработавший сайт вытесняет
    залитую выгрузку сам, без ручного переключения.
    """
    sql = "SELECT * FROM snapshots WHERE status = 'ok'"
    params: list[object] = []
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY taken_at DESC, id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return _meta_from_row(row) if row else None


def latest_covering(conn: sqlite3.Connection, day: dt.date) -> SnapshotMeta | None:
    """Самый свежий успешный снимок, в диапазон которого входит день.

    Сайт подчищает прошедшее: в новом снимке вчерашних пар уже нет, и по нему
    вчера выглядит «не опубликованным». Данные о прошедшем дне живут в старых
    снимках.
    """
    row = conn.execute(
        "SELECT * FROM snapshots WHERE status = 'ok' AND covered_from <= ? AND covered_to >= ?"
        " ORDER BY taken_at DESC, id DESC LIMIT 1",
        (day.isoformat(), day.isoformat()),
    ).fetchone()
    return _meta_from_row(row) if row else None


def last_attempt(conn: sqlite3.Connection) -> SnapshotMeta | None:
    row = conn.execute("SELECT * FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1").fetchone()
    return _meta_from_row(row) if row else None


def load_lessons(
    conn: sqlite3.Connection,
    snapshot_id: int,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> list[Lesson]:
    sql = "SELECT * FROM lessons WHERE snapshot_id = ?"
    params: list[object] = [snapshot_id]
    if start is not None:
        sql += " AND date >= ?"
        params.append(start.isoformat())
    if end is not None:
        sql += " AND date <= ?"
        params.append(end.isoformat())
    sql += " ORDER BY date, starts_at, dedup_index"
    return [_lesson_from_row(row) for row in conn.execute(sql, params).fetchall()]


def save_events(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    prev_snapshot_id: int | None,
    detected_at: dt.datetime,
    drafts: Sequence[ChangeDraft],
) -> int:
    conn.executemany(
        "INSERT INTO change_events (detected_at, snapshot_id, prev_snapshot_id, kind, date, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                moscow.isoformat(detected_at),
                snapshot_id,
                prev_snapshot_id,
                draft.kind,
                draft.date.isoformat(),
                draft.model_dump_json(),
            )
            for draft in drafts
        ],
    )
    return len(drafts)


def max_event_id(conn: sqlite3.Connection) -> int:
    """Id последнего события, 0 — если событий ещё не было."""
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS top FROM change_events").fetchone()
    return _int(row, "top")


def events_after(conn: sqlite3.Connection, after_id: int) -> list[ChangeEvent]:
    """События с id больше заданного, от старых к новым."""
    rows = conn.execute(
        "SELECT id, detected_at, payload_json FROM change_events WHERE id > ? ORDER BY id",
        (after_id,),
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def _event_from_row(row: sqlite3.Row) -> ChangeEvent:
    decoded: object = json.loads(_text(row, "payload_json"))
    if not isinstance(decoded, dict):
        raise ValueError("payload_json события не является объектом")
    return build_event(
        cast("dict[str, object]", decoded),
        id=_int(row, "id"),
        detected_at=moscow.parse_isoformat(_text(row, "detected_at")),
    )


def save_room_override(
    conn: sqlite3.Connection,
    *,
    override: RoomOverride,
    set_at: dt.datetime,
    set_by: int | None,
    chat_id: str | None,
    message_text: str,
) -> None:
    """Запоминает аудиторию из сообщения куратора. Правка той же пары заменяет прежнюю."""
    conn.execute(
        "INSERT INTO room_overrides (date, starts, room, set_at, set_by, chat_id, message_text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (date, starts) DO UPDATE SET room = excluded.room, set_at = excluded.set_at,"
        " set_by = excluded.set_by, chat_id = excluded.chat_id,"
        " message_text = excluded.message_text",
        (
            override.day.isoformat(),
            override.start,
            override.room,
            moscow.isoformat(set_at),
            set_by,
            chat_id,
            message_text,
        ),
    )


def room_overrides(conn: sqlite3.Connection) -> list[RoomOverride]:
    rows = conn.execute("SELECT date, starts, room FROM room_overrides").fetchall()
    return [
        RoomOverride(
            day=dt.date.fromisoformat(str(row["date"])),
            start=str(row["starts"]),
            room=str(row["room"]),
        )
        for row in rows
    ]

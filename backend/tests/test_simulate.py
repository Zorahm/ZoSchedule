"""Simulating a week on a copy of the real database."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from app import moscow
from app.bot import store as bot_store
from app.bot.simulate import copy_real_database
from app.config import AppConfig
from app.models.db import connect
from app.snapshots import store
from tests.fakes import Clock, save_demo

TUESDAY = dt.date(2026, 9, 29)
SUNDAY = dt.date(2026, 9, 27)


def _taken(path: Path) -> list[dt.datetime]:
    with connect(path) as conn:
        rows = conn.execute("SELECT taken_at FROM snapshots ORDER BY id").fetchall()
    return [dt.datetime.fromisoformat(str(row["taken_at"])) for row in rows]


def _sunday_morning() -> dt.datetime:
    return dt.datetime(2026, 9, 27, 6, 0, tzinfo=moscow.MOSCOW)


def test_the_copy_is_backdated_and_wiped_of_bot_state_and_the_original_is_untouched(
    config: AppConfig, at: Clock, tmp_path: Path
) -> None:
    at(TUESDAY, "10:00")
    save_demo(config, TUESDAY)
    save_demo(config, TUESDAY)  # a second snapshot: their order must survive
    with connect(config.db_path) as conn:
        bot_store.set_target(conn, "-100", None)
        bot_store.record(conn, chat_id="-100", kind="week", day=TUESDAY, message_id=5)
    original = _taken(config.db_path)
    copy = tmp_path / "copy.db"

    copy_real_database(config.db_path, copy, before=_sunday_morning())

    assert _taken(config.db_path) == original  # the source: not a byte of it changed
    with connect(config.db_path) as conn:
        assert bot_store.target(conn) == ("-100", None)

    shifted = _taken(copy)
    assert shifted == sorted(shifted) and len(shifted) == 2
    assert shifted[-1] == _sunday_morning() - dt.timedelta(hours=1)
    assert shifted[-1] - shifted[0] == original[-1] - original[0]  # one common shift
    with connect(copy) as conn:
        assert bot_store.target(conn) is None
        assert bot_store.all_of_kind(conn, chat_id="-100", kind="week") == []
        latest = store.latest_ok(conn)
        assert latest is not None and latest.lesson_count > 0  # the lessons came along


def test_an_already_old_database_keeps_its_timestamps(
    config: AppConfig, at: Clock, tmp_path: Path
) -> None:
    at(dt.date(2026, 9, 1), "10:00")
    save_demo(config, TUESDAY)
    copy = tmp_path / "copy.db"

    copy_real_database(config.db_path, copy, before=_sunday_morning())

    assert _taken(copy) == _taken(config.db_path)

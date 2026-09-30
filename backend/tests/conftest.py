"""Общие фикстуры: два подготовленных снимка и временная база."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

# A developer's real `.env` must never leak into the test run.
os.environ["ZOSCHEDULE_ENV_FILE"] = ""

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app import moscow  # noqa: E402
from app.config import AppConfig, GroupConfig, PollConfig  # noqa: E402
from app.models.db import connect, init_db  # noqa: E402
from app.models.domain import Lesson  # noqa: E402
from app.parsing.college import RawDay  # noqa: E402
from app.parsing.normalize import parse_days  # noqa: E402
from tests.fakes import Clock, FakeTelegram  # noqa: E402

GROUP = "ОККИПд-307"
_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list[RawDay]:
    payload: object = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return cast("list[RawDay]", payload)


@pytest.fixture
def raw_a() -> list[RawDay]:
    return _load("snapshot_a.json")


@pytest.fixture
def raw_b() -> list[RawDay]:
    return _load("snapshot_b.json")


@pytest.fixture
def lessons_a(raw_a: list[RawDay]) -> list[Lesson]:
    return parse_days(raw_a, group_name=GROUP)


@pytest.fixture
def lessons_b(raw_b: list[RawDay]) -> list[Lesson]:
    return parse_days(raw_b, group_name=GROUP)


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        group=GroupConfig(name=GROUP),
        poll=PollConfig(interval_minutes=60),
        db_path=tmp_path / "test.db",
    )


@pytest.fixture
def conn(config: AppConfig) -> Iterator[sqlite3.Connection]:
    init_db(config.db_path)
    with connect(config.db_path) as connection:
        yield connection


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def at(monkeypatch: pytest.MonkeyPatch) -> Clock:
    def _set(day: dt.date, clock: str = "08:00") -> None:
        hours, minutes = clock.split(":")
        moment = dt.datetime(day.year, day.month, day.day, int(hours), int(minutes), tzinfo=moscow.MOSCOW)
        monkeypatch.setattr(moscow, "now", lambda: moment)

    return _set

"""Обёртка парсера: границы диапазона и поведение при ненайденной группе."""

from __future__ import annotations

import datetime as dt

import pytest

from app import moscow
from app.parsing import adapter, college
from app.parsing.college import RawDay, RawGroup

from tests.conftest import GROUP


class _Recorder:
    def __init__(self, days: list[RawDay]) -> None:
        self.days = days
        self.date_from: dt.datetime | None = None
        self.date_to: dt.datetime | None = None

    async def fetch(
        self, group: RawGroup, date_from: dt.datetime, date_to: dt.datetime
    ) -> list[RawDay]:
        self.date_from = date_from
        self.date_to = date_to
        return self.days


def _patch(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder, *, groups: list[RawGroup]
) -> None:
    async def fake_groups() -> list[RawGroup]:
        return groups

    monkeypatch.setattr(college, "get_groups", fake_groups)
    monkeypatch.setattr(college, "fetch_schedule", recorder.fetch)


async def test_range_is_passed_naive(
    monkeypatch: pytest.MonkeyPatch, raw_a: list[RawDay]
) -> None:
    """Парсер сравнивает границы с наивными датами — aware его роняет.

    Регрессия: осведомлённое о зоне время приводило к TypeError внутри
    parser._filter_days и весь прогон падал в «сайт недоступен».
    """
    recorder = _Recorder(raw_a)
    _patch(monkeypatch, recorder, groups=[{"id": "g1", "name": GROUP}])

    await adapter.run(GROUP)

    assert recorder.date_from is not None and recorder.date_to is not None
    assert recorder.date_from.tzinfo is None
    assert recorder.date_to.tzinfo is None
    assert recorder.date_from < recorder.date_to


async def test_range_keeps_moscow_calendar_day(
    monkeypatch: pytest.MonkeyPatch, raw_a: list[RawDay]
) -> None:
    """Снимаем зону после приведения к Москве, а не до."""
    recorder = _Recorder(raw_a)
    _patch(monkeypatch, recorder, groups=[{"id": "g1", "name": GROUP}])

    # 23:30 UTC — в Москве это уже следующие сутки.
    at = dt.datetime(2026, 9, 7, 23, 30, tzinfo=dt.timezone.utc)
    await adapter.run(GROUP, at=at)

    assert recorder.date_from is not None
    expected = moscow.to_moscow(at).date()
    assert (recorder.date_from + adapter.WINDOW_BACK).date() == expected


async def test_unknown_group_with_empty_result_is_a_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустота по несуществующей группе — не «лето», а ошибка конфига."""
    recorder = _Recorder([])
    _patch(monkeypatch, recorder, groups=[{"id": "x", "name": "ОКБМ-201"}])

    with pytest.raises(adapter.ParserFailure, match="config.toml"):
        await adapter.run(GROUP)


async def test_known_group_with_empty_result_is_valid(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Летом сайт честно отдаёт пусто по существующей группе — это снимок, не сбой."""
    recorder = _Recorder([])
    _patch(monkeypatch, recorder, groups=[{"id": "g1", "name": GROUP}])

    result = await adapter.run(GROUP)
    assert result.lessons == []


async def test_network_error_becomes_parser_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(group: RawGroup, date_from: dt.datetime, date_to: dt.datetime) -> list[RawDay]:
        raise OSError("connection reset")

    async def fake_groups() -> list[RawGroup]:
        return [{"id": "g1", "name": GROUP}]

    monkeypatch.setattr(college, "get_groups", fake_groups)
    monkeypatch.setattr(college, "fetch_schedule", boom)

    with pytest.raises(adapter.ParserFailure, match="connection reset"):
        await adapter.run(GROUP)

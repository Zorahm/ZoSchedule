"""Единственный источник времени на сервере.

Часовой пояс приложения — Москва, независимо от TZ машины: расписание колледжа
живёт в московском времени, и сервер, поднятый в UTC, не должен сдвигать даты
пар. Прямой вызов ``datetime.now()`` в коде проекта запрещён — см. AGENTS.md.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")

_API_NAIVE_FORMAT = "%Y-%m-%d %H:%M:%S"


def now() -> dt.datetime:
    """Текущий момент в московском времени, с осведомлённостью о зоне."""
    return dt.datetime.now(MOSCOW)


def today() -> dt.date:
    return now().date()


def to_moscow(value: dt.datetime) -> dt.datetime:
    """Приводит любой datetime к московскому.

    Наивный считаем уже московским: сайт колледжа отдаёт местное время без
    смещения (``startAt``/``endAt``), и трактовать его как UTC было бы сдвигом
    на три часа.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=MOSCOW)
    return value.astimezone(MOSCOW)


def parse_api_datetime(raw: str) -> dt.datetime:
    """Разбирает ``"2026-07-24 11:50:00"`` из полей startAt/endAt."""
    return dt.datetime.strptime(raw, _API_NAIVE_FORMAT).replace(tzinfo=MOSCOW)


def parse_api_date(raw: str) -> dt.date:
    """Разбирает дату дня: ``"2026-07-24T00:00:00+03:00"`` или ``"2026-07-24"``."""
    return dt.date.fromisoformat(raw[:10])


def isoformat(value: dt.datetime) -> str:
    """ISO со смещением — формат всех меток времени в API и в базе."""
    return to_moscow(value).isoformat()


def parse_isoformat(raw: str) -> dt.datetime:
    return to_moscow(dt.datetime.fromisoformat(raw))

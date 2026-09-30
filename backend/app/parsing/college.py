"""Мост к `parser.py` из корня репозитория.

Единственное место, где живёт возня с sys.path, и единственное место, где
проект знает об устройстве парсера. Сам парсер не редактируется: он контракт,
а не наш код (см. AGENTS.md). Отсюда же берутся типы для его нетипизированных
функций — дальше по коду они уже честные.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import parser as _college  # noqa: E402  # импорт после правки sys.path — иначе не найдётся

RawDay = dict[str, Any]
RawGroup = dict[str, Any]

LESSON_TYPES: dict[str, str] = _college.LESSON_TYPES
BUILDING_NAMES: dict[str, str] = _college.BUILDING_NAMES

# Парсер аннотирует голым `dict` — уточняем до наших псевдонимов здесь, чтобы
# дальше по коду типы были честными.
_fetch_schedule = cast(
    "Callable[[RawGroup, dt.datetime, dt.datetime], Awaitable[list[RawDay]]]",
    _college.fetch_schedule,  # pyright: ignore[reportUnknownMemberType]
)
_get_groups = cast(
    "Callable[[], Awaitable[list[RawGroup]]]",
    _college.get_groups,  # pyright: ignore[reportUnknownMemberType]
)
_normalize = cast("Callable[[str], str]", _college._norm)  # pyright: ignore[reportPrivateUsage]


def normalize_name(value: str) -> str:
    """Сверка названий группы так же, как это делает парсер: без регистра и вида тире."""
    return _normalize(value)


async def get_groups() -> list[RawGroup]:
    return await _get_groups()


async def fetch_schedule(
    group: RawGroup, date_from: dt.datetime, date_to: dt.datetime
) -> list[RawDay]:
    return await _fetch_schedule(group, date_from, date_to)

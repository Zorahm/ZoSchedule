"""Сообщение куратора о смене аудитории → структура.

Пример: «добрый день, в 13.50 у ОККИПд-306,307 пара будет в 314 аудитории».
Разбор намеренно узкий: время, хотя бы одна группа и аудитория должны найтись все
три, иначе это не наше сообщение и бот молчит. Лучше пропустить странную
формулировку, чем поменять расписание по реплике про родительское собрание.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel

from app.parsing.college import normalize_name

DayHint = Literal["today", "tomorrow", "day_after"]

_TIME = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])[.:]([0-5]\d)(?![\d.:])")

# Буквенный префикс, тире, перечень номеров: «ОККИПд-306,307», «оккипд-306 и 307».
# В префиксе от двух букв: «А-12» — это аудитория, не группа.
_GROUPS = re.compile(
    r"([A-Za-zА-Яа-яЁё]{2,})\s*[-‑–—]\s*(\d{2,4}(?:\s*(?:[,;/]|\bи\b)\s*\d{2,4})*)"
)

# Номер аудитории: цифры с возможной буквой или дробью («314», «314а», «104/2»).
_ROOM = r"([^\W_]*\d[^\W_]*(?:[/\-][^\W_]+)?)"
_ROOM_WORD = r"(?:аудитори\w*|ауд\b\.?|кабинет\w*|каб\b\.?)"
_ROOM_BEFORE_WORD = re.compile(rf"{_ROOM}\s*{_ROOM_WORD}", re.IGNORECASE)
_ROOM_AFTER_WORD = re.compile(rf"{_ROOM_WORD}\s*(?:№|n)?\s*{_ROOM}", re.IGNORECASE)

_DAY_WORDS: tuple[tuple[str, DayHint], ...] = (
    (r"\bпослезавтра\b", "day_after"),
    (r"\bзавтра\b", "tomorrow"),
    (r"\bсегодня\b", "today"),
)


class RoomNotice(BaseModel):
    """Что сообщил куратор: у каких групп, во сколько и в какую аудиторию."""

    groups: tuple[str, ...]
    """Названия групп в виде, пригодном для сверки (`college.normalize_name`)."""
    start: dt.time
    room: str
    day_hint: DayHint | None = None
    """Слово «сегодня»/«завтра»/«послезавтра», если куратор его написал."""

    @property
    def start_label(self) -> str:
        return self.start.strftime("%H:%M")

    def mentions(self, group_name: str) -> bool:
        return normalize_name(group_name) in self.groups


def _fold(text: str) -> str:
    return text.replace("ё", "е").replace("Ё", "Е")


def _groups_in(text: str) -> tuple[str, ...]:
    names: list[str] = []
    for prefix, numbers in _GROUPS.findall(text):
        for number in re.findall(r"\d+", numbers):
            name = normalize_name(f"{prefix}-{number}")
            if name not in names:
                names.append(name)
    return tuple(names)


def _room_in(text: str) -> str | None:
    # Раньше «число перед словом»: «в 314 аудитории»; иначе «аудитория 314».
    found = _ROOM_BEFORE_WORD.search(text) or _ROOM_AFTER_WORD.search(text)
    return found.group(1).upper() if found else None


def _day_hint_in(text: str) -> DayHint | None:
    lowered = text.lower()
    for pattern, hint in _DAY_WORDS:
        if re.search(pattern, lowered):
            return hint
    return None


def parse_room_notice(text: str) -> RoomNotice | None:
    """Разбирает сообщение; None, если это не сообщение о смене аудитории."""
    text = _fold(text)
    time_match = _TIME.search(text)
    if time_match is None:
        return None
    groups = _groups_in(text)
    # Время и группы вырезаются до поиска аудитории: иначе «13.50 аудитория» дало бы
    # аудиторию «50», а «ОККИПд-306 и 307 аудитория 314» — «307».
    rest = _GROUPS.sub(" ", text[: time_match.start()] + " " + text[time_match.end() :])
    room = _room_in(rest)
    if not groups or room is None:
        return None
    return RoomNotice(
        groups=groups,
        start=dt.time(int(time_match.group(1)), int(time_match.group(2))),
        room=room,
        day_hint=_day_hint_in(text),
    )

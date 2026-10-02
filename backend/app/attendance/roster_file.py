"""Первичный список группы из файла: староста дальше правит его в самом журнале.

Файл лежит вне git (``roster.txt``): в нём настоящие ФИО, а репозиторий может быть
публичным. В git есть только ``roster.example.txt`` с придуманными именами.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path

from app.attendance import store
from app.attendance.models import RosterEntry
from app.attendance.store import sort_key

logger = logging.getLogger(__name__)


def parse(text: str) -> list[str]:
    """ФИО по строкам. Пустые строки, ``#``-комментарии и номера (``1.``, ``2)``) отбрасываются.

    Повтор одного и того же ФИО берётся один раз: сид не должен падать из-за опечатки в файле.
    """
    names: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = " ".join(raw.split()).strip()
        line = line.lstrip("0123456789").lstrip(".) ").strip() if line[:1].isdigit() else line
        if not line or line.startswith("#"):
            continue
        key = sort_key(line)
        if key not in seen:
            seen.add(key)
            names.append(line)
    return names


def seed(conn: sqlite3.Connection, path: Path, *, now: dt.datetime) -> int:
    """Заводит список из файла, только если журнал ещё ни разу не знал студентов.

    Если староста потом убрал всех, список из файла не воскресает: «пусто» тоже его решение.
    Возвращает, сколько студентов заведено.
    """
    if not store.roster_is_untouched(conn):
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeDecodeError) as error:
        logger.warning("Список группы из %s не прочитан: %s", path, error)
        return 0
    entries: list[RosterEntry] = []
    for name in parse(text):
        try:
            entries.append(RosterEntry(name=name))
        except ValueError:
            logger.warning("Строка списка группы пропущена (слишком короткая или длинная): %r", name)
    if not entries:
        return 0
    students = store.save_roster(conn, entries, now=now)
    logger.info("Список группы заведён из %s: %d студентов", path.name, len(students))
    return len(students)

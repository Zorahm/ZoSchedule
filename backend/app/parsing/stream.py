"""Состав потока: чужие группы, сидящие на паре вместе с нами.

У группы нет деления на подгруппы. Единственное, чем пара отличается для нашей
группы, — поточная лекция: она приходит с полным составом в ``groups[]``.
"""

from __future__ import annotations

import re

from app.parsing.college import normalize_name
from app.parsing.seed import LessonSeed

# Название группы = префикс потока плюс номер после последнего разделителя.
_GROUP_TAIL = re.compile(r"^(?P<prefix>.*[-–—/\s])(?P<tail>[^-–—/\s]+)$")


def stream_of(seed: LessonSeed, *, group_name: str) -> tuple[str, ...]:
    """Чужие группы, которые сидят на этой паре вместе с нами.

    «Лекция с 306» объясняет и чужие лица в аудитории, и то, почему пара
    осталась, когда её сняли у нас.

    Имя сокращается до номера, если префикс у групп общий: в подписи «ОККИПд-»
    один шум. У группы с другого направления префикс свой — там номер без него
    ничего не объясняет, и имя остаётся целиком.
    """
    base = normalize_name(group_name)
    ours = _GROUP_TAIL.match(base)
    names: list[str] = []

    for name in seed.group_names:
        normalized = normalize_name(name)
        if normalized == base:
            continue
        other = _GROUP_TAIL.match(normalized)
        short = _GROUP_TAIL.match(name)
        same_stream = ours is not None and other is not None and other["prefix"] == ours["prefix"]
        names.append(short["tail"] if same_stream and short is not None else name)

    return tuple(names)

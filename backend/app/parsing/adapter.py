"""Прогон парсера: сеть + разбор, без обращения к базе."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from app import moscow
from app.models.domain import Lesson
from app.parsing import college, normalize

logger = logging.getLogger(__name__)

# Тянем заведомо шире, чем показываем: снимок хранит всё, что сайт выложил,
# а границы опубликованного диапазона вычисляются из полученных дат.
WINDOW_BACK = dt.timedelta(days=60)
WINDOW_AHEAD = dt.timedelta(days=300)


class ParserFailure(RuntimeError):
    """Прогон не дал пригодных данных: сеть, ошибка сайта или неизвестная группа."""


@dataclass(frozen=True, slots=True)
class ParseResult:
    raw: list[college.RawDay]
    lessons: list[Lesson]


async def _resolve_group(group_name: str) -> tuple[college.RawGroup, bool]:
    """Ищет группу в списке сайта. Второй элемент — нашлась ли она."""
    try:
        groups = await college.get_groups()
    except Exception as error:  # список групп кэшируется, сеть может лечь только здесь
        logger.warning("Список групп недоступен, идём по имени: %s", error)
        return ({"name": group_name}, False)

    wanted = college.normalize_name(group_name)
    for group in groups:
        name = group.get("name")
        if isinstance(name, str) and college.normalize_name(name) == wanted:
            return (group, True)

    logger.warning("Группа %r не найдена среди %d групп сайта", group_name, len(groups))
    return ({"name": group_name}, False)


async def run(group_name: str, *, at: dt.datetime | None = None) -> ParseResult:
    """Дёргает сайт и возвращает разобранное расписание.

    Бросает ParserFailure на любой негодный результат — решение о том, что
    показывать пользователю, принимается уровнем выше.
    """
    moment = at or moscow.now()
    group, listed = await _resolve_group(group_name)

    # Парсер сравнивает границы диапазона с наивными датами из ответа сайта,
    # поэтому осведомлённое о зоне время его роняет. Снимаем tzinfo уже после
    # приведения к Москве: календарные сутки должны остаться московскими.
    local = moscow.to_moscow(moment).replace(tzinfo=None)

    try:
        raw_days = await college.fetch_schedule(group, local - WINDOW_BACK, local + WINDOW_AHEAD)
    except Exception as error:
        raise ParserFailure(f"Прогон парсера не удался: {error}") from error

    lessons = normalize.parse_days(raw_days, group_name=group_name)

    # Пустой ответ по существующей группе — нормальное лето. Пустой ответ по
    # группе, которой нет в списке сайта, — почти наверняка опечатка в конфиге
    # или переименование группы, и молчать об этом нельзя.
    if not lessons and not listed:
        raise ParserFailure(
            f"Группа «{group_name}» не найдена на сайте и расписание пустое — проверьте config.toml"
        )

    return ParseResult(raw=raw_days, lessons=lessons)

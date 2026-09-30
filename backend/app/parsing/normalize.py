"""Сырой ответ сайта → список нормализованных пар.

Здесь чинится всё, что парсер отдаёт как есть: сайт пишет тип занятия то
«зачет», то «зачёт», аудитория приходит числом, преподаватель — то объектом,
то пустотой. Парсер при этом не трогаем, он контракт.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, cast

from app import moscow
from app.models.domain import RETAKE_KIND, Lesson
from app.parsing import stream
from app.parsing.college import BUILDING_NAMES, LESSON_TYPES, RawDay
from app.parsing.seed import LessonSeed

logger = logging.getLogger(__name__)


def fold(value: str) -> str:
    """Ключ для сверки текста: без регистра и без разницы «ё»/«е».

    Сайт отдаёт «зачет», словарь парсера знает «зачёт» — без свёртки маппинг
    молча не срабатывает и тип занятия теряется.
    """
    return value.strip().lower().replace("ё", "е")


_KIND_BY_FOLDED: dict[str, tuple[str, str]] = {
    fold(name): (name, badge) for name, badge in LESSON_TYPES.items()
}
# Словаря парсера (внешний контракт) этого типа не знает, а сайт его присылает:
# заводим здесь, чтобы регистр не зависел от того, как его напишет сайт.
_KIND_BY_FOLDED[fold(RETAKE_KIND)] = (RETAKE_KIND, "ПЕР")


def _text(source: Mapping[str, Any], key: str) -> str | None:
    value = source.get(key)
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


# Сайт ставит прочерк там, где имени нет. Для UI это отсутствие имени, а не
# имя: иначе в «кто ведёт» появляется преподаватель «-», а вместо честного
# «не указан на сайте» строка с одним символом.
_MISSING_NAMES = frozenset(
    {"-", "--", "---", "–", "—", "−", "?", "не указан", "не указано", "нет"}
)


def teacher_name(value: str | None) -> str | None:
    """Имя преподавателя или None, если на месте имени стоит заглушка."""
    if value is None:
        return None
    return None if fold(value) in _MISSING_NAMES else value


def _mapping(source: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = source.get(key)
    if isinstance(value, Mapping):
        return cast("Mapping[str, Any]", value)
    return None


def _sequence(source: Mapping[str, Any], key: str) -> list[Any]:
    value = source.get(key)
    if isinstance(value, list):
        return cast("list[Any]", value)
    return []


def _mappings(source: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    """Элементы списка, которые вообще похожи на объект. Мусор молча отбрасываем."""
    return [
        cast("Mapping[str, Any]", item)
        for item in _sequence(source, key)
        if isinstance(item, Mapping)
    ]


def kind_and_badge(raw_type: str | None) -> tuple[str, str]:
    """Вид занятия и его бейдж. Словарь общий для сайта и выгрузки из ЛМС."""
    if raw_type is None:
        return ("занятие", "ЗАН")
    known = _KIND_BY_FOLDED.get(fold(raw_type))
    if known is not None:
        return known
    return (raw_type, raw_type[:3].upper())


def _time_bounds(
    raw: Mapping[str, Any], day: dt.date
) -> tuple[dt.datetime, dt.datetime] | None:
    start_raw = _text(raw, "startAt")
    end_raw = _text(raw, "endAt")
    if start_raw and end_raw:
        return (moscow.parse_api_datetime(start_raw), moscow.parse_api_datetime(end_raw))

    # Запасной путь: «11:50-13:20» из поля time, если сайт перестанет слать startAt.
    label = _text(raw, "time")
    if label and "-" in label:
        left, _, right = label.partition("-")
        try:
            start = dt.time.fromisoformat(left.strip())
            end = dt.time.fromisoformat(right.strip())
        except ValueError:
            return None
        return (
            dt.datetime.combine(day, start, tzinfo=moscow.MOSCOW),
            dt.datetime.combine(day, end, tzinfo=moscow.MOSCOW),
        )
    return None


def _seed(raw: Mapping[str, Any], day: dt.date) -> LessonSeed | None:
    bounds = _time_bounds(raw, day)
    if bounds is None:
        logger.warning("Пара без разбираемого времени пропущена: %r", raw.get("id"))
        return None
    starts_at, ends_at = bounds

    discipline = _mapping(raw, "discipline")
    teacher = _mapping(raw, "teacher")
    short = _text(raw, "buildingAbbreviation")
    kind, badge = kind_and_badge(_text(raw, "type"))

    return LessonSeed(
        source_id=_text(raw, "id"),
        date=day,
        starts_at=starts_at,
        ends_at=ends_at,
        time_label=_text(raw, "time") or f"{starts_at:%H:%M}-{ends_at:%H:%M}",
        discipline=(_text(discipline, "name") if discipline else None) or "Без названия",
        discipline_id=_text(discipline, "id") if discipline else None,
        kind=kind,
        badge=badge,
        room=_text(raw, "room"),
        # Короткое имя корпуса читаемее, чем «Корпус "Варшавский"», а плотность
        # вёрстки — часть задачи.
        building=(BUILDING_NAMES.get(short) if short else None) or _text(raw, "building"),
        building_short=short,
        teacher=teacher_name(_text(teacher, "name") if teacher else None),
        group_names=[
            name for item in _mappings(raw, "groups") for name in [_text(item, "name")] if name
        ],
    )


def _seeds_by_day(raw_days: Sequence[RawDay]) -> dict[dt.date, list[LessonSeed]]:
    grouped: defaultdict[dt.date, list[LessonSeed]] = defaultdict(list)
    for day_raw in raw_days:
        raw_date = _text(day_raw, "date")
        if raw_date is None:
            continue
        try:
            day = moscow.parse_api_date(raw_date)
        except ValueError:
            logger.warning("День с неразбираемой датой пропущен: %r", raw_date)
            continue
        for lesson_raw in _mappings(day_raw, "lessons"):
            seed = _seed(lesson_raw, day)
            if seed is not None:
                grouped[day].append(seed)
    return dict(grouped)


def parse_days(raw_days: Sequence[RawDay], *, group_name: str) -> list[Lesson]:
    """Разбирает ответ сайта в пары, проставляя номера и индексы."""
    return build_lessons(_seeds_by_day(raw_days), group_name=group_name)


def build_lessons(
    seeds_by_day: Mapping[dt.date, list[LessonSeed]], *, group_name: str
) -> list[Lesson]:
    """Доводит разобранное сырьё до пар: номера, индексы дедупа.

    Работает от ``LessonSeed``, а не от ответа сайта: выгрузка из ЛМС проходит
    ровно этот же хвост, и правила нумерации у обоих источников общие.
    """
    lessons: list[Lesson] = []
    grouped = seeds_by_day

    for day in sorted(grouped):
        seeds = sorted(grouped[day], key=lambda s: (s.starts_at, s.discipline))
        # Номер пары общий для всего слота: две записи в одно время — это одна
        # пара по счёту, а не две.
        slots = sorted({seed.starts_at for seed in seeds})
        position_by_slot = {slot: index + 1 for index, slot in enumerate(slots)}

        seen: defaultdict[str, int] = defaultdict(int)
        for seed in seeds:
            dedup_key = fold(seed.discipline)
            dedup_index = seen[dedup_key]
            seen[dedup_key] += 1

            lessons.append(
                Lesson(
                    source_id=seed.source_id,
                    date=seed.date,
                    starts_at=seed.starts_at,
                    ends_at=seed.ends_at,
                    time_label=seed.time_label,
                    discipline=seed.discipline,
                    discipline_id=seed.discipline_id,
                    kind=seed.kind,
                    badge=seed.badge,
                    room=seed.room,
                    building=seed.building,
                    building_short=seed.building_short,
                    teacher=seed.teacher,
                    stream=stream.stream_of(seed, group_name=group_name),
                    dedup_index=dedup_index,
                    position=position_by_slot[seed.starts_at],
                )
            )

    return lessons

# Парсер расписания МТИ
#
# Использование:
#   python parser.py                              # выбор группы, интерактивный ввод дат
#   python parser.py --group ОККИПд-207           # конкретная группа, интерактивный ввод дат
#   python parser.py --group ОККИПд-207 --from 01.05.2026
#   python parser.py --group ОККИПд-207 --from 01.05.2026 --to 07.05.2026
#
# Форматы даты: ДД.ММ.ГГГГ  |  ГГГГ-ММ-ДД  |  ДД/ММ/ГГГГ  |  ДД-ММ-ГГГГ

import argparse
import asyncio
import json
import time
import unicodedata
import aiohttp
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta

API_URL       = "https://mti.moscow/srvc?command=schedule"
SCHEDULE_PAGE = "https://mti.moscow/studentu/informacziya-dlya-studentov?tab=schedule"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

_CACHE_FILE = Path(__file__).parent / ".groups_cache.json"
_CACHE_TTL  = 180 * 86400  # ~полгода

_DASHES = "‐‑‒–—―−"

LESSON_TYPES = {
    "лекция":                  "ЛЕК",
    "лабораторный практикум":  "ЛАБ",
    "практическое занятие":    "ПРА",
    "семинар":                 "СЕМ",
    "консультация":            "КОН",
    "зачёт":                   "ЗАЧ",
    "экзамен":                 "ЭКЗ",
}

BUILDING_NAMES = {
    "Л-к.1": "Сокол",
    "В":     "Варшавская",
}

# ── Утилиты ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    for ch in _DASHES:
        s = s.replace(ch, "-")
    return s.lower().strip()

# ── Группы ────────────────────────────────────────────────────────────────────

async def _fetch_groups_from_api() -> list[dict]:
    async with aiohttp.ClientSession() as session:
        await session.get(SCHEDULE_PAGE, headers={"user-agent": _USER_AGENT})
        async with session.post(
            API_URL,
            headers={
                "accept":           "application/json, text/javascript, */*; q=0.01",
                "content-type":     "application/x-www-form-urlencoded; charset=UTF-8",
                "origin":           "https://mti.moscow",
                "referer":          SCHEDULE_PAGE,
                "user-agent":       _USER_AGENT,
                "x-requested-with": "XMLHttpRequest",
            },
            data={"command": "students.list"},
        ) as resp:
            resp.raise_for_status()
            body = await resp.json(content_type=None)

    data = body.get("data", body) if isinstance(body, dict) else body
    raw_items = data.values() if isinstance(data, dict) else data
    groups = [v for v in raw_items if isinstance(v, dict) and "id" in v and "name" in v]

    if not groups:
        raise RuntimeError(f"Список групп пуст. Ответ сервера: {body!r}")

    groups.sort(key=lambda g: g["name"])
    return groups


async def get_groups() -> list[dict]:
    try:
        cached = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - cached.get("ts", 0) < _CACHE_TTL:
            return cached["groups"]
    except Exception:
        pass

    groups = await _fetch_groups_from_api()

    try:
        _CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "groups": groups}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

    return groups


def select_group(groups: list[dict]) -> dict:
    def _search(query: str) -> list[dict]:
        q = _norm(query)
        exact = [g for g in groups if _norm(g["name"]) == q]
        return exact if exact else [g for g in groups if q in _norm(g["name"])]

    print()
    while True:
        raw = input("  Введите название группы: ").strip()
        if not raw:
            continue
        found = _search(raw)
        if len(found) == 1:
            return found[0]
        if found:
            print(f"  Несколько совпадений: {', '.join(g['name'] for g in found)}")
        else:
            print("  Не найдено. Попробуйте ещё раз.")

# ── Сеть ──────────────────────────────────────────────────────────────────────

def _build_page_url(group_id: str | None, group_name: str) -> str:
    if not group_id:
        return SCHEDULE_PAGE
    return (
        "https://mti.moscow/studentu/informacziya-dlya-studentov"
        f"?tab=schedule&schedule-type=students&schedule-id={group_id}"
        f"&schedule-title={quote(group_name, safe='')}"
    )


async def fetch_schedule(group: dict, date_from: datetime, date_to: datetime) -> list[dict]:
    page_url = _build_page_url(group.get("id"), group["name"])
    headers = {
        "accept":           "application/json, text/javascript, */*; q=0.01",
        "accept-language":  "ru,en;q=0.9",
        "content-type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "origin":           "https://mti.moscow",
        "referer":          page_url,
        "user-agent":       _USER_AGENT,
        "x-requested-with": "XMLHttpRequest",
        "sec-fetch-dest":   "empty",
        "sec-fetch-mode":   "cors",
        "sec-fetch-site":   "same-origin",
    }

    async with aiohttp.ClientSession() as session:
        await session.get(page_url, headers={"user-agent": _USER_AGENT})
        async with session.post(
            API_URL,
            headers=headers,
            data={"command": "students.schedule", "groupName": group["name"]},
        ) as resp:
            resp.raise_for_status()
            body = await resp.json(content_type=None)

    if not body.get("success"):
        raise RuntimeError(body.get("message", "Неизвестная ошибка API"))

    return _filter_days(body["data"], date_from, date_to)


def _filter_days(raw: list | dict, date_from: datetime, date_to: datetime) -> list[dict]:
    days: list[dict] = raw if isinstance(raw, list) else list(raw.values())
    result = []
    for day in days:
        if not isinstance(day, dict):
            continue
        try:
            day_date = datetime.strptime(day.get("date", "")[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if date_from <= day_date <= date_to:
            result.append(day)
    return result


# ── Вывод ─────────────────────────────────────────────────────────────────────

def print_schedule(days: list[dict]) -> None:
    if not days:
        print("  Занятий за указанный период нет.")
        return
    for day in days:
        _print_day(day)


def _print_day(day: dict) -> None:
    date_str    = day.get("date", "")[:10]
    day_of_week = day.get("dayOfWeek", "")
    lessons     = day.get("lessons") or []

    tags = ""
    if day.get("isToday"):       tags += "  [сегодня]"
    if day.get("isNearestDay"):  tags += "  [ближайший]"

    print(f"\n┌{'─'*58}┐")
    print(f"│  {day_of_week}, {date_str}{tags:<{48 - len(date_str) - len(day_of_week)}}│")
    print(f"└{'─'*58}┘")

    if not lessons:
        print("   Выходной")
        return

    for lesson in lessons:
        _print_lesson(lesson)


def _print_lesson(lesson: dict) -> None:
    time_str   = lesson.get("time", "?")
    discipline = (lesson.get("discipline") or {}).get("name", "?")
    raw_type   = lesson.get("type", "")
    badge      = LESSON_TYPES.get(raw_type, raw_type[:3].upper())
    building   = lesson.get("buildingAbbreviation") or lesson.get("building", "?")
    room       = lesson.get("room", "?")
    teacher    = (lesson.get("teacher") or {}).get("name", "Преподаватель не указан")

    building_display = BUILDING_NAMES.get(building, building)

    print(f"\n   {time_str}  [{badge}]  {discipline}")
    print(f"   {teacher}")
    print(f"   {building_display}, ауд. {room}")


# ── Ввод дат ──────────────────────────────────────────────────────────────────

def parse_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Неверный формат: «{s}». Используйте ДД.ММ.ГГГГ или ГГГГ-ММ-ДД")


def prompt_date(label: str, default: datetime) -> datetime:
    raw = input(f"  {label} [{default.strftime('%d.%m.%Y')}]: ").strip()
    return parse_date(raw) if raw else default


# ── Точка входа ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Парсер расписания МТИ",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--group", dest="group", metavar="ГРУППА",
        help="название группы (например: ОККИПд-207)",
    )
    parser.add_argument(
        "--from", dest="date_from", metavar="ДАТА",
        help="начало периода (ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)",
    )
    parser.add_argument(
        "--to", dest="date_to", metavar="ДАТА",
        help="конец периода (ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)",
    )
    return parser.parse_args()


async def main() -> None:
    args   = _parse_args()
    today  = datetime.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    if args.group:
        group: dict = {"name": args.group.strip()}
    else:
        print("\n  Расписание МТИ — загружаю группы...")
        try:
            groups = await get_groups()
        except aiohttp.ClientError as e:
            print(f"\n  Ошибка сети при загрузке групп: {e}")
            return
        except RuntimeError as e:
            print(f"\n  {e}")
            return
        group = select_group(groups)

    print(f"\n  Расписание МТИ — {group['name']}\n")

    try:
        if args.date_from or args.date_to:
            date_from = parse_date(args.date_from) if args.date_from else monday
            date_to   = parse_date(args.date_to)   if args.date_to   else sunday
        else:
            date_from = prompt_date("С", monday)
            date_to   = prompt_date("По", sunday)
    except ValueError as e:
        print(f"\n  Ошибка: {e}")
        return

    print(f"\n  Загружаю расписание с {date_from.strftime('%d.%m.%Y')} по {date_to.strftime('%d.%m.%Y')}...")

    try:
        days = await fetch_schedule(group, date_from, date_to)
    except aiohttp.ClientError as e:
        print(f"\n  Ошибка сети: {e}")
        return
    except RuntimeError as e:
        print(f"\n  {e}")
        return

    print_schedule(days)
    print()


if __name__ == "__main__":
    asyncio.run(main())

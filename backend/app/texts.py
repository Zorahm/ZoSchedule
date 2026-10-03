"""User-facing Russian strings and their formatting.

Everything the bot says to people lives here, so wording can change without
touching the logic. Text goes out with Telegram ``parse_mode=HTML``: every value
that comes from the college site is escaped.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Sequence

from app.models.changes import Added, Cancelled, ChangeEvent, Moved, TeacherChanged
from app.models.domain import weekday_ru

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTHS_SHORT = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

STREAM_TAG = "гр. {}"
ROOM_LABEL = "аудитория"
BUILDING_TAG = "корп. {}"
STREAM_LEGEND = "пара вместе с группой {}"
TEACHER_UNKNOWN = "не указан на сайте"
NO_LESSONS = "Выходной"
"""A published day without lessons, retake-only days included: the group has no classes."""
RETAKE_TAG = "ПЕРЕСДАЧА"
NOT_PUBLISHED = "Ещё не опубликовано"
CHANGES_TITLE = "🔔 <b>Изменения в расписании</b>"

# Telegram rejects messages over 4096 characters; keep a margin for the tags.
MESSAGE_LIMIT = 3800
CAPTION_LIMIT = 1024
_MORE_NOTES = "…и ещё {}"

_KIND_LABELS = {
    "лекция": "Лекция",
    "практическое занятие": "Практика",
    "семинар": "Семинар",
    "лабораторный практикум": "ЛР",
    "зачёт": "Зачёт",
    "экзамен": "Экзамен",
    "консультация": "Консультация",
}


def _fold(value: str) -> str:
    return value.strip().lower().replace("ё", "е")


_KIND_LABELS_FOLDED = {_fold(key): label for key, label in _KIND_LABELS.items()}


# Long course names shortened for the week picture only. Change texts keep the full
# name so it matches what the college site says. First match wins per rule; the
# prefix rules cut everything after the base name ("... в профессиональной
# деятельности"), the last one shortens a phrase wherever it stands.
_TITLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^иностранный язык\b.*$", re.IGNORECASE), "Иностранный язык"),
    (re.compile(r"^дискретная математика\b.*$", re.IGNORECASE), "Дискретная математика"),
    (re.compile(r"^теория вероятностей\b.*$", re.IGNORECASE), "Теория вероятностей"),
    (re.compile(r"^(?:адаптивная\s+)?физическая культура\b.*$", re.IGNORECASE), "Физкультура"),
    # A "|" separates the course from its module codes: "Производственная практика | ПМ.04 | ..."
    (re.compile(r"\s*\|.*$"), ""),
    # "Технологическая (проектно-технологическая) практика": the clarification goes.
    (re.compile(r"\s*\([^)]*\)"), ""),
    (re.compile(r"\bинженерно-техническая поддержка\b", re.IGNORECASE), "Техподдержка"),
    (re.compile(r"\bпрофессиональн\w+\s+деятельност\w*", re.IGNORECASE), "проф. деятельности"),
    (re.compile(r"\bинформационн\w+\s+систем\w*", re.IGNORECASE), "ИС"),
)


def short_title(title: str) -> str:
    """The lesson title as it appears on the week picture."""
    result = title.strip()
    for pattern, replacement in _TITLE_RULES:
        result = pattern.sub(replacement, result)
    return result


def kind_label(kind: str) -> str:
    """Short label for a lesson kind; unknown kinds pass through capitalized."""
    known = _KIND_LABELS_FOLDED.get(_fold(kind))
    if known is not None:
        return known
    stripped = kind.strip()
    return stripped[:1].upper() + stripped[1:]


def format_retake(title: str, teacher: str | None, room: str | None) -> str:
    """"Пересдача по предмету {предмет} у {препод} в аудитории {аудитория}".

    Plain text: escape it for whatever it is put into. A missing value is said
    outright rather than left out, as everywhere else in the bot.
    """
    known_teacher = teacher.strip() if teacher else ""
    known_room = room.strip() if room else ""
    who = f"у {known_teacher}" if known_teacher else f"преподаватель {TEACHER_UNKNOWN}"
    where = f"в аудитории {known_room}" if known_room else "аудитория не указана"
    # The template as asked when everything is known; commas keep the gaps readable.
    sep = " " if known_teacher and known_room else ", "
    return f"Пересдача по предмету {title.strip()}{sep}{who}{sep}{where}"


def retake_note(sentence: str, start: str, end: str, day: dt.date | None = None) -> str:
    """One caption line (HTML) about a retake; `day` only when the picture spans several."""
    when = f"{WEEKDAYS_SHORT[day.weekday()]}, {date_short(day)} · " if day else ""
    return f"🔁 {when}{start}–{end} — {_esc(sentence)}"


def with_notes(caption: str, notes: Sequence[str]) -> str:
    """Appends note lines to a caption, within Telegram's 1024 characters.

    What does not fit is counted, not silently dropped: "…и ещё 2".
    """
    if not notes:
        return caption
    lines: list[str] = []
    used = len(caption) + 2  # the blank line before the notes
    for index, note in enumerate(notes):
        is_last = index == len(notes) - 1
        reserve = 0 if is_last else len(_MORE_NOTES.format(len(notes))) + 1
        if used + len(note) + 1 + reserve > CAPTION_LIMIT:
            lines.append(_MORE_NOTES.format(len(notes) - index))
            break
        lines.append(note)
        used += len(note) + 1
    return caption + "\n\n" + "\n".join(lines)


def format_room(room: str | None) -> str | None:
    """«ауд. 208», but «спортзал»: a bare number reads as a random figure."""
    if room is None:
        return None
    trimmed = room.strip()
    if trimmed == "":
        return None
    return f"ауд. {trimmed}" if re.search(r"\d", trimmed) else trimmed


def date_long(day: dt.date) -> str:
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"


def date_short(day: dt.date) -> str:
    return f"{day.day} {MONTHS_SHORT[day.month - 1]}"


def range_long(start: dt.date, end: dt.date) -> str:
    """«28 сентября – 3 октября», or «5–10 октября» inside one month."""
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GENITIVE[end.month - 1]}"
    return f"{date_long(start)} – {date_long(end)}"


def plural(count: int, forms: tuple[str, str, str]) -> str:
    """Russian plural: (1 пара, 2 пары, 5 пар)."""
    mod100 = count % 100
    mod10 = count % 10
    if 11 <= mod100 <= 14:
        form = forms[2]
    elif mod10 == 1:
        form = forms[0]
    elif 2 <= mod10 <= 4:
        form = forms[1]
    else:
        form = forms[2]
    return f"{count} {form}"


def lessons_count(count: int) -> str:
    return plural(count, ("пара", "пары", "пар"))


def _esc(value: str | None) -> str:
    return html.escape(value or "", quote=False)


def _times(label: str) -> str:
    return label.replace("-", "–")


def _start(label: str) -> str:
    return label.split("-")[0]


def _teacher(value: str | None) -> str:
    return _esc(value) if value else TEACHER_UNKNOWN


def _room_or_none(room: str | None) -> str:
    return _esc(format_room(room)) or "аудитория не указана"


def _was_now(was: str, now: str) -> str:
    return f"      Было: {was}\n      Стало: {now}"


def _describe(event: ChangeEvent) -> str:
    """One change as a short block: what happened to which lesson, then was/now."""
    title = _esc(event.discipline)
    if isinstance(event, Moved):
        time_changed = event.from_time != event.to_time
        room_changed = event.from_room != event.to_room
        was: list[str] = []
        now: list[str] = []
        if time_changed:
            was.append(_times(event.from_time))
            now.append(_times(event.to_time))
        if room_changed:
            was.append(_room_or_none(event.from_room))
            now.append(_room_or_none(event.to_room))
        if event.retake:
            head = (
                "🔄 <b>Пересдача перенесена</b>"
                if time_changed
                else "🚪 <b>Пересдача: другая аудитория</b>"
            )
        else:
            head = "🔄 <b>Перенос</b>" if time_changed else "🚪 <b>Другая аудитория</b>"
        # Without a time change the lesson is identified by its time instead.
        suffix = "" if time_changed else f" ({_start(event.to_time)})"
        return f"{head}: {title}{suffix}\n{_was_now(', '.join(was), ', '.join(now))}"
    if isinstance(event, Cancelled):
        label = "Пересдача отменена" if event.retake else "Отменена"
        return f"❌ <b>{label}</b>: {title} ({_start(event.at_time)})"
    if isinstance(event, Added) and event.retake:
        sentence = format_retake(event.discipline, event.teacher, event.room)
        return f"🔁 <b>{_esc(sentence)}</b>\n      {_times(event.at_time)}"
    if isinstance(event, Added):
        details = [_times(event.at_time), format_room(event.room), event.teacher]
        line = " · ".join(_esc(part) for part in details if part)
        return f"➕ <b>Добавлена</b>: {title}\n      {line}"
    if isinstance(event, TeacherChanged):
        label = "Пересдача: другой преподаватель" if event.retake else "Другой преподаватель"
        return (
            f"👤 <b>{label}</b>: {title} ({_start(event.at_time)})\n"
            f"{_was_now(_teacher(event.from_teacher), _teacher(event.to_teacher))}"
        )
    unreachable: object = event
    raise TypeError(f"Неизвестный вид события: {unreachable!r}")


def _sort_key(event: ChangeEvent) -> tuple[dt.date, str, str]:
    time = event.to_time if isinstance(event, Moved) else event.at_time
    return (event.date, time, event.discipline)


def _day_header(day: dt.date) -> str:
    return f"📅 <b>{weekday_ru(day)}, {date_long(day)}</b>"


def format_changes(events: Sequence[ChangeEvent]) -> list[str]:
    """Change messages ready to send. Empty input gives no messages.

    Events are grouped under their day. One message normally; a long batch is
    split on event boundaries, and the day header is repeated on the next
    message so nothing is left without a date.
    """
    if not events:
        return []

    messages: list[str] = []
    current = CHANGES_TITLE
    last_day: dt.date | None = None
    for event in sorted(events, key=_sort_key):
        block = _describe(event)
        header = _day_header(event.date) if event.date != last_day else None
        needed = len(block) + (len(header) + 2 if header else 0) + 2
        if len(current) + needed > MESSAGE_LIMIT:
            messages.append(current)
            current = CHANGES_TITLE
            header = _day_header(event.date)
        if header:
            current += "\n\n" + header
        current += "\n\n" + block
        last_day = event.date
    messages.append(current)
    return messages


def week_caption(start: dt.date, end: dt.date) -> str:
    return f"📅 Расписание на неделю · {range_long(start, end)}"


def day_caption(day: dt.date, today: dt.date) -> str:
    """"Сегодня" / "Завтра" / a plain day label: /go may show a day other than today."""
    weekday = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
    delta = (day - today).days
    lead = "Сегодня" if delta == 0 else "Завтра" if delta == 1 else "Расписание на день"
    return f"📅 {lead} · {weekday[day.weekday()]}, {date_long(day)}"

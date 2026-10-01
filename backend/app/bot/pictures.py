"""Builds the two images from the latest snapshot. No Telegram in here."""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace

from app import moscow
from app.bot import templates, texts, theme
from app.bot.renderer import Renderer
from app.bot.view import WEEK_DAYS, DayView, Header, build_days, week_monday
from app.config import AppConfig
from app.models.db import connect
from app.snapshots import store


@dataclass(frozen=True, slots=True)
class Picture:
    png: bytes
    caption: str
    fingerprint: str
    last_end: str | None = None
    """When the day's last lesson ends, "HH:MM"; None for the week."""


@dataclass(frozen=True, slots=True)
class _Snapshot:
    days: list[DayView]
    header: Header


def target_monday(today: dt.date) -> dt.date:
    """The week to show: from Sunday it is the coming week, otherwise the current one."""
    if today.weekday() == 6:
        return today + dt.timedelta(days=1)
    return week_monday(today)


_NO_DATE = dt.date(2000, 1, 1)


def _fingerprint(html_for: Callable[[Header], str], header: Header, caption: str) -> str:
    """Hash of what the picture would look like, not of the data behind it.

    So a change of template, CSS or title abbreviations redraws the pictures already
    in the chat too. The footer's "updated" date is pinned: a new date alone is no
    reason to redraw. The caption is in: "Завтра" must turn into "Сегодня" overnight.
    """
    page = html_for(replace(header, updated=_NO_DATE))
    return hashlib.sha1((page + caption).encode("utf-8")).hexdigest()


class PictureBuilder:
    def __init__(self, config: AppConfig, renderer: Renderer) -> None:
        self._config = config
        self._renderer = renderer

    def _theme(self) -> theme.Theme:
        return theme.resolve(self._config.bot, moscow.now())

    def _load(self, start: dt.date, count: int) -> _Snapshot | None:
        with connect(self._config.db_path) as conn:
            snapshot = store.latest_ok(conn)
            if snapshot is None:
                return None
            end = start + dt.timedelta(days=count - 1)
            lessons = store.load_lessons(conn, snapshot.id, start=start, end=end)
            days = build_days(lessons, snapshot, start, count, group=self._config.group.name)
            self._restore_past(conn, days)
        header = Header(group=self._config.group.name, updated=snapshot.taken_at.date())
        return _Snapshot(days, header)

    def _restore_past(self, conn: sqlite3.Connection, days: list[DayView]) -> None:
        """Past days the site has already dropped come back from older snapshots.

        Otherwise Monday would flip to "not published" by Wednesday, and the
        picture would be redrawn with a hole in it.
        """
        today = moscow.today()
        group = self._config.group.name
        for index, day in enumerate(days):
            if day.coverage == "published" or day.date >= today:
                continue
            older = store.latest_covering(conn, day.date)
            if older is None:
                continue
            lessons = store.load_lessons(conn, older.id, start=day.date, end=day.date)
            days[index] = build_days(lessons, older, day.date, 1, group=group)[0]

    async def week(self, monday: dt.date) -> Picture | None:
        loaded = self._load(monday, WEEK_DAYS)
        if loaded is None:
            return None
        look = self._theme()
        html = templates.week_html(loaded.days, loaded.header, look)
        caption = texts.with_notes(
            texts.week_caption(monday, monday + dt.timedelta(days=WEEK_DAYS - 1)),
            [
                texts.retake_note(retake.sentence(), retake.start, retake.end, day.date)
                for day in loaded.days
                for retake in day.retakes
            ],
        )
        return Picture(
            png=await self._renderer.render(html),
            caption=caption,
            fingerprint=_fingerprint(
                lambda header: templates.week_html(loaded.days, header, look), loaded.header, caption
            ),
        )

    def day_end(self, day: dt.date) -> str | None:
        """When the day's last lesson ends, "HH:MM", without rendering; None if it has none."""
        loaded = self._load(day, 1)
        if loaded is None or not loaded.days[0].lessons:
            return None
        return loaded.days[0].lessons[-1].end

    async def today(self, day: dt.date, *, force: bool = False) -> Picture | None:
        """None when there is nothing worth posting: no data, unpublished, or a day off."""
        monday = week_monday(day)
        loaded = self._load(monday, 7)
        if loaded is None:
            return None
        view = loaded.days[day.weekday()]
        if not force and (view.coverage == "unpublished" or not view.lessons):
            return None
        strip = loaded.days[:WEEK_DAYS]
        look = self._theme()
        html = templates.day_html(view, strip, loaded.header, look)
        caption = texts.with_notes(
            texts.day_caption(day, moscow.today()),
            [texts.retake_note(retake.sentence(), retake.start, retake.end) for retake in view.retakes],
        )
        return Picture(
            png=await self._renderer.render(html),
            caption=caption,
            fingerprint=_fingerprint(
                lambda header: templates.day_html(view, strip, header, look), loaded.header, caption
            ),
            last_end=view.lessons[-1].end if view.lessons else None,
        )

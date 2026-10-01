"""Какую тему рисовать: светлую или ночную."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from app.config import BotConfig

Theme = Literal["light", "night"]


def is_night(clock: dt.time, start: dt.time, end: dt.time) -> bool:
    """Попадает ли время в ночь [start, end). Ночь может переходить через полночь."""
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def resolve(bot: BotConfig, now: dt.datetime) -> Theme:
    """Тема на момент `now` (московский).

    Картинку нельзя подстроить под тему телефона читателя: она одна на всех в чате.
    Поэтому `auto` решает по времени суток."""
    if bot.theme != "auto":
        return bot.theme
    return "night" if is_night(now.time(), bot.night_from, bot.night_to) else "light"

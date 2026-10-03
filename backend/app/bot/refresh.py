"""Keeping the snapshot fresh: a poll by interval and a forced one before the first post."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Protocol

from app import moscow
from app.config import AppConfig
from app.db import connect
from app.snapshots import store
from app.snapshots.service import RefreshOutcome

logger = logging.getLogger(__name__)


class Refresher(Protocol):
    """What the bot needs from `ScheduleService`: one parser run saved as a snapshot."""

    async def refresh(self) -> RefreshOutcome: ...


class SnapshotRefresher:
    def __init__(self, config: AppConfig, schedule: Refresher | None) -> None:
        self._config = config
        self._schedule = schedule
        self._refreshed: set[str] = set()

    async def refresh(self, *, force: bool = False) -> None:
        """Takes a fresh snapshot when the last attempt is older than the poll interval.

        Once per `poll.interval_minutes`. A failed run still counts as an
        attempt: a site that is down is not hammered every minute.
        """
        if self._schedule is None or not self._config.bot.refresh:
            return
        if not force:
            interval = dt.timedelta(minutes=self._config.poll.interval_minutes)
            with connect(self._config.db_path) as conn:
                attempt = store.last_attempt(conn)
            if attempt is not None and moscow.now() - attempt.taken_at < interval:
                return
        outcome = await self._schedule.refresh()
        if outcome.ok:
            logger.info("Снимок обновлён ботом, изменений: %d", outcome.changes_detected)
        else:
            # Post from the last good snapshot rather than stay silent.
            logger.warning("Бот не смог обновить снимок: %s", outcome.error)

    async def refresh_once(self, key: str) -> None:
        """A forced refresh, once per key: right before the day's or the week's first post."""
        if key in self._refreshed:
            return
        await self.refresh(force=True)
        self.mark_fresh(key)

    def mark_fresh(self, key: str) -> None:
        """The snapshot was just taken: `refresh_once` with this key has nothing left to do."""
        self._refreshed.add(key)

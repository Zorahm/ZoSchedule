"""Оркестрация прогона: парсер → снимок → дифф → события."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass

from app import moscow
from app.config import AppConfig
from app.models.changes import ChangeDraft
from app.models.db import connect, init_db
from app.models.domain import Lesson, SnapshotMeta, SnapshotSource
from app.parsing.adapter import ParserFailure, run as run_parser
from app.snapshots import diff, store

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    ok: bool
    changes_detected: int
    error: str | None


class ScheduleService:
    """Владеет базой и замком на прогон."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()

    @property
    def config(self) -> AppConfig:
        return self._config

    def prepare(self) -> None:
        init_db(self._config.db_path)

    async def refresh(self) -> RefreshOutcome:
        """Один прогон парсера с сохранением результата.

        Замок нужен, чтобы два прогона не писали снимки одновременно — иначе они
        дифферятся друг с другом и рождают фантомные события.
        """
        async with self._lock:
            return await self._run_once()

    async def _run_once(self) -> RefreshOutcome:
        taken_at = moscow.now()
        try:
            result = await run_parser(self._config.group.name, at=taken_at)
        except ParserFailure as error:
            logger.warning("Прогон парсера не удался: %s", error)
            with connect(self._config.db_path) as conn:
                store.save_failure(conn, taken_at=taken_at, error=str(error))
            return RefreshOutcome(ok=False, changes_detected=0, error=str(error))

        snapshot, drafts = self._store_snapshot(
            taken_at=taken_at, raw=result.raw, lessons=result.lessons, source="site"
        )
        if drafts:
            logger.info("Снимок %d: обнаружено изменений — %d", snapshot.id, len(drafts))
        return RefreshOutcome(ok=True, changes_detected=len(drafts), error=None)

    def store_lessons(self, lessons: list[Lesson]) -> int:
        """Сохраняет готовые пары как снимок сайта. Возвращает число найденных изменений.

        Нужен демо-данным бота: они собирают пары сами, минуя парсер.
        """
        _, drafts = self._store_snapshot(
            taken_at=moscow.now(), raw=None, lessons=lessons, source="site"
        )
        return len(drafts)

    def _store_snapshot(
        self,
        *,
        taken_at: dt.datetime,
        raw: object,
        lessons: list[Lesson],
        source: SnapshotSource,
    ) -> tuple[SnapshotMeta, list[ChangeDraft]]:
        """Снимок и события к нему, одной транзакцией.

        Предыдущим берётся снимок ТОГО ЖЕ источника: сравнение снимков из разных
        источников дало бы отмену всего расписания и добавление его же заново.
        """
        with connect(self._config.db_path) as conn:
            conn.execute("BEGIN")
            try:
                previous = store.latest_ok(conn, source=source)
                snapshot = store.save_snapshot(
                    conn, taken_at=taken_at, raw=raw, lessons=lessons, source=source
                )

                drafts: list[ChangeDraft] = []
                if previous is not None:
                    drafts = diff.diff(store.load_lessons(conn, previous.id), lessons)
                    if drafts:
                        store.save_events(
                            conn,
                            snapshot_id=snapshot.id,
                            prev_snapshot_id=previous.id,
                            detected_at=taken_at,
                            drafts=drafts,
                        )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        return (snapshot, drafts)

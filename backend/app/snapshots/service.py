"""Оркестрация прогона: парсер → снимок → дифф → события."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Literal

from app import moscow
from app.config import AppConfig
from app.models.changes import ChangeDraft
from app.db import connect, init_db
from app.models.domain import Lesson, SnapshotMeta, SnapshotSource
from app.parsing.adapter import ParserFailure, run as run_parser
from app.parsing.curator import RoomNotice
from app.snapshots import diff, overrides, store

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    ok: bool
    changes_detected: int
    error: str | None


@dataclass(frozen=True, slots=True)
class RoomCorrection:
    """Чем кончилась правка аудитории по сообщению куратора."""

    status: Literal["applied", "unchanged", "no_lesson", "ambiguous", "no_schedule"]
    day: dt.date | None = None
    changes_detected: int = 0


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

    async def correct_room(
        self, notice: RoomNotice, *, author_id: int | None, chat_id: str | None, text: str
    ) -> RoomCorrection:
        """Ставит аудиторию из сообщения куратора на ближайшую пару этого времени.

        Правка запоминается и накладывается на каждый следующий снимок сайта. Сразу
        сохраняется и новый снимок с ней: дифф с предыдущим даст событие `moved` с
        прежней и новой аудиторией, по нему бот напишет об изменении в чат.
        """
        async with self._lock:
            now = moscow.now()
            with connect(self._config.db_path) as conn:
                latest = store.latest_ok(conn)
                if latest is None:
                    return RoomCorrection("no_schedule")
                current = store.load_lessons(conn, latest.id)

            located = overrides.locate(
                current, start=notice.start_label, now=now, day_hint=notice.day_hint
            )
            if located is None:
                return RoomCorrection("no_lesson")
            day, lessons = located
            if len(lessons) > 1:
                # Две пары в одном слоте: какую менять, из сообщения не понять.
                return RoomCorrection("ambiguous", day)
            if lessons[0].room == notice.room:
                return RoomCorrection("unchanged", day)

            override = overrides.RoomOverride(day=day, start=notice.start_label, room=notice.room)
            with connect(self._config.db_path) as conn:
                store.save_room_override(
                    conn,
                    override=override,
                    set_at=now,
                    set_by=author_id,
                    chat_id=chat_id,
                    message_text=text,
                )
            # Тот же источник, что у правимого снимка: дифф считается внутри источника.
            _, drafts = self._store_snapshot(
                taken_at=now, raw=None, lessons=current, source=latest.source
            )
            logger.info(
                "Куратор %s: %s %s, аудитория %s, изменений: %d",
                author_id,
                day,
                notice.start_label,
                notice.room,
                len(drafts),
            )
            return RoomCorrection("applied", day, len(drafts))

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
                # Правки куратора переживают прогон парсера: сайт о них не знает.
                lessons = overrides.apply_room_overrides(lessons, store.room_overrides(conn))
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

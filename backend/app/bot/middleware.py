"""Remembers how far the bot has read, across restarts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.bot import store as bot_store
from app.db import connect


class UpdateOffset(BaseMiddleware):
    """Skips updates that were already handled, so a restart does not replay /go.

    aiogram keeps its polling offset in memory only: after a restart Telegram hands
    the last batch out again. The offset lives in SQLite instead, as it always did.
    Outer middleware on `dp.update`.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        with connect(self._db_path) as conn:
            seen = bot_store.update_offset(conn)
        if seen is not None and event.update_id < seen:
            return None

        try:
            result = await handler(event, data)
        except Exception:
            # A poison update must not be handed out again on every restart.
            self._save(event.update_id + 1)
            raise
        self._save(event.update_id + 1)
        return result

    def _save(self, offset: int) -> None:
        with connect(self._db_path) as conn:
            bot_store.set_update_offset(conn, offset)

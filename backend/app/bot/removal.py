"""Taking messages down by hand: one message by id, or everything the bot has posted."""

from __future__ import annotations

from app.bot import errors
from app.bot import store as bot_store
from app.bot.context import BotContext
from app.bot.store import Kind, Target
from app.db import connect

_KINDS: tuple[Kind, ...] = ("week", "today", "changes")


class Remover:
    def __init__(self, ctx: BotContext) -> None:
        self._ctx = ctx

    async def remove_message(self, chat_id: str, message_id: int) -> None:
        """Deletes a message of the bot. Telegram errors propagate: the caller tells the person.

        A message from the ledger stays there, marked, so the daily cycle does not post
        it again a minute later. One the ledger never knew (a reply, a notice) leaves no trace.
        """
        await errors.tolerate(
            self._ctx.bot.delete_message(chat_id=chat_id, message_id=message_id),
            errors.NOT_FOUND,
        )
        with connect(self._ctx.config.db_path) as conn:
            bot_store.mark_removed(conn, chat_id=chat_id, message_id=message_id)

    async def remove_posts(self, target: Target | None = None, *, kind: Kind | None = None) -> int:
        """Deletes the pictures and texts the bot has up, of one kind or all. Returns how many."""
        removed = 0
        for chat in self._ctx.scope(target):
            with connect(self._ctx.config.db_path) as conn:
                up = [
                    message
                    for each in ((kind,) if kind else _KINDS)
                    for message in bot_store.all_of_kind(conn, chat_id=chat.chat, kind=each)
                ]
            for message in up:
                await self.remove_message(chat.chat, message.message_id)
                removed += 1
        return removed

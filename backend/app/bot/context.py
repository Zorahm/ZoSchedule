"""What the parts of the bot share: config, the Telegram client, the picture builder and the chats.

The posting, updating and attendance parts all talk to the same chats through the same
client; keeping that here stops each of them from growing its own copy.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.bot import errors
from app.bot import store as bot_store
from app.bot.store import Target
from app.config import AppConfig
from app.db import connect
from app.render.pictures import Picture, PictureBuilder
from app.render.renderer import Renderer

logger = logging.getLogger(__name__)


class BotContext:
    def __init__(self, config: AppConfig, bot: Bot, renderer: Renderer) -> None:
        self.config = config
        self.bot = bot
        self.renderer = renderer
        self.pictures = PictureBuilder(config, renderer)

    # -- target chats ---------------------------------------------------------

    def load_targets(self) -> list[Target]:
        """The chats to post to: every one saved by /go, in the order they were added.

        The configured `chat_id` stands in only while none is saved. It is written down
        like a /go chat, because each chat keeps its own read position in the feed.
        """
        with connect(self.config.db_path) as conn:
            saved = bot_store.targets(conn)
            if not saved and self.config.bot.chat_id:
                bot_store.add_target(conn, self.config.bot.chat_id, self.config.bot.thread_id)
                saved = bot_store.targets(conn)
        return saved

    def add_target(self, chat_id: str, thread_id: int | None) -> None:
        with connect(self.config.db_path) as conn:
            bot_store.add_target(conn, chat_id, thread_id)

    def remove_target(self, chat_id: str) -> bool:
        """Stops posting to the chat. True if the bot was posting there."""
        with connect(self.config.db_path) as conn:
            return bot_store.remove_target(conn, chat_id)

    def scope(self, target: Target | None) -> list[Target]:
        """One chat when asked for it, else all of them."""
        return self.load_targets() if target is None else [target]

    # -- messages -------------------------------------------------------------

    async def send_photo(self, target: Target, picture: Picture) -> int:
        sent = await self.bot.send_photo(
            chat_id=target.chat,
            photo=BufferedInputFile(picture.png, "schedule.png"),
            caption=picture.caption,
            message_thread_id=target.thread,
            disable_notification=self.config.bot.silent,
        )
        return sent.message_id

    async def retire(
        self, target: Target, message: bot_store.PostedMessage, *, unpin: bool = False
    ) -> None:
        """Deletes a message and forgets it. Keeps the record only if Telegram was unreachable."""
        try:
            if unpin:
                await self._unpin(target, message)
            await errors.tolerate(
                self.bot.delete_message(chat_id=target.chat, message_id=message.message_id),
                errors.ALREADY_GONE,
            )
        except errors.TELEGRAM_ERRORS as error:
            if errors.is_transient(error):
                logger.warning("Telegram недоступен, удалю позже: %s", error)
                return
            logger.warning("Не удалось удалить сообщение %d: %s", message.message_id, error)
        with connect(self.config.db_path) as conn:
            bot_store.forget(conn, message.id)

    async def _unpin(self, target: Target, message: bot_store.PostedMessage) -> None:
        """Best effort: a refused unpin (no "pin messages" right) must not keep the message alive.

        Deleting a pinned message unpins it anyway. A transient failure still propagates,
        so the whole retire is retried later.
        """
        try:
            await errors.tolerate(
                self.bot.unpin_chat_message(chat_id=target.chat, message_id=message.message_id),
                errors.NOT_PINNED,
            )
        except errors.TELEGRAM_ERRORS as error:
            if errors.is_transient(error):
                raise
            logger.warning("Не удалось открепить сообщение %d: %s", message.message_id, error)

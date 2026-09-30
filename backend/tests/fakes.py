"""Doubles for the bot tests: a Telegram behind a real `aiogram.Bot`, and a renderer.

The bot is genuine, so requests are built by aiogram itself; only the wire is fake.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncGenerator, Callable
from typing import Any, cast

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import (
    DeleteMessage,
    EditMessageMedia,
    GetChatMember,
    GetMe,
    GetUpdates,
    PinChatMessage,
    SendMessage,
    SendPhoto,
    SetMyCommands,
    TelegramMethod,
    UnpinChatMessage,
)
from aiogram.enums import ChatMemberStatus
from aiogram.methods.base import TelegramType
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    InputFile,
    Message,
    Update,
    User,
)

from app.bot import demo
from app.bot.runner import make_bot
from app.config import AppConfig
from app.snapshots.service import ScheduleService

BOT_ID = 4242
BOT_USERNAME = "zoschedulebot"
_TOKEN = f"{BOT_ID}:TEST"

Clock = Callable[..., None]
"""`at(day, clock="08:00")`: pins the bot's idea of now."""


def save_demo(config: AppConfig, today: dt.date) -> None:
    service = ScheduleService(config)
    service.prepare()
    service.store_lessons(demo.build_schedule(today))


class FakeSession(BaseSession):
    """Answers every method the bot uses and records what was asked."""

    def __init__(self, telegram: FakeTelegram) -> None:
        super().__init__()
        self._telegram = telegram

    async def close(self) -> None:
        return None

    def stream_content(
        self, url: str, *args: Any, **kwargs: Any
    ) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        if isinstance(method, GetUpdates) and not self._telegram.updates:
            await asyncio.sleep(0.01)  # a long poll that found nothing
        return cast(TelegramType, self._telegram.answer(method))


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | str]] = []
        self.texts: list[str] = []
        self.captions: list[str] = []
        self.threads: list[int | None] = []
        self.silent: list[bool | None] = []
        """The `disable_notification` flag of every message and photo sent, in order."""
        self.member_status = "administrator"
        self.updates: list[list[Update]] = []
        """Batches `getUpdates` hands out, one per call; then it finds nothing."""
        self._failures: dict[type[TelegramMethod[Any]], tuple[TelegramAPIError, int | None]] = {}
        self.tried: list[str] = []
        """Every method asked for, failed or not, by class name."""
        self._next = 100
        self.bot: Bot = make_bot(_TOKEN, FakeSession(self))

    def fail(
        self,
        method: type[TelegramMethod[Any]],
        error: TelegramAPIError | None,
        *,
        times: int | None = None,
    ) -> None:
        """Makes calls of `method` raise `error`: `times` of them, or all (None: works again)."""
        if error is None:
            self._failures.pop(method, None)
        else:
            self._failures[method] = (error, times)

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]

    def _new_message(self, chat_id: object) -> Message:
        self._next += 1
        chat = Chat(id=int(str(chat_id)), type="supergroup")
        return Message(message_id=self._next, date=dt.datetime.now(dt.UTC), chat=chat)

    def answer(self, method: TelegramMethod[Any]) -> object:
        self.tried.append(type(method).__name__)
        failing = self._failures.get(type(method))
        if failing is not None:
            error, left = failing
            if left is not None:
                if left <= 1:
                    del self._failures[type(method)]
                else:
                    self._failures[type(method)] = (error, left - 1)
            raise error

        if isinstance(method, SendMessage):
            sent = self._new_message(method.chat_id)
            self.calls.append(("message", sent.message_id))
            self.texts.append(method.text)
            self.threads.append(method.message_thread_id)
            self.silent.append(method.disable_notification)
            return sent
        if isinstance(method, SendPhoto):
            sent = self._new_message(method.chat_id)
            self.calls.append(("photo", sent.message_id))
            self.captions.append(method.caption or "")
            self.threads.append(method.message_thread_id)
            self.silent.append(method.disable_notification)
            assert isinstance(method.photo, InputFile)
            return sent
        if isinstance(method, EditMessageMedia):
            assert method.message_id is not None
            self.calls.append(("edit", method.message_id))
            return self._new_message(method.chat_id)
        if isinstance(method, DeleteMessage):
            self.calls.append(("delete", method.message_id))
            return True
        if isinstance(method, PinChatMessage):
            self.calls.append(("pin", method.message_id))
            return True
        if isinstance(method, UnpinChatMessage):
            assert method.message_id is not None
            self.calls.append(("unpin", method.message_id))
            return True
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="Member")
            if self.member_status == "administrator":
                return ChatMemberAdministrator.model_construct(status="administrator", user=user)
            return ChatMemberMember(status=ChatMemberStatus.MEMBER, user=user)
        if isinstance(method, GetUpdates):
            return self.updates.pop(0) if self.updates else []
        if isinstance(method, GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="ZoSchedule", username=BOT_USERNAME)
        if isinstance(method, SetMyCommands):
            return True
        raise AssertionError(f"the bot called {type(method).__name__}, which the fake lacks")


class FakeRenderer:
    async def render(self, html: str) -> bytes:
        return html.encode("utf-8")

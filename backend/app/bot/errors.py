"""How the bot reads aiogram errors.

aiogram has no dedicated classes for "message is not modified" or "message to
delete not found", so those stay a text match on `TelegramBadRequest`.
"""

from __future__ import annotations

from collections.abc import Awaitable

from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

PROXY_ERRORS = (ProxyError, ProxyConnectionError, ProxyTimeoutError)
"""aiohttp-socks raises three unrelated classes, none of them an aiogram or aiohttp error."""
TELEGRAM_ERRORS = (TelegramAPIError, ClientDecodeError, *PROXY_ERRORS)
"""Everything a Bot call raises for reasons outside our code."""

NOT_MODIFIED = ("message is not modified",)
ALREADY_GONE = ("not found", "can't be deleted")
NOT_FOUND = ("not found",)
"""For a deletion by hand: "can't be deleted" must be told to the person, not taken for success."""
NOT_PINNED = ("not found", "not modified")
MESSAGE_LOST = ("message to edit not found", "message can't be edited")


def is_transient(
    error: TelegramAPIError | ClientDecodeError | ProxyError | ProxyConnectionError | ProxyTimeoutError,
) -> bool:
    """True when the same call may succeed later: keep the record and retry.

    Anything else (bad id, no rights, kicked from the chat) will not fix itself.
    """
    if isinstance(error, ClientDecodeError):
        return True  # not the Bot API at all: a proxy or gateway error page
    if isinstance(error, PROXY_ERRORS):
        return True  # the configured proxy is down or slow: Telegram itself was never asked
    if isinstance(error, TelegramEntityTooLarge):
        return False  # a network-error subclass, but resending the same file changes nothing
    return isinstance(error, TelegramNetworkError | TelegramServerError | TelegramRetryAfter)


def says(error: TelegramBadRequest, phrases: tuple[str, ...]) -> bool:
    text = error.message.lower()
    return any(phrase in text for phrase in phrases)


async def tolerate(call: Awaitable[object], phrases: tuple[str, ...]) -> bool:
    """Awaits `call`; False if Telegram answers with one of the harmless phrases.

    Other failures propagate.
    """
    try:
        await call
    except TelegramBadRequest as error:
        if says(error, phrases):
            return False
        raise
    return True

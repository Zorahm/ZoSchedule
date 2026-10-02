"""Проверка запуска мини-приложения: подпись Telegram вместо паролей.

Telegram кладёт в запуск строку ``initData`` и подписывает её ключом, выведенным из токена
бота. Подделать подпись без токена нельзя, поэтому она и есть вход: кто староста, сервер
узнаёт из ``user.id`` внутри подписанной строки.
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import parse_qsl

MAX_AGE = dt.timedelta(hours=24)
"""Старше суток запуск не принимаем: староста откроет журнал заново."""
_FUTURE_SLACK = dt.timedelta(minutes=5)


class AuthError(Exception):
    """Запуску нельзя верить. ``code`` отличает просроченный запуск от подделки."""

    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class TelegramUser:
    id: int


def _secret(token: str) -> bytes:
    return hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()


def sign(fields: dict[str, str], token: str) -> str:
    """Подпись набора полей, как её ставит Telegram. Нужна проверкам и демо-запуску."""
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    return hmac.new(_secret(token), check.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_init_data(raw: str, token: str, *, now: dt.datetime) -> TelegramUser:
    fields = dict(parse_qsl(raw, keep_blank_values=True))
    received = fields.pop("hash", "")
    if not received:
        raise AuthError("unauthorized", "Откройте журнал из чата с ботом.")
    if not hmac.compare_digest(sign(fields, token), received):
        raise AuthError("unauthorized", "Подпись Telegram не сошлась. Откройте журнал из чата с ботом.")

    try:
        issued = dt.datetime.fromtimestamp(int(fields["auth_date"]), dt.UTC)
    except (KeyError, ValueError, OverflowError, OSError):
        raise AuthError("unauthorized", "В запуске нет даты. Откройте журнал заново.") from None
    if now - issued > MAX_AGE:
        raise AuthError("expired", "Сессия устарела. Закройте журнал и откройте его заново из чата с ботом.")
    if issued - now > _FUTURE_SLACK:
        raise AuthError("unauthorized", "Часы запуска не сходятся. Откройте журнал заново.")

    try:
        user = json.loads(fields["user"])
        user_id = user["id"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise AuthError("unauthorized", "В запуске нет пользователя. Откройте журнал из личного чата с ботом.") from None
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise AuthError("unauthorized", "В запуске нет пользователя. Откройте журнал из личного чата с ботом.")
    return TelegramUser(id=user_id)

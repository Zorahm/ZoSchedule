"""Подпись запуска мини-приложения: принимаем только то, что подписал Telegram."""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlencode

import pytest

from app import moscow
from app.web import auth

TOKEN = "4242:TEST"
NOW = dt.datetime(2026, 9, 29, 9, 0, tzinfo=moscow.MOSCOW)


def init_data(
    user_id: int | None = 7, *, token: str = TOKEN, issued: dt.datetime = NOW, extra: dict[str, str] | None = None
) -> str:
    """Строка, какую открывшему мини-приложение отдаёт Telegram."""
    fields = {"auth_date": str(int(issued.timestamp())), "query_id": "AAH", **(extra or {})}
    if user_id is not None:
        fields["user"] = json.dumps({"id": user_id, "first_name": "Староста"}, ensure_ascii=False)
    fields["hash"] = auth.sign(fields, token)
    return urlencode(fields)


def test_a_signed_launch_names_the_user() -> None:
    assert auth.verify_init_data(init_data(7), TOKEN, now=NOW).id == 7


def test_a_launch_signed_with_another_token_is_refused() -> None:
    with pytest.raises(auth.AuthError) as raised:
        auth.verify_init_data(init_data(7, token="1:OTHER"), TOKEN, now=NOW)

    assert raised.value.code == "unauthorized"


def test_changing_the_user_after_signing_breaks_the_signature() -> None:
    genuine = init_data(7)
    forged = genuine.replace("%22id%22%3A+7", "%22id%22%3A+8")
    assert forged != genuine

    with pytest.raises(auth.AuthError):
        auth.verify_init_data(forged, TOKEN, now=NOW)


def test_a_launch_without_a_hash_is_refused() -> None:
    with pytest.raises(auth.AuthError):
        auth.verify_init_data("auth_date=1&user=%7B%7D", TOKEN, now=NOW)


def test_an_old_launch_is_expired_and_says_so() -> None:
    old = NOW - auth.MAX_AGE - dt.timedelta(minutes=1)

    with pytest.raises(auth.AuthError) as raised:
        auth.verify_init_data(init_data(7, issued=old), TOKEN, now=NOW)

    assert raised.value.code == "expired"


def test_a_launch_from_the_future_is_refused() -> None:
    with pytest.raises(auth.AuthError):
        auth.verify_init_data(init_data(7, issued=NOW + dt.timedelta(hours=2)), TOKEN, now=NOW)


def test_a_launch_without_a_user_is_refused() -> None:
    with pytest.raises(auth.AuthError, match="пользовател"):
        auth.verify_init_data(init_data(None), TOKEN, now=NOW)


def test_a_user_id_that_is_not_a_number_is_refused() -> None:
    fields = {"auth_date": str(int(NOW.timestamp())), "user": json.dumps({"id": "7"})}
    fields["hash"] = auth.sign(fields, TOKEN)

    with pytest.raises(auth.AuthError):
        auth.verify_init_data(urlencode(fields), TOKEN, now=NOW)

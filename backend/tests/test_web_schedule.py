"""Ручка расписания: открыта любому со своей подписью Telegram, староста узнаётся по флагу."""

from __future__ import annotations

import datetime as dt

from app.config import AppConfig
from tests.fakes import Clock, save_demo
from tests.test_web_api import HEADMAN, STRANGER, TUE, FakeSender, auth, call, running


async def test_the_schedule_wants_a_signed_launch_but_not_a_headman(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    async with running(config, FakeSender()) as client:
        nothing, _ = await call(client, "GET", "/api/schedule")
        stranger, seen = await call(client, "GET", "/api/schedule", headers=auth(STRANGER))
        headman, head = await call(client, "GET", "/api/schedule", headers=auth(HEADMAN))

    assert nothing == 401
    assert stranger == 200 and seen["headman"] is False
    assert headman == 200 and head["headman"] is True


async def test_days_tell_lessons_from_an_unpublished_date(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    far = TUE + dt.timedelta(days=200)
    async with running(config, FakeSender()) as client:
        _, body = await call(
            client, "GET", f"/api/schedule?from={TUE}&to={TUE + dt.timedelta(days=60)}", headers=auth(STRANGER),
        )
        _, later = await call(
            client, "GET", f"/api/schedule?from={far - dt.timedelta(days=1)}&to={far}", headers=auth(STRANGER)
        )

    today = body["days"][0]
    assert today["state"] == "lessons" and today["lessons"]
    assert {"start", "end", "title", "kind", "teacher", "room"} <= set(today["lessons"][0])
    assert "changes" not in today  # только актуальные пары
    assert today["lessons"][0]["building"] == "Сокол"  # полное название, как на картинках
    assert body["published_to"] is not None
    assert [day["state"] for day in later["days"]] == ["unpublished", "unpublished"]  # не «Выходной»


async def test_a_too_wide_or_backwards_range_is_a_400(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    async with running(config, FakeSender()) as client:
        wide, _ = await call(
            client, "GET", f"/api/schedule?from={TUE}&to={TUE + dt.timedelta(days=90)}", headers=auth()
        )
        backwards, _ = await call(
            client, "GET", f"/api/schedule?from={TUE}&to={TUE - dt.timedelta(days=1)}", headers=auth()
        )
        far, _ = await call(
            client, "GET", f"/api/schedule?from={TUE + dt.timedelta(days=500)}&to={TUE + dt.timedelta(days=501)}",
            headers=auth(),
        )

    assert wide == backwards == far == 400

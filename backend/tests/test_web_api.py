"""Сервер журнала целиком: вход, отметки, список группы, картинка, статика."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from app.attendance.models import JournalDay
from app.attendance.report import ReportError
from app.config import AppConfig, BotConfig
from app.web import server
from tests.fakes import Clock, save_demo
from tests.test_web_auth import TOKEN, init_data

TUE = dt.date(2026, 9, 29)
HEADMAN, OWNER, STRANGER = 7, 8, 9


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, JournalDay, bool]] = []
        self.refuse: ReportError | None = None

    async def send_attendance_report(self, user_id: int, day: JournalDay, *, titles: bool) -> None:
        if self.refuse is not None:
            raise self.refuse
        self.sent.append((user_id, day, titles))


Reply = tuple[int, dict[str, Any]]


async def call(client: TestClient[Any, Any], method: str, path: str, **kwargs: Any) -> Reply:
    """Status and JSON of one request, read while the client is still open."""
    async with client.request(method, path, **kwargs) as reply:
        return reply.status, await reply.json()


@asynccontextmanager
async def running(config: AppConfig, sender: FakeSender) -> AsyncGenerator[TestClient[Any, Any]]:
    bot = BotConfig(token=SecretStr(TOKEN), headmen=[HEADMAN], trusted_users=[OWNER])
    app = server.build_app(config.model_copy(update={"bot": bot}), sender)
    async with TestClient(TestServer(app)) as client:
        yield client


def auth(user: int = HEADMAN, *, token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": "tma " + init_data(user, token=token)}


async def test_the_api_wants_a_signed_launch(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    async with running(config, FakeSender()) as client:
        nothing, _ = await call(client, "GET", "/api/journal")
        forged, body = await call(client, "GET", "/api/journal", headers=auth(token="1:OTHER"))

    assert nothing == 401 and forged == 401
    assert body["error"] == "unauthorized"


async def test_a_stranger_with_a_genuine_launch_is_still_refused(
    config: AppConfig, at: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    at(TUE, "10:00")
    async with running(config, FakeSender()) as client:
        with caplog.at_level("WARNING"):
            status, body = await call(client, "GET", "/api/journal", headers=auth(STRANGER))
        marks, _ = await call(client, "POST", "/api/marks", headers=auth(STRANGER),
                              json={"date": "2026-09-29", "changes": []})

    assert status == 403 and marks == 403 and body["error"] == "forbidden"
    assert f"пользователю {STRANGER}" in caplog.text  # the owner finds the id there


async def test_the_headman_and_the_owner_both_get_in(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    async with running(config, FakeSender()) as client:
        for user in (HEADMAN, OWNER):
            status, body = await call(client, "GET", "/api/journal", headers=auth(user))
            assert status == 200

    assert body["current_day"] == "2026-09-29" and body["group"] == "ОККИПд-307"
    assert body["day"]["state"] == "open" and [p["slot"] for p in body["day"]["pairs"]][:2] == ["08:30", "10:10"]


async def test_roster_then_marks_round_trip(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    async with running(config, FakeSender()) as client:
        _, roster = await call(client, "PUT", "/api/roster", headers=auth(),
                               json={"students": [{"name": "  Баранова   Алина "}, {"name": "Абрамов Илья"}]})
        students = roster["students"]
        assert [s["name"] for s in students] == ["Абрамов Илья", "Баранова Алина"]

        status, saved = await call(client, "POST", "/api/marks", headers=auth(), json={
            "date": "2026-09-29", "changes": [
                {"student_id": students[0]["id"], "slot": "08:30", "mark": "absent"},
                {"student_id": students[1]["id"], "slot": "10:10", "mark": "present"}]})
        _, shown = await call(client, "GET", "/api/journal", headers=auth())
        _, listed = await call(client, "GET", "/api/roster", headers=auth())

    assert status == 200 and saved == {"saved": 2}
    assert [(s["name"], s["marks"]) for s in shown["day"]["students"]] == [
        ("Абрамов Илья", {"08:30": "absent"}), ("Баранова Алина", {"10:10": "present"})]
    assert listed == roster


async def test_marks_on_a_locked_day_are_refused_with_a_reason(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    async with running(config, FakeSender()) as client:
        _, roster = await call(client, "PUT", "/api/roster", headers=auth(),
                               json={"students": [{"name": "Абрамов Илья"}]})
        status, body = await call(client, "POST", "/api/marks", headers=auth(), json={
            "date": "2026-09-30", "changes": [
                {"student_id": roster["students"][0]["id"], "slot": "08:30", "mark": "present"}]})

    assert status == 403 and body["message"].startswith("День ещё не начался")


async def test_a_duplicate_in_the_roster_is_told_to_the_headman(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    async with running(config, FakeSender()) as client:
        status, body = await call(client, "PUT", "/api/roster", headers=auth(), json={
            "students": [{"name": "Абрамов Илья"}, {"name": "абрамов илья"}]})

    assert status == 422 and "Дважды" in body["message"]


async def test_garbage_in_a_request_is_a_422_with_a_russian_hint_not_a_crash(
    config: AppConfig, at: Clock
) -> None:
    at(TUE, "10:00")
    async with running(config, FakeSender()) as client:
        short = await call(client, "PUT", "/api/roster", headers=auth(), json={"students": [{"name": "А"}]})
        not_json = await call(client, "POST", "/api/marks", headers=auth(), data="не json")
        bad_day = await call(client, "GET", "/api/journal?day=вчера", headers=auth())
        bad_mark = await call(client, "POST", "/api/marks", headers=auth(), json={
            "date": "2026-09-29", "changes": [{"student_id": 1, "slot": "08:30", "mark": "maybe"}]})

    assert short[0] == 422 and "короткое" in short[1]["message"]
    assert not_json[0] == 422 and bad_day[0] == 400 and bad_mark[0] == 422


async def test_the_report_is_sent_to_the_asking_headman_with_the_title_choice(
    config: AppConfig, at: Clock
) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    sender = FakeSender()
    async with running(config, sender) as client:
        await call(client, "PUT", "/api/roster", headers=auth(), json={"students": [{"name": "Абрамов Илья"}]})
        status, _ = await call(client, "POST", "/api/report", headers=auth(),
                               json={"date": "2026-09-29", "titles": False})

    assert status == 200
    [(user, day, titles)] = sender.sent
    assert user == HEADMAN and titles is False
    assert day.date == TUE and [s.name for s in day.students] == ["Абрамов Илья"]


async def test_there_is_nothing_to_send_for_an_empty_roster_or_a_closed_day(
    config: AppConfig, at: Clock
) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    sender = FakeSender()
    async with running(config, sender) as client:
        empty, _ = await call(client, "POST", "/api/report", headers=auth(), json={"date": "2026-09-29"})
        await call(client, "PUT", "/api/roster", headers=auth(), json={"students": [{"name": "Абрамов Илья"}]})
        closed, _ = await call(client, "POST", "/api/report", headers=auth(), json={"date": "2026-09-30"})

    assert empty == 422 and closed == 403 and sender.sent == []


async def test_a_telegram_refusal_reaches_the_headman_as_text(config: AppConfig, at: Clock) -> None:
    at(TUE, "10:00")
    save_demo(config, TUE)
    sender = FakeSender()
    sender.refuse = ReportError("Нажмите «Старт»", 409)
    async with running(config, sender) as client:
        await call(client, "PUT", "/api/roster", headers=auth(), json={"students": [{"name": "Абрамов Илья"}]})
        status, body = await call(client, "POST", "/api/report", headers=auth(), json={"date": "2026-09-29"})

    assert status == 409 and body["message"] == "Нажмите «Старт»"


async def test_the_page_and_its_fonts_are_served_without_a_login(config: AppConfig) -> None:
    async with running(config, FakeSender()) as client:
        async with client.get("/") as index:
            page, headers = await index.text(), index.headers
        statuses = [(await client.get(path)).status for path in ("/app.js", "/fonts/Onest-cyrillic.woff2", "/healthz")]

    assert statuses == [200, 200, 200]
    assert "Журнал посещаемости" in page
    assert "script-src 'self' https://telegram.org" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-cache"


async def test_the_design_mock_is_not_served_to_the_world(config: AppConfig) -> None:
    async with running(config, FakeSender()) as client:
        mock = (await client.get("/mock.js")).status
        sneaky = (await client.get("/..%2Fserver.py")).status

    assert mock == 404 and sneaky == 404

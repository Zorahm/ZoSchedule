"""Сервер мини-приложения «Журнал посещаемости»: статика и JSON API на aiohttp.

Живёт внутри процесса бота (одна база, один токен). Вход только по подписи Telegram
(auth.py) и только для старост и доверенных. Каждая ручка — тонкая обёртка над
``attendance.journal``: правила там, здесь разбор запроса и ответ.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol, TypeVar

from aiohttp import web
from pydantic import BaseModel, ValidationError

from app import moscow
from app.attendance import journal, store
from app.attendance.models import (
    JournalDay,
    MarksBody,
    ReportBody,
    RosterBody,
    RosterEntry,
    RosterOut,
)
from app.attendance.report import ReportError
from app.config import AppConfig
from app.models.db import connect
from app.web.auth import AuthError, TelegramUser, verify_init_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
FONTS_DIR = Path(__file__).resolve().parents[1] / "bot" / "assets" / "fonts"
_MAX_BODY = 256 * 1024

_CONFIG = web.AppKey("config", AppConfig)

# Скрипты — свои и telegram.org (его SDK Telegram требует грузить оттуда). Стили с
# инлайном: ширины полосок в интерфейсе задаются атрибутом style.
_CSP = (
    "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'"
)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class ReportSender(Protocol):
    """Что серверу нужно от бота: нарисовать день и прислать файлом пользователю."""

    async def send_attendance_report(self, user_id: int, day: JournalDay, *, titles: bool) -> None: ...


_SENDER = web.AppKey("sender", ReportSender)


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status)


def _first_problem(error: ValidationError) -> str:
    """Русский текст первой ошибки проверки: своё сообщение валидатора, без служебных слов."""
    first = error.errors()[0]
    message = str(first["msg"]).removeprefix("Value error, ")
    return message if first["type"] == "value_error" else "Запрос непонятен: обновите журнал и повторите."


@web.middleware
async def _errors(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except AuthError as error:
        return _error(error.status, error.code, error.message)
    except journal.JournalError as error:
        return _error(error.status, error.code, error.message)
    except store.RosterError as error:
        return _error(422, "roster", str(error))
    except ReportError as error:
        return _error(error.status, "report", error.message)
    except ValidationError as error:
        return _error(422, "invalid", _first_problem(error))
    except ValueError as error:
        return _error(400, "bad_request", str(error))
    except Exception:
        # Запрос не должен ронять соединение без ответа: старосте нужен текст, а
        # подробности (с трассировкой) остаются в журнале.
        logger.exception("Сбой в журнале посещаемости: %s %s", request.method, request.path)
        return _error(500, "server", "Сбой на сервере. Попробуйте ещё раз.")


def _login(request: web.Request) -> TelegramUser:
    """Кто просит: по подписи запуска. Не староста и не доверенный — отказ."""
    config = request.app[_CONFIG]
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        raise AuthError("unauthorized", "Откройте журнал из чата с ботом.")
    user = verify_init_data(
        header.removeprefix("tma "), config.bot.token.get_secret_value(), now=moscow.now()
    )
    if user.id not in set(config.bot.headmen) | set(config.bot.trusted_users):
        logger.warning("Журнал: отказ пользователю %s, его нет в headmen/trusted_users", user.id)
        raise AuthError("forbidden", "Журнал доступен только старосте.", 403)
    return user


def _for_headmen(handler: Callable[[web.Request, TelegramUser], Awaitable[web.Response]]) -> Handler:
    """Ручка API видит только вошедшего: забыть проверку в новой ручке нельзя."""

    async def wrapper(request: web.Request) -> web.StreamResponse:
        return await handler(request, _login(request))

    return wrapper


@web.middleware
async def _headers(request: web.Request, handler: Handler) -> web.StreamResponse:
    response = await handler(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


_Model = TypeVar("_Model", bound=BaseModel)


async def _json(request: web.Request, model: type[_Model]) -> _Model:
    try:
        raw = await request.read()
    except web.HTTPRequestEntityTooLarge:
        raise ReportError("Слишком большой запрос.", 413) from None
    return model.model_validate_json(raw)


def _day_arg(request: web.Request, name: str) -> dt.date | None:
    raw = request.query.get(name)
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise ValueError("Дата ожидается в виде ГГГГ-ММ-ДД.") from None


async def _journal(request: web.Request, _: TelegramUser) -> web.Response:
    config = request.app[_CONFIG]
    with connect(config.db_path) as conn:
        result = journal.build(
            conn,
            group=config.group.name,
            day=_day_arg(request, "day"),
            week_of=_day_arg(request, "week"),
            now=moscow.now(),
        )
    return web.json_response(result.model_dump(mode="json"))


async def _marks(request: web.Request, user: TelegramUser) -> web.Response:
    config = request.app[_CONFIG]
    body = await _json(request, MarksBody)
    with connect(config.db_path) as conn:
        saved = journal.save_marks(
            conn, day=body.date, changes=body.changes, by=user.id, now=moscow.now()
        )
    return web.json_response({"saved": saved})


def _roster_out(students: list[store.Student]) -> web.Response:
    out = RosterOut(students=[RosterEntry(id=s.id, name=s.name) for s in students])
    return web.json_response(out.model_dump(mode="json"))


async def _roster_get(request: web.Request, _: TelegramUser) -> web.Response:
    with connect(request.app[_CONFIG].db_path) as conn:
        return _roster_out(store.active_students(conn))


async def _roster_put(request: web.Request, _: TelegramUser) -> web.Response:
    body = await _json(request, RosterBody)
    with connect(request.app[_CONFIG].db_path) as conn:
        return _roster_out(store.save_roster(conn, body.students, now=moscow.now()))


async def _report(request: web.Request, user: TelegramUser) -> web.Response:
    config = request.app[_CONFIG]
    sender = request.app[_SENDER]
    body = await _json(request, ReportBody)
    now = moscow.now()
    with connect(config.db_path) as conn:
        info = journal.open_day(conn, body.date, now)
        day = journal.day_view(conn, info, store.active_students(conn), now)
    if not day.students:
        raise ReportError("Список группы пуст: сначала добавьте студентов.", 422)
    await sender.send_attendance_report(user.id, day, titles=body.titles)
    return web.json_response({"sent": True})


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def _static_routes(app: web.Application) -> None:
    pages = {
        path.name: path
        for path in STATIC_DIR.iterdir()
        if path.suffix in (".html", ".css", ".js")
    }

    async def serve(request: web.Request) -> web.StreamResponse:
        name = request.match_info.get("name") or "index.html"
        path = pages.get(name)
        if path is None:
            raise web.HTTPNotFound
        # Интерфейс обновляется вместе с ботом: без проверки свежести староста застрял бы на старом.
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    app.router.add_get("/", serve)
    app.router.add_get("/{name}", serve)
    app.router.add_static("/fonts", FONTS_DIR, follow_symlinks=False)


def build_app(config: AppConfig, sender: ReportSender) -> web.Application:
    app = web.Application(
        middlewares=[_headers, _errors], client_max_size=_MAX_BODY
    )
    app[_CONFIG] = config
    app[_SENDER] = sender
    app.router.add_get("/healthz", _health)
    app.router.add_get("/api/journal", _for_headmen(_journal))
    app.router.add_post("/api/marks", _for_headmen(_marks))
    app.router.add_get("/api/roster", _for_headmen(_roster_get))
    app.router.add_put("/api/roster", _for_headmen(_roster_put))
    app.router.add_post("/api/report", _for_headmen(_report))
    _static_routes(app)
    return app


async def serve(config: AppConfig, sender: ReportSender) -> None:
    """Слушает порт, пока задачу не отменят. Сбой запуска не роняет бота: он пишет в журнал."""
    if not config.bot.configured:
        logger.warning("Журнал посещаемости не запущен: нет токена бота")
        return
    runner = web.AppRunner(build_app(config, sender), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, config.web.host, config.web.port).start()
    except OSError as error:
        logger.error(
            "Журнал посещаемости не запущен: не удалось занять %s:%d (%s)",
            config.web.host,
            config.web.port,
            error,
        )
        await runner.cleanup()
        return
    logger.info("Журнал посещаемости слушает %s:%d, адрес для Telegram: %s",
                config.web.host, config.web.port, config.web.public_url)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

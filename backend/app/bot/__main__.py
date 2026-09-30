"""Manual control of the bot: `python -m app.bot <command>`.

Meant for trying it on a scratch database and a test chat:

    set ZOSCHEDULE_DB=D:/scratch/test.db
    set ZOSCHEDULE_BOT_TOKEN=...   set ZOSCHEDULE_BOT_CHAT_ID=...
    python -m app.bot seed-demo
    python -m app.bot preview
    python -m app.bot post-week
    python -m app.bot post-today
    python -m app.bot demo-change   (then `announce` or `run`)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app import moscow
from app.bot import demo, simulate
from app.bot.renderer import PlaywrightRenderer
from app.bot.runner import open_service, run_forever
from app.bot.pictures import PictureBuilder, target_monday
from app.config import AppConfig, get_config
from app.models.db import connect
from app.snapshots import store
from app.snapshots.service import ScheduleService

_REAL_DB = Path(__file__).resolve().parents[2] / "zoschedule.db"


def _require_token(config: AppConfig) -> None:
    if not config.bot.configured:
        sys.exit("Нужен ZOSCHEDULE_BOT_TOKEN.")


def _refuse_real_db(config: AppConfig) -> None:
    if config.db_path.resolve() == _REAL_DB.resolve():
        sys.exit("Демо-данные не пишутся в настоящую базу. Задайте ZOSCHEDULE_DB на пустую.")


def _without_refresh(config: AppConfig) -> AppConfig:
    return config.model_copy(update={"bot": config.bot.model_copy(update={"refresh": False})})


async def _preview(config: AppConfig, out: Path) -> None:
    builder = PictureBuilder(config, PlaywrightRenderer(config.bot.browser_path))
    out.mkdir(parents=True, exist_ok=True)
    today = moscow.today()
    week = await builder.week(target_monday(today))
    day = await builder.today(today, force=True)
    for name, picture in (("week", week), ("today", day)):
        if picture is None:
            print(f"{name}: нет данных (в базе нет ни одного снимка)")
            continue
        path = out / f"{name}.png"
        path.write_bytes(picture.png)
        print(f"{name}: {path}  |  {picture.caption}")


def _seed(config: AppConfig, *, changed: bool) -> None:
    _refuse_real_db(config)
    service = ScheduleService(config)
    service.prepare()
    today = moscow.today()
    with connect(config.db_path) as conn:
        latest = store.latest_ok(conn)
        current = store.load_lessons(conn, latest.id) if latest else []
    if changed:
        if not current:
            sys.exit("Сначала seed-demo: изменять нечего.")
        count = service.store_lessons(demo.apply_changes(current, today))
        print(f"Сохранён снимок с изменениями, событий: {count}")
    else:
        if current:
            sys.exit("В базе уже есть снимки. Для демо нужна пустая база.")
        service.store_lessons(demo.build_schedule(today))
        print("Демо-расписание сохранено (эта неделя и следующая).")


async def _run(args: argparse.Namespace) -> None:
    config = get_config()
    command: str = args.command

    if command == "preview":
        await _preview(config, args.out)
    elif command in ("seed-demo", "demo-change"):
        _seed(config, changed=command == "demo-change")
    else:
        ScheduleService(config).prepare()
        if command == "run":
            _require_token(config)
            await run_forever(_without_refresh(config) if args.no_refresh else config)
            return
        if command == "simulate":
            _refuse_real_db(config)
            if not (config.bot.configured and config.bot.chat_id):
                sys.exit("Для simulate нужны ZOSCHEDULE_BOT_TOKEN и ZOSCHEDULE_BOT_CHAT_ID.")
            await simulate.run(
                config, pause=args.pause, real_source=_REAL_DB if args.real_data else None
            )
            return
        _require_token(config)
        if args.no_refresh:
            # A demo database must not be overwritten by whatever the real site says.
            config = _without_refresh(config)
        async with open_service(config) as bot:
            if not bot.bind_target():
                sys.exit("Нет чата: напишите /go в группе или задайте ZOSCHEDULE_BOT_CHAT_ID.")
            await bot.refresh(force=True)  # a manual command always works on fresh data
            if command == "post-week":
                print("Отправлено" if await bot.post_week(force=args.force) else "Пропущено")
            elif command == "post-today":
                print("Отправлено" if await bot.post_today(force=args.force) else "Пропущено")
            elif command == "announce":
                print(f"Объявлено изменений: {await bot.announce_changes()}")
            elif command == "sync":
                print(f"Картинок обновлено: {await bot.sync_pictures()}")


def main() -> None:
    # Windows consoles default to cp1251, which cannot print the emoji in captions.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="python -m app.bot")
    parser.add_argument(
        "--no-refresh", action="store_true", help="не опрашивать сайт (для демо-базы)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview", help="нарисовать картинки в папку, без Telegram")
    preview.add_argument("--out", type=Path, default=Path("bot-preview"))
    for name in ("post-week", "post-today"):
        post = sub.add_parser(name, help="отправить сейчас")
        post.add_argument("--force", action="store_true", help="даже если уже отправлено")
    sub.add_parser("announce", help="объявить новые изменения текстом")
    sub.add_parser("sync", help="перерисовать устаревшие картинки")
    sub.add_parser("run", help="рабочий цикл бота")
    simulation = sub.add_parser("simulate", help="неделя жизни бота с подставными часами (тестовая база)")
    simulation.add_argument("--pause", type=float, default=6.0, help="секунд между шагами")
    simulation.add_argument(
        "--real-data",
        action="store_true",
        help="взять расписание из КОПИИ настоящей базы, а не демо (оригинал только читается)",
    )
    sub.add_parser("seed-demo", help="заполнить пустую тестовую базу демо-расписанием")
    sub.add_parser("demo-change", help="сохранить снимок с изменениями (перенос, отмена, ...)")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()

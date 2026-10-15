"""Версия статики в адресах: Telegram на телефоне держит css и js в своём кэше и игнорирует
``no-cache``, из-за чего после обновления бота страница собиралась из старых и новых файлов."""

from __future__ import annotations

import re
from pathlib import Path

_LOCAL = re.compile(r'(href|src)="([A-Za-z0-9_.-]+\.(?:css|js))"')


def version(static_dir: Path) -> str:
    """Меняется, как только изменился любой файл интерфейса."""
    stamps = [path.stat().st_mtime_ns for path in static_dir.iterdir() if path.suffix in (".html", ".css", ".js")]
    return format(max(stamps, default=0), "x")


def index_html(static_dir: Path) -> str:
    """Главная страница со своими стилями и скриптами по адресам вида ``app.js?v=…``."""
    page = (static_dir / "index.html").read_text(encoding="utf-8")
    stamp = version(static_dir)
    return _LOCAL.sub(lambda m: f'{m.group(1)}="{m.group(2)}?v={stamp}"', page)

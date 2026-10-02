"""Предпросмотр дизайна журнала без сервера и Telegram: ``python -m app.web.preview ПАПКА``.

Собирает в папку статику, шрифты и подставной сервер (preview/mock.js), открывается как
обычный файл. Дизайн правится здесь, не трогая базу и бота. ``page.html`` — то же самое
одним фрагментом, для публикации артефактом.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.web.server import FONTS_DIR, STATIC_DIR

MOCK = Path(__file__).parent / "preview" / "mock.js"
_SDK = '<script src="https://telegram.org/js/telegram-web-app.js"></script>\n'
_APP = '<script src="app.js"></script>'


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC_DIR, out)
    shutil.copytree(FONTS_DIR, out / "fonts")
    shutil.copy(MOCK, out / "mock.js")

    page = (out / "index.html").read_text(encoding="utf-8")
    page = page.replace(_SDK, "").replace(_APP, f'<script src="mock.js"></script>\n{_APP}')
    (out / "index.html").write_text(page, encoding="utf-8")

    # Публикатор сам оборачивает фрагмент в html/head/body, поэтому без этих тегов.
    start, end = page.index("<title>"), page.index("</head>")
    body = page[page.index("<body>") + len("<body>") : page.index("</body>")]
    (out / "page.html").write_text(page[start:end] + body.strip() + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Использование: python -m app.web.preview ПАПКА")
    out = Path(sys.argv[1])
    build(out)
    print(f"Готово: откройте {out / 'index.html'}")


if __name__ == "__main__":
    main()

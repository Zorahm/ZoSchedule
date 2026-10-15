"""Рисует cover.png (640×360) из cover.html: ``python -m app.web.design.render_cover``.

Путь к браузеру можно дать аргументом. Картинку просит @BotFather при регистрации мини-приложения (/newapp). Нужен Chrome/Chromium.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from app.render.renderer import find_browser

HERE = Path(__file__).parent


async def render(browser_path: Path | None = None) -> Path:
    target = HERE / "cover.png"
    executable = find_browser(browser_path)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable) if executable else None)
        page = await browser.new_page(viewport={"width": 640, "height": 360})
        await page.goto((HERE / "cover.html").as_uri())
        await page.evaluate("document.fonts.ready")
        await page.screenshot(path=str(target))
        await browser.close()
    return target


if __name__ == "__main__":
    print(f"Готово: {asyncio.run(render(Path(sys.argv[1]) if len(sys.argv) > 1 else None))}")

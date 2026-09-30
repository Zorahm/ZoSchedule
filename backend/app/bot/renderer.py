"""HTML -> PNG through Playwright.

The bundled Chromium is a ~150 MB download that some networks block, so an
installed Chrome/Edge is preferred. Playwright's own channel lookup misses
browsers under `Program Files` when environment variables are trimmed, hence the
explicit path search.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

from app.bot.templates import FRAME_SELECTOR, WIDTH

logger = logging.getLogger(__name__)


class Renderer(Protocol):
    async def render(self, html: str) -> bytes: ...


class RenderError(RuntimeError):
    pass


def _candidate_paths() -> list[Path]:
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
        "C:/Program Files",
        "C:/Program Files (x86)",
    ]
    suffixes = (
        "Google/Chrome/Application/chrome.exe",
        "Microsoft/Edge/Application/msedge.exe",
    )
    return [Path(root) / suffix for root in roots if root for suffix in suffixes]


def find_browser(configured: Path | None = None) -> Path | None:
    """Explicit path wins; otherwise the first installed Chrome or Edge."""
    if configured is not None:
        if not configured.is_file():
            raise RenderError(f"browser_path не найден: {configured}")
        return configured
    for path in _candidate_paths():
        if path.is_file():
            return path
    return None


class PlaywrightRenderer:
    def __init__(self, browser_path: Path | None = None) -> None:
        self._browser_path = browser_path

    async def render(self, html: str) -> bytes:
        # Imported here so the API starts without Playwright while the bot is off.
        from playwright.async_api import async_playwright

        executable = find_browser(self._browser_path)
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(
                    executable_path=str(executable) if executable else None
                )
            except Exception as error:
                # Playwright raises a bare Error with install hints; wrap it so callers
                # can tell "no browser" apart from a broken page.
                raise RenderError(
                    "Не удалось запустить браузер для картинок. Установите Chrome/Edge, "
                    "задайте bot.browser_path или выполните `playwright install chromium`."
                ) from error
            try:
                page = await browser.new_page(viewport={"width": WIDTH, "height": 800})
                await page.set_content(html, wait_until="load")
                await page.evaluate("document.fonts.ready")
                frame = page.locator(FRAME_SELECTOR)
                return await frame.screenshot(type="png")
            finally:
                await browser.close()

"""Экран расписания и навигация в настоящем браузере, на подставных данных (preview/mock.js).

Часы мока стоят на пятнице 2 октября 2026, 11:20; расписание опубликовано до 10 октября.
Нужен Chrome/Chromium: путь можно задать ZOSCHEDULE_TEST_BROWSER, иначе тест пропускается.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright

from app.render.renderer import find_browser
from app.web import preview

_JUNK = ("null", "undefined", "NaN", "[object")
Open = Callable[[str], Coroutine[Any, Any, Page]]


@pytest.fixture
async def open_app(tmp_path: Path) -> AsyncGenerator[Open]:
    configured = os.environ.get("ZOSCHEDULE_TEST_BROWSER")
    executable = Path(configured) if configured else find_browser()
    out = tmp_path / "preview"
    preview.build(out)
    async with async_playwright() as playwright:
        try:
            browser: Browser = await playwright.chromium.launch(
                executable_path=str(executable) if executable else None
            )
        except Exception as error:  # noqa: BLE001
            # Не баг проекта: на машине нет подходящего браузера. Тест пропускаем, а не глушим.
            pytest.skip(f"нет браузера для проверки интерфейса: {str(error).splitlines()[0]}")
        context = await browser.new_context(viewport={"width": 390, "height": 780})

        async def opener(query: str) -> Page:
            tab = await context.new_page()
            await tab.goto((out / "index.html").as_uri() + query)
            await tab.wait_for_selector(".wk")
            return tab

        yield opener
        await browser.close()


async def _clean(page: Page, where: str) -> None:
    text = await page.evaluate("document.body.innerText")
    found = [word for word in _JUNK if word in text]
    assert not found, f"на экране {found!r} ({where}):\n{text[:400]}"


async def test_the_schedule_is_the_first_screen_and_marks_changes(open_app: Open) -> None:
    page = await open_app("")

    assert await page.locator("h1").inner_text() == "Пятница"
    assert await page.locator(".les").count() >= 4
    assert "Идёт сейчас" in await page.locator(".les.now").inner_text()
    assert "Перенесена с 12:10" in await page.locator(".les .tag.warn").first.inner_text()
    assert "Отменена" in await page.locator(".les.cancelled").inner_text()
    assert "преподаватель не указан на сайте" in await page.locator(".lessons").inner_text()
    await _clean(page, "день")

    await page.get_by_label("Следующая неделя").click()
    await page.get_by_label("Понедельник, 5 октября").click()
    assert "Другой преподаватель" in await page.locator(".tag.warn").first.inner_text()
    assert await page.locator(".void").count() == 0  # в день с парами «Выходной» не пишем


async def test_a_day_off_is_not_the_same_as_an_unpublished_one(open_app: Open) -> None:
    page = await open_app("")

    await page.get_by_label("Воскресенье, 4 октября").click()
    assert await page.locator(".void-t").inner_text() == "Выходной"

    await page.get_by_label("Следующая неделя").click()  # воскресенье 11 октября: за пределом публикации
    assert await page.locator(".void-t").inner_text() == "Ещё не опубликовано"
    await _clean(page, "не опубликовано")


async def test_the_calendar_reaches_a_year_ahead(open_app: Open) -> None:
    page = await open_app("")

    await page.get_by_label("Календарь на год").click()
    for _ in range(12):
        await page.get_by_label("Следующий месяц").click()
    assert "Октябрь" in await page.locator(".cal-title").inner_text()
    assert await page.get_by_label("Следующий месяц").is_disabled()

    await page.locator(".cd").nth(14).click()  # 15 октября 2027
    assert await page.locator("h1").inner_text() != ""
    assert await page.locator(".void-t").inner_text() == "Ещё не опубликовано"


async def test_the_headman_switches_to_the_journal_and_back(open_app: Open) -> None:
    page = await open_app("")

    await page.get_by_role("button", name="Журнал").click()
    await page.wait_for_selector(".cell")
    assert not await page.locator("#pane-schedule").is_visible()
    await page.get_by_role("button", name="Расписание").click()
    assert await page.locator(".wk").is_visible()
    assert not await page.locator("#pane-journal").is_visible()


async def test_a_student_sees_only_the_schedule(open_app: Open) -> None:
    page = await open_app("?guest")

    assert await page.locator(".les").count() >= 4
    assert await page.locator(".nav").count() == 0
    assert not await page.locator("#pane-journal").is_visible()

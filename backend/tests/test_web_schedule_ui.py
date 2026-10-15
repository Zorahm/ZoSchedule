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
Open = Callable[..., Coroutine[Any, Any, Page]]


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

        async def opener(query: str, script: str | None = None) -> Page:
            tab = await context.new_page()
            if script:
                await tab.add_init_script(script)
            await tab.goto((out / "index.html").as_uri() + query)
            await tab.wait_for_selector(".wk")
            return tab

        yield opener
        await browser.close()


async def _clean(page: Page, where: str) -> None:
    text = await page.evaluate("document.body.innerText")
    found = [word for word in _JUNK if word in text]
    assert not found, f"на экране {found!r} ({where}):\n{text[:400]}"


async def test_the_schedule_is_the_first_screen_and_shows_only_current_lessons(open_app: Open) -> None:
    page = await open_app("")

    assert await page.locator("h1").inner_text() == "Пятница"
    assert await page.locator(".les").count() == 4
    assert "Идёт сейчас" in await page.locator(".les.now").inner_text()
    assert "преподаватель не указан на сайте" in await page.locator(".lessons").inner_text()
    assert await page.locator(".void").count() == 0  # в день с парами «Выходной» не пишем
    text = await page.locator(".sch-list").inner_text()
    assert "Опубликовано на сайте" not in text  # эта подпись живёт только в календаре
    assert not [word for word in ("Отменена", "Перенесена", "Добавлена", "Другой преподаватель") if word in text]
    await _clean(page, "день")


async def test_a_day_off_is_not_the_same_as_an_unpublished_one(open_app: Open) -> None:
    page = await open_app("")

    await page.get_by_label("Четверг, 1 октября").click()  # опубликован, пар нет
    assert await page.locator(".void-t").inner_text() == "Выходной"

    await page.get_by_label("Следующая неделя").click()
    await page.get_by_label("Следующая неделя").click()  # четверг 15 октября: за пределом публикации
    assert await page.locator(".void-t").inner_text() == "Ещё не опубликовано"
    await _clean(page, "не опубликовано")


async def test_sunday_is_not_offered_and_the_building_is_named(open_app: Open) -> None:
    page = await open_app("")

    assert await page.locator(".wd").count() == 6
    assert await page.get_by_label("Воскресенье, 4 октября").count() == 0
    await page.get_by_label("Календарь на год").click()
    assert await page.locator(".cal-grid b").count() == 6
    assert await page.locator(".cd").count() == 27  # октябрь 2026: 31 день минус 4 воскресенья
    await page.locator(".scrim").click(position={"x": 10, "y": 10})

    await page.get_by_label("Суббота, 3 октября").click()
    await page.get_by_label("Следующая неделя").click()
    await page.get_by_label("Суббота, 10 октября").click()
    assert await page.locator("h1").inner_text() == "Суббота"
    assert "корп. Сокол" in await page.locator(".les-top").first.inner_text()


async def test_the_calendar_reaches_a_year_ahead(open_app: Open) -> None:
    page = await open_app("")

    await page.get_by_label("Календарь на год").click()
    for _ in range(12):
        await page.get_by_label("Следующий месяц").click()
    assert "Октябрь" in await page.locator(".cal-title").inner_text()
    assert await page.get_by_label("Следующий месяц").is_disabled()

    await page.locator(".cd").nth(10).click()  # день через год
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


async def test_the_kind_of_a_lesson_is_colored_and_sits_at_the_right_edge(open_app: Open) -> None:
    page = await open_app("")

    assert await page.locator(".kind.k-lecture").count() >= 1
    assert await page.locator(".kind.k-practice").count() >= 1
    card = await page.locator(".les").first.bounding_box()
    chip = await page.locator(".les .kind").first.bounding_box()
    assert card is not None and chip is not None
    assert card["x"] + card["width"] - (chip["x"] + chip["width"]) < 20  # у правого края карточки


async def test_the_arrows_land_on_a_monday(open_app: Open) -> None:
    page = await open_app("")  # пятница 2 октября

    await page.get_by_label("Следующая неделя").click()
    assert await page.locator("h1").inner_text() == "Понедельник"
    assert await page.locator(".wd.sel").get_attribute("aria-label") == "Понедельник, 5 октября"

    await page.get_by_label("Прошлая неделя").click()
    await page.get_by_label("Прошлая неделя").click()
    assert await page.locator(".wd.sel").get_attribute("aria-label") == "Понедельник, 21 сентября"


_HOME_STUB = """
window.Telegram = { WebApp: {
  initData: '', colorScheme: 'light', BackButton: { onClick() {}, show() {}, hide() {} },
  ready() {}, expand() {}, onEvent() {}, setHeaderColor() {}, setBackgroundColor() {},
  checkHomeScreenStatus(cb) { cb('missed'); },
  addToHomeScreen() { window.__added = true; },
} };
"""


async def test_the_home_screen_shortcut_is_offered_only_where_telegram_can_add_it(open_app: Open) -> None:
    page = await open_app("", _HOME_STUB)
    await page.get_by_label("Добавить на экран телефона").click()
    assert await page.evaluate("window.__added") is True

    plain = await open_app("")
    assert await plain.get_by_label("Добавить на экран телефона").count() == 0

"""Интерфейс журнала в настоящем браузере: на экране не должно быть служебных слов.

Родной ``replaceChildren`` превращает ``null`` в текст «null», и такой баг уже дважды доезжал
до старосты: он виден только на живой странице и только в некоторых состояниях (например, когда
в столбце заполнены все ячейки). Тест проходит по состояниям на подставных данных (mock.js).

Нужен Chrome/Edge или Chromium. Путь можно задать переменной ZOSCHEDULE_TEST_BROWSER,
иначе тест пропускается.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from playwright.async_api import Page, async_playwright

from app.render.renderer import find_browser
from app.web import preview

_JUNK = ("null", "undefined", "NaN", "[object")


@pytest.fixture
async def page(tmp_path: Path) -> AsyncGenerator[Page]:
    configured = os.environ.get("ZOSCHEDULE_TEST_BROWSER")
    executable = Path(configured) if configured else find_browser()
    out = tmp_path / "preview"
    preview.build(out)
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                executable_path=str(executable) if executable else None
            )
        except Exception as error:  # noqa: BLE001
            # Не баг проекта: на машине нет подходящего браузера. Тест пропускаем, а не глушим.
            pytest.skip(f"нет браузера для проверки интерфейса: {str(error).splitlines()[0]}")
        context = await browser.new_context(viewport={"width": 390, "height": 780})
        tab = await context.new_page()
        await tab.goto((out / "index.html").as_uri())
        await tab.wait_for_selector(".cell")
        yield tab
        await browser.close()


async def _no_junk(page: Page, where: str) -> None:
    text = await page.evaluate("document.body.innerText")
    found = [word for word in _JUNK if word in text]
    assert not found, f"на экране {found!r} ({where}):\n{text[:400]}"


async def test_the_sums_row_is_clean_when_a_whole_column_is_filled(page: Page) -> None:
    await _no_junk(page, "первый экран")

    await page.get_by_text("Остальным П").click()  # все ячейки заполнены: «ещё N» не нужно
    await page.wait_for_selector(".btn.send")

    await _no_junk(page, "всё отмечено")
    sums = await page.locator("tfoot .sum").all_inner_texts()
    assert sums and all(cell.strip() and "null" not in cell for cell in sums)


async def test_every_sheet_and_screen_is_clean(page: Page) -> None:
    await page.locator('[data-pair="12:10"]').click()  # выбор пары: баннер и своя панель
    await _no_junk(page, "пара выбрана")
    await page.locator(".scope-text").click()  # карточка пары
    await _no_junk(page, "карточка пары")
    await page.locator(".scrim").click(position={"x": 10, "y": 10})
    await page.locator('[data-student="3"]').click()
    await _no_junk(page, "карточка студента")
    await page.locator(".scrim").click(position={"x": 10, "y": 10})

    await page.get_by_label("Список группы").click()
    await _no_junk(page, "список группы")
    await page.get_by_label("Назад").click()

    await page.get_by_label("Отправить куратору").click()
    await _no_junk(page, "подтверждение отправки")
    await page.get_by_text("Прислать картинку").click()
    await page.wait_for_selector(".done")
    await _no_junk(page, "картинка отправлена")


async def test_closed_and_other_days_are_clean(page: Page) -> None:
    for label in ("СБ", "ЧТ", "ВТ"):
        await page.locator(".chip", has_text=label).click()
        await page.wait_for_timeout(300)
        await _no_junk(page, f"день {label}")

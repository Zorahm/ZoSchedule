"""The week picture never cuts a name with "…": it fits, shrinks a little, or wraps."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import SecretStr

from app.bot import demo
from app.bot.pictures import PictureBuilder
from app.bot.renderer import find_browser, prepare_page
from app.bot.templates import WIDTH
from app.config import AppConfig, BotConfig
from app.snapshots.service import ScheduleService
from tests.fakes import Clock, FakeRenderer

SUNDAY = dt.date(2026, 9, 27)
MONDAY = dt.date(2026, 9, 28)
LONG = "Комплексная автоматизация технологических процессов производства и контроля качества"


@pytest.fixture
def bot_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), chat_id="-100500")
    return config.model_copy(update={"bot": bot})


@pytest.mark.skipif(find_browser() is None, reason="нет установленного Chrome/Edge")
async def test_in_a_real_browser_no_name_on_the_week_is_cut(
    bot_config: AppConfig, at: Clock
) -> None:
    from playwright.async_api import async_playwright

    at(SUNDAY)
    lessons = demo.build_schedule(SUNDAY)
    first = next(i for i, item in enumerate(lessons) if item.date == MONDAY)  # the pictured week
    lessons[first] = lessons[first].model_copy(update={"discipline": LONG})
    service = ScheduleService(bot_config)
    service.prepare()
    service.store_lessons(lessons)
    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)
    assert picture is not None
    html = picture.png.decode("utf-8")  # the fake renderer hands the page back

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(find_browser()))
        try:
            page = await browser.new_page(viewport={"width": WIDTH, "height": 800})
            await prepare_page(page, html)
            names: list[tuple[str, bool, str]] = await page.evaluate(
                """Array.from(document.querySelectorAll('.ln .n')).map(
                    e => [e.textContent, e.scrollWidth <= e.clientWidth + 1, e.style.cssText])"""
            )
        finally:
            await browser.close()

    assert names, "the week has lessons"
    assert all(fits for _, fits, _ in names)  # nothing runs past its cell
    by_name = {text: style for text, _, style in names}
    assert by_name["Математический анализ"] == ""  # what already fitted is left alone
    assert by_name[LONG] != ""  # the long one was shrunk or wrapped


@pytest.mark.skipif(find_browser() is None, reason="нет установленного Chrome/Edge")
async def test_in_a_real_browser_a_day_off_keeps_its_date_on_one_line_and_its_divider(
    bot_config: AppConfig, at: Clock
) -> None:
    from playwright.async_api import async_playwright

    at(SUNDAY)
    tuesday = MONDAY + dt.timedelta(days=1)  # "29 сентября": the longest date of the week
    lessons = [item for item in demo.build_schedule(SUNDAY) if item.date != tuesday]
    service = ScheduleService(bot_config)
    service.prepare()
    service.store_lessons(lessons)
    picture = await PictureBuilder(bot_config, FakeRenderer()).week(MONDAY)
    assert picture is not None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(find_browser()))
        try:
            page = await browser.new_page(viewport={"width": WIDTH, "height": 800})
            await prepare_page(page, picture.png.decode("utf-8"))
            box: dict[str, float] = await page.evaluate(
                """(() => {
                    const who = document.querySelector('.row.off .who');
                    const date = who.querySelector('.dat');
                    const block = (who.querySelector('.wd') || who).getBoundingClientRect();
                    const column = who.getBoundingClientRect();
                    return {
                        lines: date.getBoundingClientRect().height / parseFloat(getComputedStyle(date).lineHeight),
                        divider: parseFloat(getComputedStyle(who).borderRightWidth),
                        overlap: block.right - (column.right - parseFloat(getComputedStyle(who).paddingRight)),
                    };
                })()"""
            )
        finally:
            await browser.close()

    assert round(box["lines"]) == 1  # "29 сентября" is not broken in two
    assert box["divider"] == 1  # the line between the day and its lessons stays
    assert box["overlap"] <= 0.5  # the green block stays inside its column

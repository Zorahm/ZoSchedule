"""Предпросмотр дизайна собирается и не тянет в боевую страницу подставной сервер."""

from __future__ import annotations

from pathlib import Path

from app.web import preview, server


def test_the_preview_has_the_mock_but_the_real_page_does_not() -> None:
    real = (server.STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "mock.js" not in real and not (server.STATIC_DIR / "mock.js").exists()


def test_the_preview_is_a_page_you_can_open_from_disk(tmp_path: Path) -> None:
    preview.build(tmp_path / "out")

    index = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    fragment = (tmp_path / "out" / "page.html").read_text(encoding="utf-8")
    assert index.index("mock.js") < index.index("app.js")
    assert "telegram.org" not in index
    assert (tmp_path / "out" / "fonts" / "Onest-cyrillic.woff2").is_file()
    assert fragment.startswith("<title>") and "<html" not in fragment and "mock.js" in fragment

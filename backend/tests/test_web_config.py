"""Настройки журнала: староста, адрес мини-приложения, порт."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppConfig, BotConfig, WebConfig, load_config


def _load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, edits: dict[str, str] | None = None, **env: str
) -> AppConfig:
    base = Path(__file__).resolve().parents[2] / "config.toml"
    config_file = tmp_path / "config.toml"
    text = base.read_text(encoding="utf-8")
    for old, new in (edits or {}).items():
        assert old in text, old
        text = text.replace(old, new)
    config_file.write_text(text, encoding="utf-8")
    monkeypatch.setenv("ZOSCHEDULE_CONFIG", str(config_file))
    monkeypatch.setenv("ZOSCHEDULE_ENV_FILE", "")
    for name in ("ZOSCHEDULE_BOT_HEADMEN", "ZOSCHEDULE_WEB_URL", "ZOSCHEDULE_WEB_HOST", "ZOSCHEDULE_WEB_PORT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_config()


def test_the_journal_is_off_until_an_address_is_given(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(monkeypatch, tmp_path)

    assert not config.web.enabled and config.bot.headmen == []


def test_the_address_and_headmen_come_from_the_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(monkeypatch, tmp_path, ZOSCHEDULE_WEB_URL="https://j.example.org/", ZOSCHEDULE_WEB_PORT="9090",
                   ZOSCHEDULE_BOT_HEADMEN=" 22, 11 ;22")

    assert config.web.enabled and config.web.public_url == "https://j.example.org"
    assert config.web.port == 9090 and config.bot.headmen == [11, 22]


def test_the_toml_section_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(monkeypatch, tmp_path, {'public_url = ""': 'public_url = "https://j.example.org"',
                                           'host = "127.0.0.1"': 'host = "0.0.0.0"', "headmen = []": "headmen = [5]"})

    assert config.web.host == "0.0.0.0" and config.web.enabled and config.bot.headmen == [5]


@pytest.mark.parametrize("url", ["http://j.example.org", "j.example.org", "https://", "ftp://x.y"])
def test_telegram_only_opens_https_so_nothing_else_is_accepted(url: str) -> None:
    with pytest.raises(ValidationError, match="https"):
        WebConfig(public_url=url)


def test_a_group_or_a_junk_value_is_not_a_headman() -> None:
    with pytest.raises(ValidationError, match="headmen"):
        BotConfig(headmen=[-100123])
    with pytest.raises(ValidationError, match="headmen"):
        BotConfig.model_validate({"headmen": "abc"})

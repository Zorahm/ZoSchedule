"""The list of people who may run the bot (`trusted_users`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.bot.service import BotService
from app.config import AppConfig, BotConfig, load_config
from app.snapshots.service import ScheduleService
from tests.fakes import FakeRenderer, FakeTelegram


def _load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, toml: str | None, env: str | None
) -> BotConfig:
    base = Path(__file__).resolve().parents[2] / "config.toml"
    text = base.read_text(encoding="utf-8")
    if toml is not None:
        text = text.replace("trusted_users = []", f"trusted_users = {toml}")
    config_file = tmp_path / "config.toml"
    config_file.write_text(text, encoding="utf-8")
    monkeypatch.setenv("ZOSCHEDULE_CONFIG", str(config_file))
    monkeypatch.setenv("ZOSCHEDULE_ENV_FILE", "")
    monkeypatch.setenv("ZOSCHEDULE_BOT_TOKEN", "4242:TEST")
    if env is None:
        monkeypatch.delenv("ZOSCHEDULE_BOT_TRUSTED_USERS", raising=False)
    else:
        monkeypatch.setenv("ZOSCHEDULE_BOT_TRUSTED_USERS", env)
    return load_config().bot


def test_nobody_is_trusted_until_someone_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _load(monkeypatch, tmp_path, toml=None, env=None).trusted_users == []


def test_the_list_comes_from_config_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bot = _load(monkeypatch, tmp_path, toml="[111, 222]", env=None)

    assert bot.trusted_users == [111, 222]


def test_the_env_value_is_comma_separated_and_wins_over_the_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bot = _load(monkeypatch, tmp_path, toml="[111]", env=" 333, 222 ;444,,")

    assert bot.trusted_users == [222, 333, 444]  # sorted, no duplicates, blanks skipped


@pytest.mark.parametrize("env", ["abc", "123,x", "-100123", "0"])
def test_a_bad_value_is_refused_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        _load(monkeypatch, tmp_path, toml=None, env=env)

    assert "ожидается список положительных Telegram-id" in str(raised.value)


def test_a_group_id_in_the_list_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _load(monkeypatch, tmp_path, toml="[123, -1001234567890]", env=None)  # a group, not a person


def _load_chats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, env: str | None) -> BotConfig:
    base = Path(__file__).resolve().parents[2] / "config.toml"
    config_file = tmp_path / "config.toml"
    config_file.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("ZOSCHEDULE_CONFIG", str(config_file))
    monkeypatch.setenv("ZOSCHEDULE_ENV_FILE", "")
    monkeypatch.setenv("ZOSCHEDULE_BOT_TOKEN", "4242:TEST")
    monkeypatch.delenv("ZOSCHEDULE_BOT_TRUSTED_USERS", raising=False)
    if env is None:
        monkeypatch.delenv("ZOSCHEDULE_BOT_TRUSTED_CHATS", raising=False)
    else:
        monkeypatch.setenv("ZOSCHEDULE_BOT_TRUSTED_CHATS", env)
    return load_config().bot


def test_no_group_is_whitelisted_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _load_chats(monkeypatch, tmp_path, env=None).trusted_chats == []


def test_several_whitelisted_groups_come_from_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bot = _load_chats(monkeypatch, tmp_path, env=" -1002, -1001 ;-1002,,")

    assert bot.trusted_chats == [-1002, -1001]  # sorted, no duplicates, blanks skipped


@pytest.mark.parametrize("env", ["abc", "-100,x", "123456", "0"])
def test_a_bad_group_id_is_refused_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        _load_chats(monkeypatch, tmp_path, env=env)

    assert "ожидается список отрицательных Telegram-id групп" in str(raised.value)


def test_the_service_exposes_the_list_as_a_set(config: AppConfig, telegram: FakeTelegram) -> None:
    trusted = config.model_copy(
        update={"bot": config.bot.model_copy(update={"trusted_users": [5, 6]})}
    )
    ScheduleService(trusted).prepare()

    service = BotService(trusted, telegram.bot, FakeRenderer())

    assert service.trusted_users == frozenset({5, 6})

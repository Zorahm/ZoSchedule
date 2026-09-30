"""`.env` loading: secrets for production without a new dependency."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_config, load_dotenv


def test_reads_plain_quoted_and_commented_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("T_PLAIN", "T_QUOTED", "T_SINGLE", "T_EXPORT", "T_INLINE", "T_EMPTY", "T_MISSING"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "T_PLAIN=abc:123\n"
        'T_QUOTED="with spaces # not a comment"\n'
        "T_SINGLE='single'\n"
        "export T_EXPORT=exported\n"
        "T_INLINE=value # trailing comment\n"
        "T_EMPTY=\n"
        "no equals sign here\n",
        encoding="utf-8",
    )

    load_dotenv(env)

    import os

    assert os.environ["T_PLAIN"] == "abc:123"
    assert os.environ["T_QUOTED"] == "with spaces # not a comment"
    assert os.environ["T_SINGLE"] == "single"
    assert os.environ["T_EXPORT"] == "exported"
    assert os.environ["T_INLINE"] == "value"
    assert "T_EMPTY" not in os.environ  # an untouched template line means "unset"


def test_real_environment_wins_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T_WINS", "from-shell")
    env = tmp_path / ".env"
    env.write_text("T_WINS=from-file\n", encoding="utf-8")

    load_dotenv(env)

    import os

    assert os.environ["T_WINS"] == "from-shell"


def test_missing_file_is_fine(tmp_path: Path) -> None:
    load_dotenv(tmp_path / "nope.env")


def test_load_config_takes_bot_settings_from_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("ZOSCHEDULE_BOT_TOKEN", "ZOSCHEDULE_BOT_CHAT_ID", "ZOSCHEDULE_BOT_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / "prod.env"
    env.write_text(
        "ZOSCHEDULE_BOT_TOKEN=123:secret\nZOSCHEDULE_BOT_CHAT_ID=-1001\nZOSCHEDULE_BOT_THREAD_ID=2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOSCHEDULE_ENV_FILE", str(env))

    config = load_config()

    assert config.bot.token.get_secret_value() == "123:secret"
    assert (config.bot.chat_id, config.bot.thread_id) == ("-1001", 2)
    assert "secret" not in repr(config.bot)  # SecretStr keeps it out of logs

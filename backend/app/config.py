"""Чтение config.toml. Номер группы и фильтр — здесь, не в коде."""

from __future__ import annotations

import datetime as dt
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "config.toml"
_DEFAULT_DB = _REPO_ROOT / "backend" / "zoschedule.db"
_DEFAULT_ENV_FILE = _REPO_ROOT / ".env"


class GroupConfig(BaseModel):
    name: str


class PollConfig(BaseModel):
    interval_minutes: int = Field(default=60, gt=0)


PROXY_SCHEMES = frozenset({"socks5", "socks4", "http", "https"})
PROXY_HINT = (
    "ZOSCHEDULE_BOT_PROXY: ожидается socks5://[логин:пароль@]хост:порт "
    "(также socks4, http, https)"
)


TRUSTED_HINT = (
    "trusted_users: ожидается список положительных Telegram-id людей, "
    "в .env — через запятую: ZOSCHEDULE_BOT_TRUSTED_USERS=123456789,987654321"
)


class BotConfig(BaseModel):
    """Telegram bot. Token and chat id come from the environment, not from the repo."""


    today_at: dt.time = dt.time(7, 0)
    day_ahead: bool = False
    """Post the day's picture the evening before, at `today_at` of the previous day.

    At midnight the caption turns from "Завтра" into "Сегодня" by itself (see `sync_pictures`)."""
    week_at: dt.time = dt.time(7, 0)
    pin_week: bool = True
    silent: bool = False
    """Send every post without a sound (Telegram's `disable_notification`)."""
    refresh: bool = True
    """Poll the site from the bot itself, every `poll.interval_minutes`."""
    browser_path: Path | None = None
    token: SecretStr = SecretStr("")
    chat_id: str = ""
    thread_id: int | None = None
    """Fallback target for a bot never told /go. Normally the chat comes from /go."""
    trusted_users: list[int] = []  # pydantic copies the default for every instance
    """Telegram ids of the people who may run the bot (`/go`) and add it to a group.

    Everyone else is ignored, and a group they add the bot to is left at once."""
    proxy: SecretStr | None = None
    """Proxy for Telegram only (the college site is fetched directly). Optional.

    A secret: the URL may hold a login and password."""

    @field_validator("trusted_users", mode="before")
    @classmethod
    def _trusted_users_from_a_list_or_a_comma_string(cls, value: object) -> object:
        """`.env` gives "123,456"; config.toml gives a list."""
        if isinstance(value, str):
            try:
                return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
            except ValueError:
                raise ValueError(TRUSTED_HINT) from None
        return value

    @field_validator("trusted_users")
    @classmethod
    def _trusted_users_are_people(cls, value: list[int]) -> list[int]:
        if any(user_id <= 0 for user_id in value):
            raise ValueError(TRUSTED_HINT)  # a negative id is a group, not a person
        return sorted(set(value))

    @field_validator("proxy")
    @classmethod
    def _proxy_is_a_proxy_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        parsed = urlsplit(value.get_secret_value().strip())
        try:
            port = parsed.port
        except ValueError:
            port = None
        if parsed.scheme not in PROXY_SCHEMES or not parsed.hostname or port is None:
            raise ValueError(PROXY_HINT)  # never the value itself: it may hold the password
        return SecretStr(value.get_secret_value().strip())

    @property
    def proxy_url(self) -> str | None:
        return self.proxy.get_secret_value() if self.proxy else None

    @property
    def proxy_label(self) -> str | None:
        """The proxy for a log line: scheme, host and port, never the login or password."""
        if self.proxy_url is None:
            return None
        parsed = urlsplit(self.proxy_url)
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    @property
    def configured(self) -> bool:
        """Has a token: enough to talk to Telegram. Without a chat it waits for /go."""
        return bool(self.token.get_secret_value())


class AppConfig(BaseModel):
    # pydantic decides at the top model whether an error echoes the rejected value, and
    # the bot's proxy URL and token are secrets that must not land in a console or a log.
    model_config = ConfigDict(hide_input_in_errors=True)

    group: GroupConfig
    poll: PollConfig = Field(default_factory=PollConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    db_path: Path = _DEFAULT_DB


def load_dotenv(path: Path) -> None:
    """Fills `os.environ` from a `.env` file. Real environment variables win.

    A few lines of parsing instead of a dependency: `KEY=value`, `#` comments,
    optional quotes and `export`. Empty values are skipped, so an untouched
    template line does not shadow a default.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and value:
            os.environ.setdefault(key, value)


def load_config(path: Path | None = None) -> AppConfig:
    # ZOSCHEDULE_ENV_FILE points elsewhere, or set empty to skip the file (tests do).
    env_file = os.environ.get("ZOSCHEDULE_ENV_FILE")
    if env_file is None:
        load_dotenv(_DEFAULT_ENV_FILE)
    elif env_file:
        load_dotenv(Path(env_file))

    config_path = path or Path(os.environ.get("ZOSCHEDULE_CONFIG", _DEFAULT_CONFIG))
    raw: dict[str, object] = tomllib.loads(config_path.read_text(encoding="utf-8"))

    db_override = os.environ.get("ZOSCHEDULE_DB")
    if db_override:
        raw["db_path"] = Path(db_override)

    bot_raw = raw.get("bot")
    bot: dict[str, object] = dict(bot_raw) if isinstance(bot_raw, dict) else {}
    for env_name, field in (
        ("ZOSCHEDULE_BOT_TOKEN", "token"),
        ("ZOSCHEDULE_BOT_CHAT_ID", "chat_id"),
        ("ZOSCHEDULE_BOT_THREAD_ID", "thread_id"),
        ("ZOSCHEDULE_BOT_PROXY", "proxy"),
        ("ZOSCHEDULE_BOT_TRUSTED_USERS", "trusted_users"),
    ):
        value = os.environ.get(env_name)
        if value:
            bot[field] = value
    raw["bot"] = bot

    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()

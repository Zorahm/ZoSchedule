#!/usr/bin/env bash
# ZoSchedule: установка Telegram-бота на Linux с автозапуском (systemd).
#
#   ./install.sh             установить или обновить: окружение, браузер, токен, службу
#   ./install.sh status      состояние службы
#   ./install.sh logs        журнал бота (Ctrl+C — выйти)
#   ./install.sh restart     перезапустить бота
#   ./install.sh token       сменить токен и перезапустить
#   ./install.sh uninstall   остановить и убрать службу (файлы и .env остаются)
#
# Запускайте от обычного пользователя, не от root: sudo скрипт попросит сам, только
# там, где он нужен (системные пакеты для браузера и файл службы). Служба будет
# работать от этого же пользователя.
#
# Чат бот узнаёт из команды /go, которую администратор пишет в группе.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE="zoschedule-bot"
UNIT="/etc/systemd/system/${SERVICE}.service"
VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"
ENV_FILE="${ROOT}/.env"
CONFIG="${ROOT}/config.toml"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

interactive() { [[ -t 0 ]]; }

# ask "Вопрос" "значение по умолчанию": печатает ответ; без терминала отдаёт умолчание.
ask() {
    local prompt="$1" default="${2:-}" reply=""
    if ! interactive; then printf '%s' "$default"; return; fi
    read -r -p "$prompt [${default}]: " reply || true
    printf '%s' "${reply:-$default}"
}

confirm() {  # confirm "Вопрос" [y|n умолчание]
    local prompt="$1" default="${2:-y}" reply="" hint="Y/n"
    [[ "$default" == n ]] && hint="y/N"
    if ! interactive; then [[ "$default" == y ]]; return; fi
    read -r -p "$prompt [$hint]: " reply || true
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[YyДд] ]]
}

sudo_cmd() {
    if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

# ---------------------------------------------------------------- Python / venv

find_python() {
    local candidate
    for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

setup_venv() {
    local base
    base="$(find_python)" || die "Нужен Python 3.11 или новее. Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip"
    say "Python: $($base --version)"

    if [[ ! -x "$PY" ]]; then
        say "Создаю окружение .venv"
        "$base" -m venv "$VENV" \
            || die "Не удалось создать venv. Debian/Ubuntu: sudo apt install python3-venv"
    fi
    say "Ставлю зависимости (нужен интернет)"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -e "${ROOT}/backend"
}

setup_browser() {
    # Картинки расписания рисует браузер. На Linux берём Chromium от Playwright:
    # он кладётся в домашний каталог пользователя, с системным Chrome не мешает.
    say "Проверяю браузер для картинок (Chromium от Playwright)"
    if "$PY" -m playwright install --with-deps chromium; then
        return 0
    fi
    warn "Установка с системными библиотеками не удалась (не apt-дистрибутив или нет sudo)."
    warn "Пробую без них; если картинки не рисуются, доустановите библиотеки Chromium вручную."
    "$PY" -m playwright install chromium || die "Не удалось поставить Chromium."
}

# ------------------------------------------------------------------ .env / config

# set_env_key KEY VALUE: пишет строку в .env, чужие строки не трогает.
set_env_key() {
    KEY="$1" VALUE="$2" FILE="$ENV_FILE" "$PY" - <<'PY'
import os, pathlib, re

key, value, path = os.environ["KEY"], os.environ["VALUE"], pathlib.Path(os.environ["FILE"])
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out, done = [], False
for line in lines:
    if re.match(rf"^\s*(export\s+)?{re.escape(key)}\s*=", line):
        if not done:
            out.append(f"{key}={value}")
            done = True
        continue
    out.append(line)
if not done:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
    chmod 600 "$ENV_FILE"
}

env_has_token() {
    [[ -f "$ENV_FILE" ]] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?ZOSCHEDULE_BOT_TOKEN=.+' "$ENV_FILE"
}

# check_token TOKEN: печатает @имя бота, код 1 — Telegram токен не принял.
check_token() {
    ZS_TOKEN="$1" "$PY" - <<'PY'
import json, os, sys, urllib.request

try:
    url = "https://api.telegram.org/bot" + os.environ["ZS_TOKEN"] + "/getMe"
    data = json.load(urllib.request.urlopen(url, timeout=20))
except Exception:
    sys.exit(1)
if not data.get("ok"):
    sys.exit(1)
print(data["result"]["username"])
PY
}

ask_token() {
    local token name tries=0
    interactive || die "Токен не задан, а терминала нет. Задайте ZOSCHEDULE_BOT_TOKEN в ${ENV_FILE}."
    while (( tries < 3 )); do
        tries=$((tries + 1))
        read -r -s -p "Токен бота (выдаёт @BotFather, ввод скрыт): " token || true
        echo
        [[ -n "$token" ]] || { warn "Пустой ввод."; continue; }
        if name="$(check_token "$token")"; then
            say "Бот найден: @${name}"
            set_env_key ZOSCHEDULE_BOT_TOKEN "$token"
            return 0
        fi
        warn "Telegram не принял этот токен (или нет интернета)."
        if confirm "Сохранить его всё равно?" n; then
            set_env_key ZOSCHEDULE_BOT_TOKEN "$token"
            return 0
        fi
    done
    die "Токен не введён."
}

# cfg_get SECTION KEY: значение из config.toml.
cfg_get() {
    "$PY" - "$1" "$2" "$CONFIG" <<'PY'
import sys, tomllib

section, key, path = sys.argv[1:4]
with open(path, "rb") as handle:
    print(tomllib.load(handle).get(section, {}).get(key, ""))
PY
}

# cfg_set SECTION KEY VALUE: меняет строковое значение, комментарий в строке сохраняет.
cfg_set() {
    SECTION="$1" KEY="$2" VALUE="$3" FILE="$CONFIG" "$PY" - <<'PY'
import os, pathlib, re

section, key, value = os.environ["SECTION"], os.environ["KEY"], os.environ["VALUE"]
path = pathlib.Path(os.environ["FILE"])
text = path.read_text(encoding="utf-8")
block = re.search(rf"(?ms)^\[{re.escape(section)}\][ \t]*$.*?(?=^\[|\Z)", text)
if block is None:
    raise SystemExit(f"в config.toml нет раздела [{section}]")
line = re.compile(rf'(?m)^({re.escape(key)}\s*=\s*)"[^"]*"')
if not line.search(block.group(0)):
    raise SystemExit(f"в разделе [{section}] нет строки {key} = \"...\"")
updated = line.sub(lambda m: f'{m.group(1)}"{value}"', block.group(0), count=1)
path.write_text(text[: block.start()] + updated + text[block.end():], encoding="utf-8")
PY
}

valid_time() { [[ "$1" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; }

configure() {
    if env_has_token; then
        if confirm "Токен бота уже сохранён в .env. Оставить?" y; then
            say "Токен оставляю как есть"
        else
            ask_token
        fi
    else
        ask_token
    fi

    say "Настройки (Enter — оставить как есть)"
    local group today week
    group="$(ask "Группа (ровно как на сайте колледжа)" "$(cfg_get group name)")"
    if [[ -n "$group" && "$group" != *'"'* && "$group" != *'\'* ]]; then
        cfg_set group name "$group"
    else
        warn "Название группы не подошло, оставляю прежнее."
    fi

    today="$(ask "Время картинки «Сегодня», МСК" "$(cfg_get bot today_at)")"
    if valid_time "$today"; then cfg_set bot today_at "$today"; else warn "Время «Сегодня» не в формате ЧЧ:ММ, оставляю прежнее."; fi

    week="$(ask "Время недельного расписания по воскресеньям, МСК" "$(cfg_get bot week_at)")"
    if valid_time "$week"; then cfg_set bot week_at "$week"; else warn "Время недели не в формате ЧЧ:ММ, оставляю прежнее."; fi
}

# ------------------------------------------------------------------------ systemd

have_systemd() {
    command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]
}

write_unit() {
    local user tmp
    user="$(id -un)"
    tmp="$(mktemp)"
    cat >"$tmp" <<EOF
[Unit]
Description=ZoSchedule Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
WorkingDirectory=${ROOT}/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=${PY} -m app.bot run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    sudo_cmd install -m 644 "$tmp" "$UNIT"
    rm -f "$tmp"
}

install_service() {
    if ! have_systemd; then
        warn "systemd не найден: автозапуск не настроен."
        say "Запустить вручную: cd ${ROOT}/backend && ${PY} -m app.bot run"
        return 0
    fi
    say "Настраиваю автозапуск (systemd, служба ${SERVICE})"
    write_unit
    sudo_cmd systemctl daemon-reload
    sudo_cmd systemctl enable "$SERVICE" >/dev/null
    sudo_cmd systemctl restart "$SERVICE"
    sleep 3
    if sudo_cmd systemctl is-active --quiet "$SERVICE"; then
        say "Бот запущен и стартует вместе с системой"
    else
        warn "Служба не поднялась. Причина:"
        sudo_cmd journalctl -u "$SERVICE" -n 25 --no-pager || true
        exit 1
    fi
}

# ------------------------------------------------------------------------ commands

cmd_install() {
    [[ "$(uname -s)" == Linux ]] || die "Этот скрипт для Linux. На Windows: start-bot.bat"
    if [[ $EUID -eq 0 && -z "${ALLOW_ROOT:-}" ]]; then
        die "Запустите от обычного пользователя (sudo скрипт попросит сам). Осознанно от root: ALLOW_ROOT=1 ./install.sh"
    fi
    [[ -f "${ROOT}/backend/pyproject.toml" ]] || die "Запускайте скрипт из корня проекта ZoSchedule."

    setup_venv
    setup_browser
    configure
    install_service

    cat <<EOF

Готово. Осталось два шага в Telegram:
  1. Добавьте бота в группу и сделайте администратором с правами
     «Удалять сообщения» и «Закреплять сообщения».
  2. Напишите в группе (в нужной теме, если группа с темами) команду /go.
     Бот запомнит чат, пришлёт неделю и ближайший день и дальше будет работать сам.

Полезное:
  ./install.sh status      состояние
  ./install.sh logs        журнал
  ./install.sh restart     перезапуск
EOF
}

cmd_token() {
    setup_venv_quiet
    ask_token
    have_systemd && sudo_cmd systemctl restart "$SERVICE" && say "Бот перезапущен"
}

setup_venv_quiet() {
    [[ -x "$PY" ]] || die "Сначала выполните ./install.sh"
}

cmd_uninstall() {
    have_systemd || die "systemd не найден: удалять нечего."
    say "Останавливаю и убираю службу ${SERVICE}"
    sudo_cmd systemctl disable --now "$SERVICE" 2>/dev/null || true
    sudo_cmd rm -f "$UNIT"
    sudo_cmd systemctl daemon-reload
    say "Готово. Каталог ${ROOT}, .env и .venv не тронуты: удалите их вручную, если нужно."
}

main() {
    case "${1:-install}" in
        install)   cmd_install ;;
        status)    sudo_cmd systemctl status "$SERVICE" --no-pager || true ;;
        logs)      sudo_cmd journalctl -u "$SERVICE" -f -n 100 ;;
        restart)   sudo_cmd systemctl restart "$SERVICE" && say "Перезапущено" ;;
        token)     cmd_token ;;
        uninstall) cmd_uninstall ;;
        -h|--help|help) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
        *) die "Неизвестная команда: $1 (см. ./install.sh help)" ;;
    esac
}

# Выполняем только при прямом запуске: так функции можно подключить и проверить отдельно.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

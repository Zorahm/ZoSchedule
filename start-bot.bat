@echo off
rem ZoSchedule: запуск Telegram-бота на Windows.
rem
rem Первый запуск сам подготовит всё нужное: найдёт Python 3.11+, создаст
rem виртуальное окружение .venv, поставит зависимости, проверит браузер для
rem картинок (Chrome или Edge; иначе поставит Chromium) и спросит токен бота.
rem Дальше просто запускает бота и перезапускает его, если он упал.
rem
rem   start-bot.bat          подготовить (если нужно) и запустить
rem   start-bot.bat setup    только подготовить, не запускать (и переустановить зависимости)
rem   start-bot.bat token    задать токен заново
rem
rem Чат бот узнаёт из команды /go, которую администратор пишет в группе.

setlocal EnableExtensions
chcp 65001 >nul
title ZoSchedule bot
cd /d "%~dp0"

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "PY=%VENV%\Scripts\python.exe"
set "MARK=%VENV%\zoschedule.installed"
set "ENVFILE=%ROOT%.env"
set "MODE=%~1"

if /i "%MODE%"=="help" goto :help
if /i "%MODE%"=="/?" goto :help

rem ---------- 1. Python и окружение ----------------------------------------
if not exist "%PY%" call :create_venv
if not exist "%PY%" goto :fail

if /i "%MODE%"=="setup" if exist "%MARK%" del "%MARK%" >nul 2>&1

if not exist "%MARK%" (
    echo [..] Ставлю зависимости ^(нужен интернет, это разово^)
    "%PY%" -m pip install --quiet --upgrade pip
    "%PY%" -m pip install --quiet -e "%ROOT%backend"
    if errorlevel 1 (
        echo [!] Не удалось поставить зависимости.
        goto :fail
    )
    echo installed> "%MARK%"
)

rem ---------- 2. Браузер для картинок ---------------------------------------
call :have_browser
if not defined HAVE_BROWSER (
    echo [..] Не найден Chrome или Edge, ставлю Chromium для картинок ^(~150 МБ^)
    "%PY%" -m playwright install chromium
    if errorlevel 1 (
        echo [!] Не получилось поставить браузер. Установите Google Chrome и запустите снова.
        goto :fail
    )
)

rem ---------- 3. Токен бота --------------------------------------------------
if /i "%MODE%"=="token" goto :ask_token
call :have_token
if not defined HAVE_TOKEN goto :ask_token
:after_token

if /i "%MODE%"=="setup" (
    echo [ok] Всё готово. Запуск: start-bot.bat
    goto :done
)

rem ---------- 4. Запуск с автоперезапуском ----------------------------------
echo.
echo [ok] Запускаю бота. Остановить: Ctrl+C.
echo      Добавьте бота в группу администратором и напишите там /go.
echo.
pushd "%ROOT%backend"
:loop
"%PY%" -m app.bot run
echo.
echo [!] Бот остановился ^(код %ERRORLEVEL%^). Перезапуск через 10 секунд, Ctrl+C для выхода.
timeout /t 10 /nobreak >nul
goto :loop

rem =========================================================================
:create_venv
rem Отдельная подпрограмма, а не блок if (...): %BASEPY% внутри блока раскрылся бы
rem до того, как :find_python его задаст.
call :find_python
if not defined BASEPY (
    echo [!] Не найден Python 3.11 или новее.
    echo     Установите его с https://www.python.org/downloads/ ^(отметьте "Add python.exe to PATH"^)
    echo     и запустите этот файл снова.
    exit /b 1
)
echo [..] Создаю окружение .venv ^(%BASEPY%^)
%BASEPY% -m venv "%VENV%"
exit /b %ERRORLEVEL%

:ask_token
set "TRIES=0"
:ask_again
set /a TRIES+=1
set "TOKEN="
echo.
echo Введите токен бота ^(выдаёт @BotFather, вид 123456:ABC...^). Ввод скрыт.
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "$s = Read-Host -AsSecureString; [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "TOKEN=%%T"
if not defined TOKEN (
    echo [!] Пустой ввод.
    if %TRIES% GEQ 3 goto :fail
    goto :ask_again
)
set "CHECK_TOKEN=%TOKEN%"
set "BOTNAME="
for /f "usebackq delims=" %%N in (`powershell -NoProfile -Command "try { (Invoke-RestMethod -Uri ('https://api.telegram.org/bot' + $env:CHECK_TOKEN + '/getMe') -TimeoutSec 20).result.username } catch { }"`) do set "BOTNAME=%%N"
set "CHECK_TOKEN="
if not defined BOTNAME (
    echo [!] Telegram не принял этот токен ^(или нет интернета^).
    if %TRIES% GEQ 3 goto :fail
    goto :ask_again
)
echo [ok] Бот найден: @%BOTNAME%
call :save_token
set "TOKEN="
if /i "%MODE%"=="token" (
    echo [ok] Токен сохранён в .env
    goto :done
)
goto :after_token

:save_token
rem Токен пишется в .env; чужие строки файла не трогаем, старую строку с токеном убираем.
if exist "%ENVFILE%" (
    findstr /v /b /i /c:"ZOSCHEDULE_BOT_TOKEN=" "%ENVFILE%" > "%ENVFILE%.tmp"
    move /y "%ENVFILE%.tmp" "%ENVFILE%" >nul
)
>>"%ENVFILE%" echo ZOSCHEDULE_BOT_TOKEN=%TOKEN%
exit /b 0

:have_token
set "HAVE_TOKEN="
if defined ZOSCHEDULE_BOT_TOKEN set "HAVE_TOKEN=1"
if exist "%ENVFILE%" (
    findstr /r /b /i /c:"ZOSCHEDULE_BOT_TOKEN=." "%ENVFILE%" >nul 2>&1 && set "HAVE_TOKEN=1"
)
exit /b 0

:have_browser
set "HAVE_BROWSER="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
    "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
) do if exist %%~P set "HAVE_BROWSER=1"
exit /b 0

:find_python
set "BASEPY="
for %%V in (3.14 3.13 3.12 3.11) do (
    if not defined BASEPY (
        py -%%V -c "import sys" >nul 2>&1 && set "BASEPY=py -%%V"
    )
)
if not defined BASEPY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "BASEPY=python"
)
exit /b 0

:help
echo Использование:
echo   start-bot.bat          подготовить ^(если нужно^) и запустить бота
echo   start-bot.bat setup    только подготовить окружение, не запускать
echo   start-bot.bat token    задать токен бота заново
goto :done

:fail
echo.
echo [!] Остановлено из-за ошибки выше.
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0

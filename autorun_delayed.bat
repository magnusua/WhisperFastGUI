@echo off
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
set "VBS_PATH=%SCRIPT_DIR%start_delayed.vbs"
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNK_PATH=%STARTUP_FOLDER%\Whisper Fast GUI (delayed).lnk"

if not exist "%VBS_PATH%" (
    echo Помилка: у папці скрипта не знайдено start_delayed.vbs
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($env:LNK_PATH); $s.TargetPath = $env:VBS_PATH; $s.WorkingDirectory = ($env:SCRIPT_DIR).TrimEnd([char]92); $s.Description = 'Whisper Fast GUI - start with 25s delay'; $s.Save(); Write-Host 'Готово. Програму додано в автозавантаження (з затримкою 25 сек).'; Write-Host ('Ярлик: ' + $env:LNK_PATH)"

echo.
pause

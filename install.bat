@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo ==========================================
echo   Whisper Fast GUI — установка зависимостей
echo ==========================================
echo.

:: Переход в директорию скрипта
cd /d "%~dp0"

:: Python з settings.json (python_path), інакше — python з PATH
set "PYEXE="
if exist "settings.json" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "try { (Get-Content -Raw 'settings.json' | ConvertFrom-Json).python_path } catch { '' }"`) do set "PYEXE=%%A"
)
if defined PYEXE if exist "!PYEXE!" (
    echo Використовується Python з settings.json:
    echo   !PYEXE!
    echo.
    "!PYEXE!" -m whisperfast.setup.installer
    if not errorlevel 1 (
        pause
        exit /b 0
    )
)

:: Попытка запуска установщика
echo Запуск установщика зависимостей...
echo.
python -m whisperfast.setup.installer
if not errorlevel 1 (
    pause
    exit /b 0
)

:: Если не получилось - проверяем Python
echo.
echo ⚠ Не удалось запустить установщик. Проверка Python...
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден. Установите Python 3.9 или выше (рекомендуется 3.10+)
    echo.
    echo Скачать Python можно с: https://www.python.org/downloads/
    echo При установке обязательно отметьте опцию "Add Python to PATH"
    pause
    exit /b 1
)

python --version
echo ✓ Python найден
echo.
echo ❌ Ошибка при запуске установщика зависимостей
echo Попробуйте запустить вручную: python -m whisperfast.setup.installer
pause
exit /b 1

@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo ==========================================
echo   Whisper Fast GUI — установка зависимостей
echo ==========================================
echo.

:: Переход в директорию скрипта
cd /d "%~dp0"

:: Попытка запуска установщика
echo Запуск установщика зависимостей...
echo.
python installer.py
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
echo Попробуйте запустить вручную: python installer.py
pause
exit /b 1

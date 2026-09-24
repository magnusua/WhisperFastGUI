@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title FTW — установка зависимостей

echo ==========================================
echo   FTW — установка зависимостей
echo ==========================================
echo   pip / CUDA — только python.exe ^(не pythonw^)
echo.
echo   Ставится:
echo     Python-пакеты: torch, faster-whisper, sounddevice, numpy,
echo     pydub, pygame, pystray, Pillow, cursor-sdk, markitdown
echo     Windows: pycaw — автозапись звонков ^(Zoom/Teams/Meet/Viber/...^)
echo     FFmpeg — декод медиа + кодек записи Opus/AAC/MP3
echo     Pandoc — экспорт Word ^(если доступен установщик^)
echo ==========================================
echo.

cd /d "%~dp0"

set "PYEXE="

:: 1) Python из settings.json. Для pip всегда python.exe: pythonw прячет вывод и ломает установку.
if exist "settings.json" (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; try { $p=[string]((Get-Content -Raw -LiteralPath 'settings.json' | ConvertFrom-Json).python_path); if (-not $p) { exit 0 }; if ($p -match '(?i)pythonw\.exe$') { $p = $p -replace '(?i)pythonw\.exe$','python.exe' } elseif (([IO.Path]::GetFileName($p)) -eq 'pythonw') { $p = Join-Path ([IO.Path]::GetDirectoryName($p)) 'python' }; if (Test-Path -LiteralPath $p) { $p } } catch { }"`) do set "PYEXE=%%A"
)

:: 2) py-launcher: 3.12 / 3.11 / 3.13 / 3.10 / 3.9 (рекомендовано 3.12)
if not defined PYEXE (
    for %%V in (3.12 3.11 3.13 3.10 3.9) do (
        if not defined PYEXE (
            set "CAND="
            for /f "delims=" %%A in ('py -%%V -c "import sys; print(sys.executable)" 2^>nul') do set "CAND=%%A"
            if defined CAND (
                call :to_python_exe CAND
                if exist "!CAND!" set "PYEXE=!CAND!"
            )
        )
    )
)

:: 3) python из PATH, пропуск заглушки Microsoft Store (WindowsApps)
if not defined PYEXE (
    for /f "delims=" %%A in ('where python 2^>nul') do (
        if not defined PYEXE (
            set "CAND=%%A"
            echo !CAND! | findstr /I /C:"WindowsApps" >nul
            if errorlevel 1 (
                call :to_python_exe CAND
                if exist "!CAND!" set "PYEXE=!CAND!"
            )
        )
    )
)

if not defined PYEXE (
    echo Python 3.9-3.13 не найден.
    echo Скачайте установщик: https://www.python.org/downloads/windows/
    echo Рекомендуется Python 3.12. При установке отметьте "Add python.exe to PATH".
    echo После установки снова запустите install.bat из этой папки.
    pause
    exit /b 1
)

echo Используется Python:
echo   !PYEXE!
"!PYEXE!" --version
echo.

"!PYEXE!" -c "import sys; raise SystemExit(0 if (3,9)<=sys.version_info[:2]<=(3,13) else 1)"
if errorlevel 1 (
    echo Этот Python вне рабочего диапазона 3.9-3.13. Рекомендуется 3.12.
    echo Установка может не найти колёса torch / faster-whisper.
    echo.
)

set "GPU_LINE=NOTFOUND"
for /f "usebackq delims=" %%A in (`"!PYEXE!" -c "from whisperfast.setup.gpu_info import install_gpu_status_line; print(install_gpu_status_line())"`) do set "GPU_LINE=%%A"

set "INSTALL_ARGS=--cpu"
echo !GPU_LINE! | findstr /B /C:"SAVED:" >nul
if not errorlevel 1 (
    set "INSTALL_ARGS=--cuda"
    echo В settings.json указана видеокарта NVIDIA: !GPU_LINE:~6!
    echo Ставим PyTorch CUDA 12.8 ^(cu128^) и библиотеки NVIDIA, без вопроса.
    goto after_cuda_choice
)
echo !GPU_LINE! | findstr /B /C:"FOUND:" >nul
if not errorlevel 1 (
    set "INSTALL_ARGS=--cuda"
    echo Найдена видеокарта NVIDIA: !GPU_LINE:~6!
    echo Ставим PyTorch CUDA 12.8 ^(cu128^) и библиотеки NVIDIA, без вопроса.
    goto after_cuda_choice
)

echo Видеокарта NVIDIA не найдена автоматически.
echo ----------------------------------------
echo   У вас видеокарта NVIDIA?
echo   Y = да, установить CUDA (PyTorch cu128 + библиотеки NVIDIA)
echo   N = нет, пропустить CUDA и продолжить установку на CPU
echo ----------------------------------------
choice /C YN /N /M "NVIDIA [Y/N]?"
if errorlevel 2 goto after_cuda_choice
set "INSTALL_ARGS=--cuda"
:after_cuda_choice
echo.
if /i "!INSTALL_ARGS!"=="--cuda" (
    echo Выбрано: CUDA 12.8 ^(cu128^) — ставим PyTorch с этого индекса.
) else (
    echo Выбрано: без CUDA — дальше CPU.
)
echo.

echo Запуск установщика зависимостей...
echo.
"!PYEXE!" -m whisperfast.setup.installer !INSTALL_ARGS!
set "ERR=!errorlevel!"
echo.
if !ERR! neq 0 (
    echo Установка не завершена. Смотрите ошибки pip выше, затем снова install.bat.
    echo Для pip нужен python.exe — не запускайте установку через pythonw.exe.
    pause
    exit /b 1
)

:: numpy — запись (sounddevice callback); pycaw — автостарт по аудиосессии процесса
echo Доп. пакеты записи встреч: numpy, pycaw ...
"!PYEXE!" -m pip install --upgrade numpy
if errorlevel 1 (
    echo   Предупреждение: numpy не установился. Запись встречи может не стартовать.
)
"!PYEXE!" -m pip install --upgrade pycaw
if errorlevel 1 (
    echo   pycaw не обязателен: автозапись будет смотреть окна, без «идёт звук».
)
echo.

call :find_ffmpeg
if defined FFMPEG (
    echo FFmpeg: !FFMPEG!
    "!FFMPEG!" -hide_banner -encoders 2>nul | findstr /I /C:"libopus" /C:" aac " /C:"libmp3lame" >nul
    if errorlevel 1 (
        echo   Сборка без libopus/AAC: Stop/уривок останутся WAV. Нужен FFmpeg с libopus.
        echo   Повтор: winget install --id Gyan.FFmpeg -e
    ) else (
        echo   Кодеки записи: Opus / AAC / MP3 найдены.
    )
) else (
    echo FFmpeg не найден в PATH и в tools\ffmpeg\bin.
    echo   Без него нет декода видео и кодирования записи в Opus/AAC/MP3.
    echo   Поставьте: winget install --id Gyan.FFmpeg -e
    echo   или снова запустите install.bat — установщик пробует скачать FFmpeg сам.
)
echo.

echo ----------------------------------------
echo   Готово. Запускайте FTW так:
echo     run_whisper.vbs       обычный запуск ^(без окна консоли^)
echo     autorun_delayed.bat   ярлык в автозагрузку Windows ^(задержка 25 с^)
echo     start_delayed.vbs     тот же отложенный запуск вручную
echo.
echo   Запись: Record / Ctrl+Shift+R, пауза Ctrl+Shift+P, уривок и шестерёнка
echo   в шапке окна. Кодек по умолчанию — Opus 24 кбит/с моно.
echo.
echo   CLI ^(из этой папки, тот же Python^):
echo     "!PYEXE!" main.py sessions
echo     "!PYEXE!" main.py process ^<папка^>
echo     "!PYEXE!" main.py record start^|stop
echo.
echo   run_whisper.vbs сам возьмёт Python из settings.json и вызовет
echo   pythonw + main.py — так и задумано, консоль не нужна.
echo.
echo   Не открывайте pythonw.exe или main.py двойным щелчком в Проводнике:
echo   не будет рабочей папки и интерпретатора из settings.json.
echo   install.bat / pip — только через python.exe ^(этот файл так и делает^).
echo ----------------------------------------
echo.
pause
exit /b 0

:find_ffmpeg
set "FFMPEG="
if exist "%~dp0tools\ffmpeg\bin\ffmpeg.exe" (
    set "FFMPEG=%~dp0tools\ffmpeg\bin\ffmpeg.exe"
    goto :eof
)
for /f "delims=" %%A in ('where ffmpeg 2^>nul') do (
    if not defined FFMPEG set "FFMPEG=%%A"
)
goto :eof

:to_python_exe
set "P=!%~1!"
if /i "!P:~-11!"=="pythonw.exe" set "P=!P:~0,-11!python.exe"
set "%~1=!P!"
goto :eof

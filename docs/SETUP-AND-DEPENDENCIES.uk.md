# Встановлення та залежності

## Навіщо існує цей документ

Практичний опис того, як застосунок потрапляє з нуля в робочий стан на конкретній машині: які версії ОС/Python підтримуються, як ставляться pip-залежності й системні утиліти (FFmpeg, Pandoc), і як увімкнути автозапуск. Якщо цікавить не «як встановити», а «як влаштований код інсталятора» — дивіться карту модулів `setup/` в [INTERNAL-ARCHITECTURE.uk.md](INTERNAL-ARCHITECTURE.uk.md).

## Технічні вимоги

- **Python:** 3.9–3.13; рекомендовано **3.11 або 3.12**. Python 3.14+ часто не підходить — для нього ще немає коліс `ctranslate2`/`torch` на PyPI.
- **ОС:** Windows 10/11, Linux (X11/Wayland), macOS.
- **GPU:** рекомендована відеокарта NVIDIA з CUDA 12.1 (`cu121`) для прискорення на Windows/Linux; AMD Radeon та інші GPU без CUDA працюють лише в режимі CPU.
- **FFmpeg** у PATH — обов'язковий для аудіо/відео.
- **Pandoc** — опційний, лише для «MD → Word».
- Python 3.13+: автоматично додається `pyaudioop` (заміна прибраного модуля `audioop`, потрібного для `pydub`).

## Підтримувані формати вхідних файлів

| Тип | Формати |
|---|---|
| Аудіо | `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg` |
| Відео | `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.webm` |
| Текст | `.md`, `.markdown`, `.txt`, `.text`, `.rst`, `.csv`, `.html`, `.htm` |
| Документи | `.pdf`, `.doc`, `.docx` (конвертуються в Markdown через `markitdown`) |

Список — єдине джерело в `whisperfast/config.py` (`AUDIO_EXTENSIONS`/`VIDEO_EXTENSIONS`/`TEXT_EXTENSIONS`/`OFFICE_TO_MD_EXTENSIONS`).

## Первинне встановлення

1. **Python 3.9–3.13** (рекомендовано 3.12), позначити «Add Python to PATH» при встановленні.
2. **FFmpeg** у PATH: Windows — `winget install --id Gyan.FFmpeg -e` або `choco install ffmpeg`; macOS — `brew install ffmpeg`; Linux — `sudo apt install ffmpeg`.
3. **(Опційно) Pandoc** — лише для «MD → Word»: `winget install --id JohnMacFarlane.Pandoc -e` / `choco install pandoc` / `brew install pandoc` / `sudo apt install pandoc`. Після ручного встановлення потрібен перезапуск термінала/програми, щоб підхопився оновлений PATH.
4. **`install.bat`** (Windows) або кнопка **[Залежності]** у GUI — запускає `python -m whisperfast.setup.installer`, який: перевіряє Python і вже встановлені пакети → оновлює `pip`/`setuptools`/`wheel`/`packaging` → встановлює PyTorch (CUDA 12.1 за наявності NVIDIA), `faster-whisper`, `ctranslate2` → **пропускає** `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` при першому встановленні (ставляться окремо через [Оновлення]/[Залежності] у GUI) → встановлює `pygame`, `pydub`, `tkinterdnd2-universal`, `pystray`, `Pillow`, `cursor-sdk`, `markitdown[pdf,docx,pptx,xlsx,xls]` → за потреби ставить FFmpeg і Pandoc.
5. Запуск: `run_whisper.vbs` (Windows, без вікна консолі) або `python main.py` / `python3 main.py` (Linux/macOS). Прапорець `--transcribe` запускає обробку всієї поточної черги одразу після старту.

`install.bat` — тонка обгортка: PowerShell-командою читає `python_path` із `settings.json`, якщо він там уже збережений з попереднього запуску, інакше використовує `python` з PATH; на macOS/Linux скриптового еквівалента немає (тільки прямий виклик `python -m whisperfast.setup.installer`).

## Вибір Python-інтерпретатора

`setup/python_selector.py` при першому запуску знаходить установлені інтерпретатори (`py -0p` на Windows, `where`/пошук у типових шляхах), і якщо їх кілька — показує діалог вибору. Обраний шлях і версія зберігаються в `settings.json` (`python_path`/`python_version`) і використовуються надалі й для запуску (`run_whisper.vbs`, `start_delayed.vbs`), і для `install.bat`. Щоб повторити вибір — видалити ці два ключі з `settings.json` вручну.

## Залежності pip

| Пакет | Призначення |
|---|---|
| `pip`, `setuptools`, `wheel` | Базова інфраструктура встановлення пакетів. |
| `torch`, `torchvision`, `torchaudio` | PyTorch — основа роботи моделі Whisper на CPU/GPU. |
| `faster-whisper` | Модель розпізнавання мовлення (обгортка над Whisper). |
| `ctranslate2` | Інференс-ядро, яке використовує `faster-whisper`. |
| `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` | CUDA-бібліотеки для GPU-прискорення (ставляться лише з GUI, не при першому встановленні). |
| `pydub` | Обробка аудіо: відрізки, експорт у MP3, тривалість файлу. |
| `pygame` | Звук завершення обробки. |
| `tkinterdnd2-universal` | Drag & Drop файлів у вікно й у таблиці черги. |
| `pystray`, `Pillow` | Іконка й меню в системному треї. |
| `cursor-sdk` | Постпроцесинг через Cursor SDK. |
| `markitdown` | Конвертація PDF/DOC/DOCX у Markdown (`[pdf,docx,pptx,xlsx,xls]` extras). |
| `packaging` | Порівняння версій пакетів при перевірці оновлень. |
| `pyaudioop` | Заміна видаленого в Python 3.13+ модуля `audioop` (потрібен для `pydub`). |

**Виправлено:** у корені проєкту тепер є `requirements.txt` із зафіксованими діапазонами версій (перевірено реальними даними PyPI, зокрема обмеження `faster-whisper` на сумісний `ctranslate2<5,>=4.0`). Крім самого файлу-маніфесту, `setup/installer.py: _get_full_install_commands()` тепер збирає команду `pip install` для `faster-whisper`/`ctranslate2` із тих самих констант (`FASTER_WHISPER_PIP_SPEC`/`CTRANSLATE2_PIP_SPEC`) — тобто фіксація версій реально впливає на те, що ставиться при встановленні/оновленні, а не існує лише як документація. `torch` (CUDA/CPU-індекс) залишається таким, що обирається динамічно за результатом виявлення GPU — це свідомо не зафіксовано жорстко.

`setup/installer.py` також самостійно прибирає «зламані» залишки перерваного `pip upgrade` (каталоги виду `~ip`/`~umpy`) перед повторною спробою встановлення — це вже реалізовано і працює добре.

## Зовнішні утиліти: FFmpeg і Pandoc

`setup/external_tools.py` реалізує ланцюжок фолбеків для встановлення системних (не-pip) утиліт: спершу пакетний менеджер ОС (winget → Chocolatey на Windows, Homebrew на macOS), якщо жодного немає — завантаження готового релізу з GitHub (з урахуванням архітектури: `arm64`/`x86_64`, і платформи) у локальний каталог `tools/` без встановлення в систему. Кнопка **[Система]** показує поточний статус (версії, чи знайдено в PATH) і, якщо чогось немає, — покрокові команди встановлення для поточної ОС.

**Застереження:** завантажені з GitHub архіви FFmpeg/Pandoc **не** перевіряються за контрольною сумою перед розпакуванням і виконанням (на відміну від самооновлення застосунку, яке вимагає `SHA256SUMS` — [UPDATES.uk.md](UPDATES.uk.md)).

## GPU / CUDA

`setup/gpu_info.py` виявляє наявність відеокарти NVIDIA і зберігає результат (`has_nvidia`, `gpu_model`) у `settings.json` — це визначає, чи пропонується CUDA-індекс `cu121` для встановлення `torch`, і впливає на логіку вибору пристрою в [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md).

## Автозапуск з затримкою (Windows)

- **Кнопка [Автозапуск]** у GUI — запускає `autorun_delayed.bat`, який створює ярлик у каталозі автозавантаження поточного користувача (`%APPDATA%\...\Startup`) із затримкою 25 секунд; прав адміністратора не потрібно.
- **Вручну:** `Win+R` → `shell:startup` → створити ярлик на `start_delayed.vbs` і перемістити його в цю папку; затримку можна змінити, відредагувавши `delaySec = 25` у самому файлі `start_delayed.vbs`.

## Встановлення вручну

`python -m whisperfast.setup.installer` (те саме, що робить `install.bat` і кнопка [Залежності]).

## Куди дивитися далі

- Карта модулів `setup/` у коді — [INTERNAL-ARCHITECTURE.uk.md](INTERNAL-ARCHITECTURE.uk.md)
- Як `has_nvidia`/`gpu_model`/`python_path` потрапляють у `settings.json` — [CONFIGURATION.uk.md](CONFIGURATION.uk.md)
- Перевірка оновлень для тих самих pip-пакетів і зовнішніх утиліт — [UPDATES.uk.md](UPDATES.uk.md)

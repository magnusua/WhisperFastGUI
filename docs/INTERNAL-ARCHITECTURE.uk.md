# Внутрішня архітектура

## Навіщо існує цей документ

Це модуль-за-модулем карта пакета `whisperfast/` — для контриб'ютора, який вже прочитав [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md) і тепер хоче знайти правильний файл для конкретної зміни. Якщо ви шукаєте формат конкретного файлу (`settings.json` тощо) — це [CONFIGURATION.uk.md](CONFIGURATION.uk.md), не цей документ.

## Пакет whisperfast

```
репозиторій magnusua/WhisperFastGUI (продукт: FTW)
├── main.py                 — точка входу: вибір Python, single-instance, установка залежностей, запуск GUI
├── LICENSE / THIRD-PARTY-NOTICES.md — MIT на FTW і ліцензії залежностей
├── README.md               — версія продукту і індекс docs/
├── docs/                   — внутрішня документація (архітектура, конфігурація, оновлення)
├── captures/               — WAV запису зустрічі (створюється в рантаймі, не в git)
└── whisperfast/
    ├── config.py            — BASE_DIR, APP_NAME="FTW", RELEASE_ZIP_PREFIX, версія з README.md
    ├── settings.py           — settings.json: дефолти, читання/запис, DPAPI-обгортка ключів
    ├── utils.py              — час, шляхи черги, тривалість аудіо, звук завершення
    ├── log_store.py          — app_log.json: дні + file-сесії, batch flush
    ├── library.py            — library.sqlite: архів розмов + FTS5; стовпець telegram_to — усі адресати Telegram
    ├── srt_parse.py / audio_player.py — клік по рядку субтитрів
    ├── secrets_store.py      — ключі API: DPAPI (Windows), login Keychain (macOS), chmod 0600 (Linux)
    ├── platform_util.py      — subprocess без вікна консолі на Windows
    ├── open_path.py          — відкрити файл / показати в провіднику
    ├── single_instance.py    — PID-лок, діалог «ПЗ вже запущено»
    ├── core/                 — пайплайн обробки (відокремлений від Tkinter, див. нижче)
    ├── ui/                   — Tkinter GUI
    ├── postprocess/          — AI-постпроцесинг (Cursor/Gemini/Claude/Copilot/Ollama/OpenAI-compat)
    ├── setup/                — перший запуск, встановлення залежностей
    ├── updates/               — самооновлення застосунку та моделі
    ├── telegram/              — прийом медіа з Telegram і відповідь у той самий чат
    └── i18n/                  — переклади інтерфейсу EN/UK/RU (`app_title` = FTW)
```

## Головна ідея розбиття

Шари відповідають ідеї «GUI викликає core, core не повинен знати про GUI» — і тепер це виконано послідовно. `postprocess/`, `setup/`, `updates/`, `i18n/` UI-агностичні (працюють через параметр `log_func`, не через прямий доступ до Tkinter-віджетів). `core/` теж більше не імпортує `tkinter` напряму: діалоги вибору файлу/каталогу (раніше в `input_files.py`) і діалог слідкування за каталогами (раніше в `queue_manager.py`) перенесені в `ui/dialogs.py`; питання «зберегти MP3?» (раніше пряме `messagebox.askyesno` в `transcription.py`) тепер іде через duck-typed callback `app.ask_save_mp3_confirm(filename)`, реалізований у `ui/gui.py` — за тим самим зразком, що й уже наявний `ask_overwrite` у `core/output_conflict.py: resolve_output_paths()`.

`ui/gui.py` (клас `WhisperGUI`, заголовок вікна — FTW) — це не просто «шар UI», а фактичний оркестратор усього застосунку: він тримає стан налаштувань, викликає `core/`, керує треєм і оновленнями. Модулі `core/transcription.py` і `core/queue_manager.py` приймають цей об'єкт як duck-typed параметр `app` і викликають на ньому ~20 різних методів/атрибутів — тому жоден з них сьогодні не запускається без повністю сконструйованого `WhisperGUI`.

## Огляд модулів

### main.py
Точка входу. Послідовність при старті: вибір/перевірка Python-інтерпретатора (`setup/python_selector.py`) → перевірка блокування другого екземпляра (`single_instance.py`) → за потреби встановлення залежностей (`setup/installer.py`) → важкі імпорти (torch, faster-whisper) відкладені до цього моменту → запуск `ui/gui.py`. Windows AppUserModelID — `FTW.2026`. Вихід (`on_app_closing`) відмовляє, якщо триває запис зустрічі.

### config.py
Єдине джерело констант: `APP_NAME = "FTW"`, `RELEASE_ZIP_PREFIX`, `LEGACY_RELEASE_ZIP_PREFIXES` (апдейтер приймає і `FTW-*.zip`, і історичні `WhisperFastGUI-*.zip`), `GITHUB_REPO = "magnusua/WhisperFastGUI"`, `BASE_DIR`/`RESOURCES_DIR`, списки розширень, моделей Whisper, `SUPPORTED_LANGUAGES`, шляхи кешу Hugging Face Hub. `APP_VERSION`/`APP_DATE` читаються з `README.md` при імпорті (`load_app_metadata`).

### settings.py
Єдине джерело дефолтів і (де)серіалізації `settings.json`. Повний перелік ключів — [CONFIGURATION.uk.md](CONFIGURATION.uk.md#settingsjson).

### utils.py
Чисті функції без залежності від Tkinter: парсинг/форматування таймкодів (`parse_timestamp_to_seconds`, `format_timestamp`), нормалізація шляхів черги (`normalize_queue_path`, `make_queue_item`), тривалість аудіофайлу (через ffprobe/pydub), відтворення звуку завершення. Хороша відправна точка для перших unit-тестів проєкту.

### log_store.py
`LogStore` — потокобезпечне (через `threading.Lock`) сховище логу, що персистить у `app_log.json` пакетним flush. Формат записів — [CONFIGURATION.uk.md](CONFIGURATION.uk.md#applogjson).

### platform_util.py / open_path.py / single_instance.py
Дрібні кросплатформні helper'и: запуск subprocess без вікна консолі на Windows (`win_no_window_kwargs`), відкриття файлу/каталогу в провіднику ОС, PID-лок з діалогом при повторному запуску.

### core/ — пайплайн обробки черги

| Файл | Відповідає за |
|---|---|
| `host.py` | Protocol `TranscriptionHost` — методи/атрибути, які `run_queue` / `save_files` очікують від GUI. `WhisperGUI` реалізує його структурно (без наслідування); тести використовують фейк без Tkinter. |
| `model_manager.py` | Singleton `WhisperModelSingleton` — завантаження/вивантаження моделі. Деталі — [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md). |
| `transcription.py` | Головний цикл `run_queue`: Whisper, TXT/SRT/(опційно JSON/VTT/MP3), діаризація стерео, AI-хук. |
| `export_transcript.py` | JSON-сегменти і WebVTT. |
| `diarize.py` | Мітки SPEAKER_00/01 з енергії стереоканалів; опційний pyannote. |
| `capture.py` | Запис mic + WASAPI loopback → стерео PCM WAV **на диск по ходу** (не в RAM). `pause`/`resume`, `repair_wav_header` / `recover_captures` для аварійно обірваних файлів. |
| `document_convert.py` | PDF/DOC/DOCX/текст → Markdown через `markitdown`; допоміжні перевірки `is_document_file`, `needs_office_to_md`. |
| `pandoc_export.py` | Markdown → DOCX через Pandoc (опційно, коли увімкнено «MD → Word»). |
| `output_conflict.py` | Вирішення конфлікту імен вихідних файлів: перезапис, суфікс `_HHMM`, або пропуск. `resolve_output_paths()` — UI-агностична, `ask_overwrite_via_tk()` — Tkinter-обгортка навколо неї. |
| `queue_manager.py` | `QueueController` (персист `request_queue.json`, додавання/видалення/перетягування рядків) + `DirectoryWatcher` (слідкування за каталогами). Діалог вибору каталогів слідкування (`open_watch_dirs_dialog`) перенесено в `ui/dialogs.py`. |
| `input_files.py` | Чиста логіка валідації і додавання файлів/каталогів у чергу (без Tkinter) — придатна до тестування без GUI. Самі діалоги вибору файлу/каталогу (`add_single_file`, `add_multiple_files`, `add_directory`) — в `ui/dialogs.py`. |
| `source_relocate.py` | Перенесення вихідного файлу поруч із результатами обробки після успішного завершення. |

### ui/ — Tkinter GUI

| Файл | Відповідає за |
|---|---|
| `gui.py` | Клас `WhisperGUI` — головне вікно / оркестратор. |
| `archive.py` | Вікно архіву: пошук, плеєр по SRT, Q&A, каскадне видалення. |
| `capture_ui.py` | Record / Pause / Stop, згода, Ctrl+Shift+R і Ctrl+Shift+P, відновлення WAV при старті, `capture_blocks_shutdown()`. |
| `dialogs.py` | Модальні діалоги (модель, збереження, ключі API, промпти, правила…). |
| `log_panel.py` | Рендеринг логу в Tkinter `Text` з розгортанням по днях/файлах, клікабельні шляхи. |
| `ai_jobs.py` | Черга завдань AI-постпроцесингу (окремо від черги транскрибації — один файл може мати кілька AI-завдань). |
| `tray.py` | Іконка `ftw` і меню (динамічні Record/Pause). |
| `widgets.py` | Дрібні перевикористовувані віджети (Tooltip, константи масштабу UI). |

### postprocess/ — AI-постпроцесинг

Коротко: `ai_postprocess.py` — оркестратор; `prompt_library.py` — `promts/*.json`; `common.py` — HTTP/clipboard; `cursor_postprocess.py` — Cursor SDK; `providers/` — Claude, Gemini, Copilot, Ollama, OpenAI-compatible + `base.py`; `prompt_rules.py` — автозапуск; `usage.py` — токени/бюджет; `connection_test.py` — кнопка «Перевірити з'єднання».

### setup/ — перший запуск і залежності

| Файл | Відповідає за |
|---|---|
| `python_selector.py` | Пошук установлених інтерпретаторів Python при першому запуску, вибір і збереження `python_path`/`python_version`, re-exec під обраним інтерпретатором. |
| `installer.py` | Встановлення/оновлення pip-пакетів (torch з потрібним CUDA-індексом, faster-whisper, ctranslate2, pydub, pystray, cursor-sdk, markitdown тощо); прибирання «зламаних» залишків перерваного pip. Перевірка оновлень бере лише реліз, новіший за встановлений і сумісний з цим Python. |
| `external_tools.py` | FFmpeg/Pandoc: перевірка наявності, встановлення через winget/Chocolatey/Homebrew, або запасний варіант — завантаження релізу з GitHub у каталог `tools/`. |
| `gpu_info.py` | Виявлення відеокарти NVIDIA і моделі GPU для `settings.json`. `poke_nvidia_gpu()` утримує дискретну карту увімкненою без монітора (D3D11 + `cuInit`) і питає ім’я через `nvidia-smi`. |

Детальніше — [SETUP-AND-DEPENDENCIES.uk.md](SETUP-AND-DEPENDENCIES.uk.md).

### updates/ — самооновлення

`app_updates.py` (застосунок з **GitHub Release** + обов’язковий SHA-256; GPG не вмикається без ключа в `resources/`), `model_updates.py` (ваги Whisper з Hugging Face Hub), `release_notes.py` (текст «що нового» з `resources/release_notes.json`), `checksums.py` (парсинг `SHA256SUMS`). Детальніше — [UPDATES.uk.md](UPDATES.uk.md).

### telegram/ — прийом і відповідь

| Файл | Відповідає за |
|---|---|
| `service.py` | Запуск і зупинка слухача всередині відкритого вікна. `listener_kind` каже, чи вистачає даних для акаунта (api_id, api_hash, телефон і файл сесії) або бота (токен). |
| `account.py` | Telethon: особисті чати і групи. Власні повідомлення пропускаються, крім Обраного і чатів з `telegram_self_chat_names` (ім’я, повне ім’я, `@username`). Група з тією назвою віддає всі аудіо та відео. Старт пише рядок у лог і надсилає його в Обране. Прийнятий файл логується до завантаження. |
| `worker.py` | Режим бота і локальний `telegram-bot-api`. |
| `gui_bridge.py` | Кладє файл у чергу і повертає результати: TXT, кожен AI, MP3 з відео, відрізок аудіо (`*_ГГ-ХХ-СС_ГГ-ХХ-СС_audio.mp3`). MP3 зі звукового джерела не надсилається. |
| `origin.py` | `.ftw_tg_origin.json`: chat id і message id за шляхом. `retarget` викликається з `source_relocate`, коли джерело переїжджає. |
| `seen.py` | `.ftw_tg_seen.json`: id файлу Telegram → локальний шлях, готові TXT/AI і чати, які переслали той самий файл, поки він ще оброблявся. |
| `links.py` | Посилання YouTube, Instagram і Facebook: скачування через yt-dlp. У чергу Whisper — лише якщо `telegram_social_to_queue`. |
| `learn.py` | Режим навчання: питання перед обробкою чату, якого немає в автоматичному списку. |
| `recent.py` | `.ftw_tg_recent.json`: до 10 останніх груп і контактів, куди надсилали файли. |

### i18n/

`__init__.py` — публічний API (`t`, `set_language`, `get_language`) з fallback-імпортом; `lang_manager.py` — завантаження та кешування `lang.json`; `fallback.py` — мінімальний резерв, якщо `lang.json` недоступний. Список підтримуваних мов (`EN`/`UK`/`RU`) визначено в `config.SUPPORTED_LANGUAGES`; `lang_manager.py` і `updates/release_notes.py` тепер імпортують цю константу замість того, щоб дублювати той самий кортеж вручну.

## Потоки виконання

**Транскрибація одного медіафайлу з черги** (`core/transcription.py: run_queue`):
```
для кожного файлу в черзі (у діапазоні [Початок–Кінець]):
    WhisperModelSingleton.get(...)               # модель завантажується один раз для всієї черги
    model.transcribe(path, ...)                   # генератор сегментів
    для кожного сегмента:
        оновити прогрес/лог (через app.root.after)
    записати .txt, .srt (опційно .json / .vtt; мітки спікерів зі стерео)
    якщо save_audio_mp3: витягнути й зберегти _audio.mp3 (pydub)
    якщо send_txt_to_ai: передати .txt у обраний AI-провайдер
    якщо export_md_to_docx і є .md: pandoc_export → .docx
    source_relocate: перенести вихідний файл поруч із результатами
    log_store.end_file(...)
```

**Обробка документа** (`.pdf`/`.doc`/`.docx`/`.md`/текст): та сама черга і той самий `run_queue`, але замість Whisper — `document_convert.py` (markitdown → `.md`), Whisper не викликається.

**Перший запуск:** `main.py` → `python_selector` (вибір інтерпретатора, якщо їх кілька) → `single_instance` (перевірка PID-лока) → `installer` (встановлення відсутніх pip-пакетів, без компонентів NVIDIA) → `ui.gui.WhisperGUI`.

## Типові місця для змін

| Задача | Що міняти |
|---|---|
| Додати нове налаштування | `settings.py: _DEFAULTS`, зчитування/запис у `gui.py` (`__init__` і `_persist_settings`), за потреби — елемент у `dialogs.py` |
| Додати новий AI-провайдер | Новий файл у `postprocess/providers/`, що реалізує протокол `base.py: AIProvider`; підключити в `ai_postprocess.py` і в список вибору в `dialogs.py` |
| Змінити список моделей Whisper | `config.py: WHISPER_MODELS` / `DEFAULT_MODEL` |
| Додати новий формат вхідних файлів | `config.py`: один із `AUDIO_EXTENSIONS`/`VIDEO_EXTENSIONS`/`TEXT_EXTENSIONS`/`OFFICE_TO_MD_EXTENSIONS` |
| Змінити логіку конфлікту імен файлів | `core/output_conflict.py` |
| Змінити поведінку слідкування за каталогом | `core/queue_manager.py: DirectoryWatcher` (константи `WATCH_*` на початку файлу) |
| Додати переклад / новий рядок інтерфейсу | `whisperfast/i18n/lang.json` (усі три мови одразу) |
| Змінити запис зустрічі / crash-recovery WAV | `core/capture.py`, `ui/capture_ui.py` |
| Змінити механізм самооновлення | `updates/app_updates.py` (`_pick_release_assets` — префікс `FTW`, потім `WhisperFastGUI`) |
| Змінити, що Telegram приймає або відправляє назад | `telegram/account.py`, `telegram/gui_bridge.py`, `telegram/origin.py` |

## Куди дивитися далі

- Концептуальна картина, потік даних — [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md)
- Формати файлів і змінні середовища — [CONFIGURATION.uk.md](CONFIGURATION.uk.md)

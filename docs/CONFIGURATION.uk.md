# Конфігурація Whisper Fast GUI

## Навіщо існує цей документ

Це нормативний опис усіх файлів стану/конфігурації, які застосунок читає і пише поруч із `main.py`, та всіх змінних середовища, які він розуміє. Якщо потрібна загальна картина того, як ці файли пов'язані з потоком обробки — спочатку прочитайте [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md). Якщо цікавить саме механіка AI-провайдерів (`ai_provider`, ключі API) — після цього документа перейдіть у [POSTPROCESSING-PROVIDERS.uk.md](POSTPROCESSING-PROVIDERS.uk.md).

## Загальна структура файлів стану

Усі перелічені нижче файли лежать в одному каталозі — `BASE_DIR` (каталог, де знаходиться `main.py`; обчислюється в `whisperfast/config.py` як `os.path.dirname` каталогу пакета `whisperfast/`). Жоден з них не версіонується в git (усі, крім `README.md` і `redactor1.md`, — у `.gitignore`).

| Файл | Призначення | Джерело правди (модуль) |
|---|---|---|
| `settings.json` | Налаштування користувача (мова, пристрій, модель, шляхи збереження, ключі API…) | `whisperfast/settings.py` |
| `request_queue.json` | Збережена черга файлів (шлях + діапазон часу + прапорець «оброблено») | `whisperfast/core/queue_manager.py` |
| `app_log.json` | Лог програми по днях (у `.gitignore`) | `whisperfast/log_store.py` |
| `redactor1.md` | Бібліотека AI-промптів, редагується користувачем із GUI | `whisperfast/postprocess/cursor_postprocess.py` (парсинг) |
| `whisperfast/i18n/lang.json` | Тексти інтерфейсу EN/UK/RU | `whisperfast/i18n/lang_manager.py` |
| `README.md` | **Єдине джерело версії застосунку** (`**Версія:**` / `**Дата публікації:**`) | `whisperfast/config.py: parse_app_metadata` |

Обробка помилок скрізь однакова: якщо файл відсутній або пошкоджений (`json.JSONDecodeError`), модуль тихо повертається до значень за замовчуванням замість падіння — це свідомий вибір заради стійкості десктопного застосунку, хоча й означає, що биті/вручну відредаговані файли не завжди помітні користувачу.

## settings.json

Єдине джерело дефолтів — `whisperfast/settings.py: _DEFAULTS`. При першому запуску файл створюється з цими значеннями; при кожному наступному запуску відсутні ключі домописуються (`load_app_settings`), тож додавання нового налаштування в код автоматично «доливає» його в уже існуючі `settings.json` користувачів.

| Ключ | Значення за замовчуванням | Опис |
|---|---|---|
| `language` | `"EN"` | Мова інтерфейсу: `EN` / `UK` / `RU`. |
| `output_dir` | `""` | Каталог збереження результатів (порожньо = поруч із вихідним файлом). |
| `output_mode` | `"beside"` | Режим збереження: `beside` (поруч), `custom` (вибраний каталог) або підкаталог за шаблоном. |
| `output_named_folder` | `"{basename}"` | Шаблон підкаталогу збереження (`{basename}` → ім'я вихідного файлу без розширення). |
| `mp3_output_mode` | `"inherit"` | Куди зберігати витягнуте MP3: успадкувати від `output_mode`, поруч із відео, або окремий каталог. |
| `mp3_output_dir` | `""` | Окремий каталог для MP3, якщо `mp3_output_mode` це передбачає. |
| `watch_dir` | `""` | Каталоги слідкування, рядок через кому (`core/queue_manager.py: parse_watch_dirs` / `serialize_watch_dirs`). |
| `watch_enabled` | `false` | Чи увімкнено слідкування за каталогом. |
| `device_mode` | `"AUTO"` | `AUTO` / `GPU` / `CPU` — див. [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md). |
| `play_sound_on_finish` | `false` | Звук по завершенні всієї черги. |
| `save_audio_mp3` | `false` | Зберігати витягнуте аудіо як MP3. |
| `tray_mode` | `"panel"` | `panel` / `tray` / `panel+tray`. |
| `whisper_model` | `DEFAULT_MODEL` (`"large-v3-turbo"`) | Обрана модель Whisper зі списку `config.WHISPER_MODELS`. |
| `has_nvidia` | `false` | Кеш результату виявлення GPU NVIDIA (`setup/gpu_info.py`). |
| `gpu_model` | `""` | Назва відеокарти (для показу і для логіки cu121-індексу). |
| `send_txt_to_ai` | `false` | Прапорець «В AI». |
| `send_txt_to_cursor` | `false` | **Legacy-псевдонім** `send_txt_to_ai` — синхронізується автоматично при завантаженні й збереженні (див. нижче). |
| `export_md_to_docx` | `false` | Прапорець «MD → Word». |
| `ai_provider` | `"cursor"` | Обраний провайдер: `cursor` / `gemini` / `claude` / `copilot`. |
| `cursor_api_key` | `""` | Ключ Cursor SDK (пріоритет має env `CURSOR_API_KEY`). |
| `gemini_api_key` | `""` | Ключ Gemini (пріоритет має env `GEMINI_API_KEY` / `GOOGLE_API_KEY`). |
| `gemini_model` | `"gemini-2.0-flash"` | Модель Gemini. |
| `anthropic_api_key` | `""` | Ключ Claude/Anthropic (пріоритет має env `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY`). |
| `claude_model` | `"claude-sonnet-4-5"` | Модель Claude. |
| `azure_openai_endpoint` | `""` | Endpoint Azure OpenAI (Copilot). |
| `azure_openai_api_key` | `""` | Ключ Azure OpenAI (пріоритет має env `AZURE_OPENAI_API_KEY` / `OPENAI_API_KEY`). |
| `azure_openai_deployment` | `""` | Назва deployment в Azure OpenAI. |
| `azure_openai_api_version` | `"2024-08-01-preview"` | Версія Azure OpenAI API. |
| `python_path` | `""` | Шлях до обраного інтерпретатора Python (перший запуск, `setup/python_selector.py`). |
| `python_version` | `""` | Версія обраного інтерпретатора (для показу і для `install.bat`). |
| `skip_app_update_version` | `""` | Версія на GitHub, яку користувач попросив не пропонувати повторно. |

**⚠️ Ключі API зберігаються тут у відкритому вигляді** (простий `json.dump`, без шифрування чи інтеграції з keychain/Credential Manager ОС). Після кожного запису файлу тепер викликається обмеження прав доступу (`chmod 0600`), але це працює лише на POSIX (Linux/macOS) — на Windows, для якого це застосунок насамперед і призначений, `os.chmod` реальний ACL файлу не змінює. Якщо ключ заданий і через змінну середовища, і в `settings.json` — виграє змінна середовища (див. розділ «Змінні середовища» нижче та `POSTPROCESSING-PROVIDERS.uk.md`). Повноцінне рішення (`keyring`/Windows Credential Manager) поки не реалізоване.

**Legacy-псевдонім `send_txt_to_cursor`.** Історично прапорець «В AI» називався «В Cursor». При завантаженні `settings.json`, якщо є старий `send_txt_to_cursor`, але немає `send_txt_to_ai` — значення копіюється (`load_app_settings`), після чого обидва ключі завжди тримаються синхронізованими. Це працює, але означає, що будь-яка майбутня зміна цього прапорця в коді має враховувати обидва ключі одразу.

Читання: `whisperfast.settings.load_app_settings()`. Запис усіх ключів одразу: `save_app_settings(dict)` (мердж, інші ключі не чіпає). Запис лише мови: `save_settings(language)`.

## request_queue.json

Список об'єктів (`core/queue_manager.py: save_to_file` / `load_from_file`), кожен — один рядок таблиці черги:

```json
[
  {
    "path": "C:\\Records\\interview.mp3",
    "start": "00:00:00,000",
    "end_segment_1": "",
    "end_segment_2": "",
    "end": "00:42:10,500",
    "processed": false
  }
]
```

Ключі відповідають `config.QUEUE_ITEM_KEYS = ("path", "start", "end_segment_1", "end_segment_2", "end", "processed")`. При завантаженні файли, яких уже немає на диску, мовчки пропускаються (`os.path.isfile` перевірка); `end`, якщо не вказано, обчислюється заново з тривалості файлу. Файл перезаписується повністю при кожній зміні черги (додавання, видалення, перетягування, редагування діапазону).

## app_log.json

Формат керується `whisperfast/log_store.py`. Верхній рівень:

```json
{
  "version": 2,
  "days": {
    "2026-08-14": { "entries": [ /* ... */ ] }
  }
}
```

Кожен елемент `entries` — це або `kind: "line"` (звичайний рядок логу: `id`, `ts`, `text`, `tag`), або `kind: "file"` (одна file-сесія обробки одного файлу з черги):

```json
{
  "kind": "file",
  "id": "…",
  "ts": "2026-08-14T10:03:00",
  "ts_end": "2026-08-14T10:05:12",
  "source": "C:\\Records\\interview.mp3",
  "name": "interview.mp3",
  "status": "done",
  "index": { "current": 1, "total": 3 },
  "events": [ { "ts": "…", "text": "…", "tag": null } ],
  "segments": { "count": 214, "last": [ { "t": "00:03:40,120", "text": "…" } ] },
  "outputs": [ { "role": "txt", "path": "…" }, { "role": "srt", "path": "…" } ],
  "error": null
}
```

Обмеження, які застосовуються автоматично при записі: `MAX_DAYS = 60` (старіші дні видаляються), `MAX_ENTRIES_PER_DAY = 2000`, `MAX_SEGMENT_PREVIEWS = 3` (зберігаються лише останні прев'ю сегментів, не весь текст), `MAX_FILE_EVENTS = 50` на файл-сесію. Запис на диск — пакетний (`FLUSH_DELAY_S = 1.0`, через `threading.Timer` або UI-скедулер), не на кожен виклик, з атомарним записом через `.tmp` + `os.replace`.

**Застереження щодо приватності:** прев'ю сегментів транскрипції та повні шляхи файлів (можуть містити ім'я користувача Windows у шляху `C:\Users\<name>\...`) зберігаються в цьому файлі без шифрування. Якщо застосунок використовується для конфіденційних записів — врахуйте це перед тим, як прикладати `app_log.json` до звіту про помилку.

## Змінні середовища

| Змінна | Використовується в | Пріоритет над `settings.json`? |
|---|---|---|
| `CURSOR_API_KEY` | `postprocess/cursor_postprocess.py` | Так |
| `GEMINI_API_KEY`, `GOOGLE_API_KEY` | `postprocess/providers/gemini.py` | Так (обидві перевіряються, перша знайдена виграє) |
| `GEMINI_MODEL` | `postprocess/providers/gemini.py` | Так |
| `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY` | `postprocess/providers/claude.py` | Так |
| `CLAUDE_MODEL` | `postprocess/providers/claude.py` | Так |
| `AZURE_OPENAI_API_KEY`, `OPENAI_API_KEY` | `postprocess/providers/copilot.py` | Так |
| `AZURE_OPENAI_ENDPOINT` | `postprocess/providers/copilot.py` | Так |
| `AZURE_OPENAI_DEPLOYMENT` | `postprocess/providers/copilot.py` | Так |
| `AZURE_OPENAI_API_VERSION` | `postprocess/providers/copilot.py` | Так |
| `DEBUG` | `core/transcription.py` | — (якщо `=1`, у лог виводиться повний traceback при помилці обробки) |
| `WHISPER_PYTHON_REEXEC` | `setup/python_selector.py` | — (внутрішній прапорець при повторному запуску під іншим інтерпретатором) |
| `HF_HUB_CACHE`, `HF_HOME` | `config.get_whisper_cache_dir()` | — (визначає, де Hugging Face Hub кешує ваги моделей) |

Правило пріоритету для ключів API у всіх чотирьох провайдерів однакове: спочатку перевіряється змінна середовища, і лише якщо вона порожня — значення з `settings.json`. Це дозволяє тримати ключ поза `settings.json` узагалі (наприклад, через системне середовище користувача), не втрачаючи можливості налаштувати його через GUI для тих, кому це зручніше.

## Куди дивитися далі

- Як саме `ai_provider` і ключі з цієї таблиці використовуються під час запиту до Cursor/Gemini/Claude/Copilot — [POSTPROCESSING-PROVIDERS.uk.md](POSTPROCESSING-PROVIDERS.uk.md)
- Як `whisper_model` і `device_mode` перетворюються на реальний вибір пристрою/точності — [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md)
- Загальна картина потоку даних — [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md)

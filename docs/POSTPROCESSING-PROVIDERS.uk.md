# AI-постпроцесинг: провайдери

## Навіщо існує цей документ

Whisper Fast GUI вміє передавати вже готовий текст (`.txt` після транскрибації або `.md` після конвертації документа) у одного з чотирьох AI-провайдерів для додаткової обробки за промптами користувача. Цей документ — контракт цієї підсистеми: як влаштовані промпти, як кожен провайдер отримує ключ API і що відбувається за його відсутності. Формати `ai_provider` / ключів у `settings.json` — [CONFIGURATION.uk.md](CONFIGURATION.uk.md).

## Огляд

| Провайдер | З ключем API | Без ключа (fallback) |
|---|---|---|
| **Cursor** | Cursor SDK, ланцюжок промптів послідовно | Cursor Chat + промпт у буфері обміну |
| **Gemini** | Google Generative Language API | Браузер `gemini.google.com` + буфер обміну |
| **Claude** | Anthropic Messages API | Браузер `claude.ai` + буфер обміну |
| **Copilot** | Azure OpenAI (endpoint + ключ + deployment) | Браузер `copilot.microsoft.com` + буфер обміну |

Спільний принцип для всіх чотирьох: якщо ключ/endpoint не налаштовано (ні через змінну середовища, ні через `settings.json`), програма не блокує користувача — вона копіює промпт у буфер обміну і відкриває відповідний сайт у браузері, щоб можна було вставити текст вручну. Робочий (API-driven) режим — це прискорення, а не єдиний спосіб працювати.

## redactor1.md як бібліотека промптів

`redactor1.md` — не документація про застосунок, а **дані**: набір користувацьких промптів, який відкривається і редагується прямо з GUI (кнопка **«В AI»**). Формат — Markdown-секції виду:

```
## Промпт №2 "TW_core"
<system>
...текст системного промпту...
</system>
Вхідні дані: {{TRANSCRIPT_TEXT}}
```

- Ім'я в лапках заголовка (`"TW_core"`) стає суфіксом вихідного файлу: результат обробки промптом №2 збережеться як `<ім'я>_TW_core.md`.
- Плейсхолдери на кшталт `{{TRANSCRIPT_TEXT}}` / `{{INPUT_DATA}}` підставляються перед відправкою в AI.
- Порожні секції (без тексту після заголовка) пропускаються — не показуються у вікні вибору промптів.
- У вікні **«Промти»**, яке з'являється після готовності `.txt`/`.md`: чекбокси вибору промптів (за замовчуванням позначено перший), кнопки **«Виконати»** (або клавіша Пробіл — лише позначені) і **«Всі»** (усі одразу); закриття вікна пропускає AI-обробку, залишаючи в лозі посилання відкрити вибір знову.

## Спільний контракт провайдера

`postprocess/providers/base.py` визначає `Protocol AIProvider` — структурний інтерфейс, якому відповідають `claude.py`, `gemini.py`, `copilot.py` (і, окремим більшим модулем, `cursor_postprocess.py`). Кожен провайдер реалізує по суті один і той самий цикл: якщо є ключ — викликати API-функцію ланцюжком по всіх позначених промптах, записуючи результат кожного кроку у файл і викликаючи `on_file_created`; якщо ключа немає — скопіювати перший промпт у буфер обміну й відкрити браузер. Цей спільний цикл тепер винесено в `base.py` (`run_provider_chain()`, `run_browser_fallback()`) — `claude.py`, `gemini.py`, `copilot.py` викликають ці спільні функції, параметризуючи їх лише функцією виклику конкретного API, замість того, щоб дублювати ~90-рядкову логіку кожен. `cursor_postprocess.py` влаштований інакше (SDK-bridge-процес) і в цю уніфікацію не входив.

## Провайдер Cursor

Реалізований окремо і найбільш повно — `postprocess/cursor_postprocess.py`. Ключ: `resolve_cursor_api_key()` — спершу env `CURSOR_API_KEY`, потім `settings.json: cursor_api_key`. З ключем (Cursor SDK) на кожен промпт піднімається окремий локальний bridge-процес (`Cursor.exe`/`node.exe` на Windows — без вікна консолі, через `CREATE_NO_WINDOW`), з дренажем stderr і повтором при помилці з'єднання до вже «мертвого» bridge (`WinError 10061`). Без ключа — фолбек на Cursor Chat: текст промпту в буфері обміну, відкривається сам застосунок Cursor.

## Провайдер Gemini

`postprocess/providers/gemini.py`. Ключ: env `GEMINI_API_KEY` або `GOOGLE_API_KEY`, інакше `settings.json: gemini_api_key`. Модель: env `GEMINI_MODEL`, інакше `settings.json: gemini_model` (за замовчуванням `gemini-2.0-flash`). З ключем — виклик Google Generative Language API; без ключа — буфер обміну + браузер `gemini.google.com`.

## Провайдер Claude (Anthropic)

`postprocess/providers/claude.py`. Ключ: env `ANTHROPIC_API_KEY` або `CLAUDE_API_KEY`, інакше `settings.json: anthropic_api_key`. Модель: env `CLAUDE_MODEL`, інакше `settings.json: claude_model` (за замовчуванням `claude-sonnet-4-5`). З ключем — Anthropic Messages API; без ключа — буфер обміну + браузер `claude.ai`.

## Провайдер Copilot (Azure OpenAI)

`postprocess/providers/copilot.py`. На відміну від інших трьох, тут потрібні **три** значення одразу: endpoint (env `AZURE_OPENAI_ENDPOINT`, інакше `settings.json: azure_openai_endpoint`), ключ (env `AZURE_OPENAI_API_KEY`/`OPENAI_API_KEY`, інакше `settings.json: azure_openai_api_key`) і deployment (env `AZURE_OPENAI_DEPLOYMENT`, інакше `settings.json: azure_openai_deployment`); версія API — env `AZURE_OPENAI_API_VERSION`, інакше `settings.json` (за замовчуванням `2024-08-01-preview`). Без повного набору цих трьох значень — фолбек на буфер обміну + браузер `copilot.microsoft.com`.

## Ключі API: діалог і безпека

Усі чотири ключі налаштовуються в одному вікні — кнопка **[API keys]**; закриття вікна через × не зберігає зміни (тільки явне «Зберегти»). Жоден із чотирьох модулів провайдерів не пише значення ключа в лог. Водночас самі ключі зберігаються в `settings.json` у відкритому вигляді, якщо введені через цей діалог (права доступу до файлу тепер обмежуються — `chmod 0600` — але лише на POSIX, не на Windows) — див. [CONFIGURATION.uk.md](CONFIGURATION.uk.md#settingsjson).

## Межа цього документа

Тут не описано: як саме побудовано вікно вибору промптів у Tkinter (це `ui/dialogs.py` і `ui/ai_jobs.py` — деталі структури UI, не контракт провайдера) і як обирається модель Whisper для самої транскрибації (це не AI-постпроцесинг — див. [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md)).

## Куди дивитися далі

- Формат ключів і змінних середовища — [CONFIGURATION.uk.md](CONFIGURATION.uk.md)
- Де в загальному потоці обробки викликається AI-постпроцесинг — [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md)
- Карта модулів `postprocess/` у коді — [INTERNAL-ARCHITECTURE.uk.md](INTERNAL-ARCHITECTURE.uk.md)

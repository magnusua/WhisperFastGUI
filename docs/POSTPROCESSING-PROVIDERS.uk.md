# AI-постпроцесинг: провайдери

## Навіщо існує цей документ

FTW вміє передавати вже готовий текст (`.txt` після транскрибації або `.md` після конвертації документа) у обраного AI-провайдера для додаткової обробки за промптами користувача. Цей документ — контракт цієї підсистеми: як влаштовані промпти, як кожен провайдер отримує ключ API і що відбувається за його відсутності. Формати `ai_provider` / ключів у `settings.json` — [CONFIGURATION.uk.md](CONFIGURATION.uk.md).

## Огляд

| Провайдер | З ключем / URL | Без ключа (fallback) |
|---|---|---|
| **Cursor** | Cursor SDK, ланцюжок промптів послідовно | Cursor Chat + промпт у буфері обміну |
| **Gemini** | Google Generative Language API | Браузер `gemini.google.com` + буфер обміну |
| **Claude** | Anthropic Messages API | Браузер `claude.ai` + буфер обміну |
| **Copilot** | Azure OpenAI (endpoint + ключ + deployment) | Браузер `copilot.microsoft.com` + буфер обміну |
| **Ollama** | Локальний `http://127.0.0.1:11434/api/chat` | Немає: потрібен запущений Ollama |
| **OpenAI-compatible** | `…/v1/chat/completions` (LM Studio, Groq, DeepSeek) | Браузер platform.openai.com |

Хмарні провайдери без ключа відкривають браузер. Ollama ключа не потребує. У діалозі ключів є **Test connection** і місячна стеля витрат (`ai_month_budget`) — при перевищенні AI-черга паузиться, транскрибація ні.

Правила автозапуску (`ai_prompt_rules`): після TXT можна прогнати промпти без модалки (`match`: always / watch_dir / filename). Q&A по одній розмові — з вікна **Архів**.

## Каталог promts/ як бібліотека промптів

`promts/` — не документація про застосунок, а **дані**: по одному JSON на промпт (`01-redactor.json`, …). Кнопка **«В AI»** відкриває вікно зі списком; галочка ліворуч — «включити в обробку за замовчуванням»; **Змінити** в рядку відкриває цей JSON. Поле `hint` (EN/UK/RU) показується при наведенні й **не** надсилається в модель.

```json
{
  "num": 2,
  "name": "TW_core",
  "hint": { "EN": "…", "UK": "…", "RU": "…" },
  "body": "<system>…</system>\n{{TRANSCRIPT_TEXT}}"
}
```

- `name` стає суфіксом вихідного файлу: промпт №2 → `<ім'я>_TW_core.md`.
- Плейсхолдери на кшталт `{{TRANSCRIPT_TEXT}}` / `{{INPUT_DATA}}` підставляються перед відправкою в AI.
- Порожній `body` пропускається — промпт не показується у вікні.
- У вікні **«Промпти»** після готовності `.txt`/`.md`: чекбокси (за замовчуванням — `ai_default_prompt_nums`), **«Виконати»** (або Пробіл) і **«Всі»**; закриття пропускає AI, у лозі лишається посилання відкрити вибір знову.

## Спільний контракт провайдера

`postprocess/providers/base.py` визначає `Protocol AIProvider`. Хмарні HTTP-провайдери (`claude.py`, `gemini.py`, `copilot.py`, `openai_compat.py`) і локальний `ollama.py` використовують спільний цикл `run_provider_chain()` / `run_browser_fallback()`. `cursor_postprocess.py` влаштований інакше (SDK-bridge-процес) і в цю уніфікацію не входив.

## Провайдер Cursor

Реалізований окремо і найбільш повно — `postprocess/cursor_postprocess.py`. Ключ: `resolve_cursor_api_key()` — спершу env `CURSOR_API_KEY`, потім `settings.json: cursor_api_key`. З ключем (Cursor SDK) на кожен промпт піднімається окремий локальний bridge-процес (`Cursor.exe`/`node.exe` на Windows — без вікна консолі, через `CREATE_NO_WINDOW`), з дренажем stderr і повтором при помилці з'єднання до вже «мертвого» bridge (`WinError 10061`). Без ключа — фолбек на Cursor Chat: текст промпту в буфері обміну, відкривається сам застосунок Cursor.

## Провайдер Gemini

`postprocess/providers/gemini.py`. Ключ: env `GEMINI_API_KEY` або `GOOGLE_API_KEY`, інакше `settings.json: gemini_api_key`. Модель: env `GEMINI_MODEL`, інакше `settings.json: gemini_model` (за замовчуванням `gemini-2.0-flash`). З ключем — виклик Google Generative Language API; без ключа — буфер обміну + браузер `gemini.google.com`.

## Провайдер Claude (Anthropic)

`postprocess/providers/claude.py`. Ключ: env `ANTHROPIC_API_KEY` або `CLAUDE_API_KEY`, інакше `settings.json: anthropic_api_key`. Модель: env `CLAUDE_MODEL`, інакше `settings.json: claude_model` (за замовчуванням `claude-sonnet-4-5`). З ключем — Anthropic Messages API; без ключа — буфер обміну + браузер `claude.ai`.

## Провайдер Copilot (Azure OpenAI)

`postprocess/providers/copilot.py`. Потрібні **три** значення одразу: endpoint (env `AZURE_OPENAI_ENDPOINT`, інакше `settings.json: azure_openai_endpoint`), ключ (env `AZURE_OPENAI_API_KEY`/`OPENAI_API_KEY`, інакше `settings.json: azure_openai_api_key`) і deployment (env `AZURE_OPENAI_DEPLOYMENT`, інакше `settings.json: azure_openai_deployment`); версія API — env `AZURE_OPENAI_API_VERSION`, інакше `settings.json` (за замовчуванням `2024-08-01-preview`). Без повного набору — фолбек на буфер обміну + браузер `copilot.microsoft.com`.

## Провайдер Ollama

`postprocess/providers/ollama.py`. Базовий URL: env `OLLAMA_HOST` / `OLLAMA_BASE_URL`, інакше `settings.json: ollama_base_url` (типово `http://127.0.0.1:11434`). Модель: env `OLLAMA_MODEL` або `ollama_model`. Немає ключа API; якщо демон не запущений — помилка в лог, без браузерного фолбеку. Транскрипт лишається на цій машині, поки URL — loopback.

## Провайдер OpenAI-compatible

`postprocess/providers/openai_compat.py`. Для LM Studio, Groq, DeepSeek та інших сумісних з `/v1/chat/completions`. URL / ключ / модель: env `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` або відповідні ключі `openai_compatible_*` у `settings.json`. Без URL — фолбек на браузер `platform.openai.com`.

## Ключі API: діалог і безпека

Усі провайдери налаштовуються в одному вікні — кнопка **[API keys]** (включно з Ollama URL і OpenAI-compatible). Закриття через × не зберігає зміни (тільки явне «Зберегти»). Модулі провайдерів не пишуть значення ключа в лог.

На **Windows** ключі в `settings.json` шифруються DPAPI (`whisperfast/secrets_store.py`, префікс `dpapi:`). На POSIX — відкритий текст і `chmod 0600`. Якщо той самий ключ заданий і в середовищі, і в файлі — виграє середовище. Деталі — [CONFIGURATION.uk.md](CONFIGURATION.uk.md#settingsjson).

## Межа цього документа

Тут не описано: як саме побудовано вікно вибору промптів у Tkinter (це `ui/dialogs.py` і `ui/ai_jobs.py` — деталі структури UI, не контракт провайдера) і як обирається модель Whisper для самої транскрибації (це не AI-постпроцесинг — див. [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md)).

## Куди дивитися далі

- Формат ключів і змінних середовища — [CONFIGURATION.uk.md](CONFIGURATION.uk.md)
- Де в загальному потоці обробки викликається AI-постпроцесинг — [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md)
- Карта модулів `postprocess/` у коді — [INTERNAL-ARCHITECTURE.uk.md](INTERNAL-ARCHITECTURE.uk.md)

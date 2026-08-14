# Whisper Fast GUI

**Версія:** 1.2.12
**Дата публікації:** 15.08.2026

Графічний інтерфейс для транскрибації аудіо та відео на основі Faster-Whisper (OpenAI Whisper). Також обробляє текстові/офісні документи (конвертація в Markdown, опційно AI-постпроцесинг і Word).

Репозиторій: https://github.com/magnusua/WhisperFastGUI

---

## Навіщо існує цей застосунок

Пакетна транскрибація аудіо/відео в текст без хмарного сервісу і без командного рядка: черга файлів, один клік [Старт], TXT/SRT на виході, опційно — AI-редагування результату (Cursor/Gemini/Claude/Copilot) і експорт у Word. Той самий потік працює і для PDF/DOC/DOCX/Markdown — без виклику Whisper, лише конвертація й опційний AI-постпроцесинг.

## Що є в цьому repo

- `main.py` + пакет `whisperfast/` (`core`, `ui`, `postprocess`, `setup`, `updates`, `i18n`) — сам застосунок.
- `docs/` — внутрішня документація (архітектура, конфігурація, оновлення).
- `resources/` — іконка, звук завершення, довідка для кінцевого користувача (`Help_EN.md` / `Help_UK.md` / `Help_RU.md`).
- `redactor1.md` — бібліотека AI-промптів, редагується з GUI.
- `install.bat`, `run_whisper.vbs`, `start_delayed.vbs`, `autorun_delayed.bat` — запуск і автозапуск на Windows.
- `settings.json`, `request_queue.json`, `app_log.json` — стан і налаштування користувача (створюються при першому запуску, у git не потрапляють).

Повне дерево файлів із коментарями по кожному модулю — у [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md).

## З чого почати новій людині

1. Загальна картина того, що робить застосунок і як влаштований потік обробки — [ARCHITECTURE.uk.md](docs/ARCHITECTURE.uk.md)
2. Модуль-за-модулем карта коду — [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md)
3. Встановлення, залежності, FFmpeg/Pandoc — [SETUP-AND-DEPENDENCIES.uk.md](docs/SETUP-AND-DEPENDENCIES.uk.md)
4. Формати `settings.json`/`request_queue.json`/`app_log.json`, змінні середовища — [CONFIGURATION.uk.md](docs/CONFIGURATION.uk.md)

Якщо потрібна саме **інструкція користувача** (кнопки, черга, гарячі клавіші, режими трею) — це `resources/Help_UK.md` (або `Help_EN.md`/`Help_RU.md`), а не документи вище: ті описують внутрішній устрій, а не як натискати кнопки.

## Технічні вимоги (коротко)

Python 3.9–3.13 (рекомендовано 3.11/3.12), FFmpeg у PATH, опційно Pandoc для «MD → Word», опційно NVIDIA GPU з CUDA 12.1. Повний список підтримуваних форматів і залежностей — [SETUP-AND-DEPENDENCIES.uk.md](docs/SETUP-AND-DEPENDENCIES.uk.md).

## Документація

| Документ | Що описує |
|---|---|
| [ARCHITECTURE.uk.md](docs/ARCHITECTURE.uk.md) | Концептуальна модель: потік даних, життєвий цикл GUI, «одна задача одночасно» |
| [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md) | Карта модулів пакета `whisperfast/`, потоки виконання, «де що міняти» |
| [CONFIGURATION.uk.md](docs/CONFIGURATION.uk.md) | `settings.json`, `request_queue.json`, `app_log.json`, змінні середовища |
| [POSTPROCESSING-PROVIDERS.uk.md](docs/POSTPROCESSING-PROVIDERS.uk.md) | Cursor / Gemini / Claude / Copilot, `redactor1.md` |
| [MODEL-AND-DEVICE-MANAGEMENT.uk.md](docs/MODEL-AND-DEVICE-MANAGEMENT.uk.md) | Вибір пристрою, singleton моделі Whisper, кеш Hugging Face Hub |
| [SETUP-AND-DEPENDENCIES.uk.md](docs/SETUP-AND-DEPENDENCIES.uk.md) | Встановлення, pip-залежності, FFmpeg/Pandoc, автозапуск |
| [UPDATES.uk.md](docs/UPDATES.uk.md) | Самооновлення застосунку та моделі Whisper |
| [CHANGELOG.md](docs/CHANGELOG.md) | Історія змін по версіях |
| `resources/Help_EN.md` / `Help_UK.md` / `Help_RU.md` | Довідка кінцевого користувача (відкривається кнопкою [Довідка] у GUI) |

## Куди дивитися далі

Немає окремого документа «вище» за цей README — він і є точка входу. Якщо ви вже прочитали всі документи вище і чогось не вистачає — найімовірніше, відповідь у самому коді відповідного модуля (список — [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md#типові-місця-для-змін)).

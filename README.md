# Whisper Fast GUI

**Версія:** 1.2.15
**Дата публікації:** 03.09.2026

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

## Як опублікувати нову версію

Пуш у `main` **недостатньо**. GUI порівнює локальну версію з **останнім GitHub Release** (`/releases/latest`), а не з гілкою `main`. Без опублікованого тега й асета `SHA256SUMS` оновлення не пропонується. Механізм — [UPDATES.uk.md](docs/UPDATES.uk.md).

1. Підніміть версію в цьому файлі: `**Версія:** X.Y.Z` і `**Дата публікації:** DD.MM.YYYY`. Це єдине джерело `APP_VERSION`.
2. Додайте нотатки: `docs/CHANGELOG.md` і `resources/release_notes.json` (EN / UK / RU — вікно «Що нового» в GUI).
3. Закомітьте й запуште `main`.
4. Опублікуйте **GitHub Release** (не draft і не pre-release) з тегом `vX.Y.Z` на цей коміт. Заголовок — `X.Y.Z` (без `v`). Приклад:

```bash
gh release create vX.Y.Z --title "X.Y.Z" --target main --notes "…"
```

5. Дочекайтеся workflow `release-checksums` (подія `release: published`): він збере `WhisperFastGUI-X.Y.Z-src.zip` і `SHA256SUMS` і прикріпить їх до релізу. Без `SHA256SUMS` програма оновлення не запропонує.

ZIP-інсталяції оновлюються з асетів релізу. Клон із `.git` після підтвердження робить `git pull` з `main`, але **діалог** «є нова версія» все одно з’являється лише після кроку 4–5.

## Куди дивитися далі

Немає окремого документа «вище» за цей README — він і є точка входу. Якщо ви вже прочитали всі документи вище і чогось не вистачає — найімовірніше, відповідь у самому коді відповідного модуля (список — [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md#типові-місця-для-змін)).

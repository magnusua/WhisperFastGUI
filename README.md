# FTW

**Версія:** 1.3.1
**Дата публікації:** 17.09.2026

**FTW** (раніше *Whisper Fast GUI*) — графічний інтерфейс для транскрибації аудіо та відео на основі Faster-Whisper (OpenAI Whisper). Також обробляє текстові/офісні документи (конвертація в Markdown, опційно AI-постпроцесинг і Word).

Репозиторій: https://github.com/magnusua/WhisperFastGUI

---

## Повністю безкоштовне

FTW **повністю безкоштовний**: немає акаунта, підписки, прихованих лімітів і платних функцій у самій програмі. Локальна транскрибація працює офлайн.

Програма поширюється як вільне ПЗ за ліцензією **MIT** — [LICENSE](LICENSE).

Сторонні бібліотеки й інструменти мають власні ліцензії; повний список зі посиланнями — [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Коротко за компонентами:

| Компонент | Навіщо FTW | Ліцензія | Посилання |
|---|---|---|---|
| faster-whisper / CTranslate2 | Локальний ASR | MIT | [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| OpenAI Whisper | Модель розпізнавання | MIT | [whisper](https://github.com/openai/whisper) |
| PyTorch / NumPy | Інференс і аудіо-масиви | BSD | [PyTorch](https://github.com/pytorch/pytorch), [NumPy](https://numpy.org/doc/stable/license.html) |
| Python / Tcl/Tk | Runtime і GUI | PSF / Tcl/Tk | [Python](https://docs.python.org/3/license.html), [Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html) |
| sounddevice / PortAudio | Мікрофон + WASAPI loopback | MIT | [sounddevice](https://github.com/spatialaudio/python-sounddevice) |
| pydub | Нарізка аудіо, стерео-спікери | MIT | [pydub](https://github.com/jiaaro/pydub) |
| pygame / pystray | Звук завершення, трей | LGPL | [pygame](https://www.pygame.org/wiki/about), [pystray](https://github.com/moses-palmer/pystray) |
| Pillow / tkinterdnd2 / markitdown | Іконка, DnD, PDF/Office → MD | MIT / HPND | див. NOTICE |
| FFmpeg (окремо) | Декод аудіо/відео | LGPL або GPL | [ffmpeg.org/legal](https://ffmpeg.org/legal.html) |
| Pandoc (опційно) | MD → Word | GPL-2.0+ | [pandoc](https://github.com/jgm/pandoc) |

Хмарні AI (Gemini, Claude, Copilot, Cursor) — **за бажанням користувача** і за тарифами тих сервісів; це не частина ціни FTW.

---

## Навіщо існує цей застосунок

Пакетна транскрибація аудіо/відео в текст без хмарного сервісу і без командного рядка: черга файлів, один клік [Старт], TXT/SRT на виході, опційно — AI-редагування результату (Cursor/Gemini/Claude/Copilot/Ollama) і експорт у Word. Той самий потік працює і для PDF/DOC/DOCX/Markdown — без виклику Whisper, лише конвертація й опційний AI-постпроцесинг. Можна записати зустріч (мікрофон + системний звук) прямо в чергу.

## Що є в цьому repo

- `main.py` + пакет `whisperfast/` (`core`, `ui`, `postprocess`, `setup`, `updates`, `i18n`) — сам застосунок.
- `docs/` — внутрішня документація (архітектура, конфігурація, оновлення).
- `resources/` — іконка, звук завершення, довідка для кінцевого користувача (`Help_EN.md` / `Help_UK.md` / `Help_RU.md`).
- `promts/` — бібліотека AI-промптів (окремий JSON на кожен промпт; поле `hint` лише для підказки в GUI).
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
| [ARCHITECTURE.uk.md](docs/ARCHITECTURE.uk.md) | Концептуальна модель: черга, запис зустрічі, архів, «одна задача одночасно» |
| [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md) | Карта модулів пакета `whisperfast/`, потоки виконання, «де що міняти» |
| [CONFIGURATION.uk.md](docs/CONFIGURATION.uk.md) | `settings.json`, `request_queue.json`, `app_log.json`, змінні середовища |
| [POSTPROCESSING-PROVIDERS.uk.md](docs/POSTPROCESSING-PROVIDERS.uk.md) | Cursor / Gemini / Claude / Copilot / Ollama, каталог `promts/` |
| [MODEL-AND-DEVICE-MANAGEMENT.uk.md](docs/MODEL-AND-DEVICE-MANAGEMENT.uk.md) | Вибір пристрою, singleton моделі Whisper, кеш Hugging Face Hub |
| [SETUP-AND-DEPENDENCIES.uk.md](docs/SETUP-AND-DEPENDENCIES.uk.md) | Встановлення, pip-залежності, FFmpeg/Pandoc, автозапуск |
| [UPDATES.uk.md](docs/UPDATES.uk.md) | Самооновлення застосунку та моделі Whisper |
| [CHANGELOG.md](docs/CHANGELOG.md) | Історія змін по версіях (старі записи — під колишньою назвою Whisper Fast GUI) |
| [LICENSE](LICENSE) | Ліцензія вихідного коду FTW (MIT) |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) | Ліцензії залежностей |
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

5. Дочекайтеся workflow `release-checksums` (подія `release: published`): він збере `FTW-X.Y.Z-src.zip` і `SHA256SUMS` і прикріпить їх до релізу. Без `SHA256SUMS` програма оновлення не запропонує. Апдейтер також приймає історичні асети `WhisperFastGUI-*-src.zip`.

ZIP-інсталяції оновлюються з асетів релізу. Клон із `.git` після підтвердження робить `git pull` з `main`, але **діалог** «є нова версія» все одно з’являється лише після кроку 4–5.

## Куди дивитися далі

Немає окремого документа «вище» за цей README — він і є точка входу. Якщо ви вже прочитали всі документи вище і чогось не вистачає — найімовірніше, відповідь у самому коді відповідного модуля (список — [INTERNAL-ARCHITECTURE.uk.md](docs/INTERNAL-ARCHITECTURE.uk.md#типові-місця-для-змін)).

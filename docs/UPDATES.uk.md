# Оновлення застосунку

## Навіщо існує цей документ

Whisper Fast GUI сам себе оновлює з GitHub, а окремо — оновлює ваги моделі Whisper з Hugging Face Hub. Це два різні механізми з різними ризиками; цей документ описує обидва. Встановлення pip-залежностей і зовнішніх утиліт при першому запуску — інша тема, див. [SETUP-AND-DEPENDENCIES.uk.md](SETUP-AND-DEPENDENCIES.uk.md).

## Перевірка версії

Локальна версія як і раніше читається з `README.md` (`**Версія:** X.Y.Z` / `**Дата публікації:** DD.MM.YYYY` → `parse_app_metadata()` у `whisperfast/config.py`). Якщо рядок не парситься, версія стає `"unknown"` і оновлення **не пропонується**.

Кнопка **[Оновлення]** і перевірка при старті звертаються до `check_app_update()`: версія й дата беруться з **GitHub Releases** (`GET /repos/{repo}/releases/latest`, `tag_name` без провідного `v`, `published_at` як `DD.MM.YYYY`), а не з `README.md` на гілці `main`. Порівняння — `packaging.version.Version` (`_version_is_newer`). Оновлення пропонується лише якщо remote новіший **і** у релізі є асет `SHA256SUMS` / `SHA256SUMS.txt`. Немає релізу або немає контрольної суми — діалог не показується (fail closed; ZIP з мутабельної гілки `main` більше не використовується).

## Оновлення застосунку

`updates/app_updates.py: apply_app_update()` обирає один із двох шляхів:

- **Якщо в каталозі є `.git`** (`is_git_repo()`) — `git fetch` + `git pull --ff-only` з гілки `main`. Шлях розробника: історія комітів tamper-evident (без перевірки підписів git). Оновлення застосовується одразу, без ZIP.
- **Інакше (інсталяція з ZIP)** — завантажується асет `WhisperFastGUI-*.zip` **останнього GitHub Release** (іммутабельний тег), разом із `SHA256SUMS`. Перед розпакуванням обов’язково звіряється SHA-256 файлу з рядком у `SHA256SUMS` (GNU або BSD формат). Невідповідність або відсутність суми — оновлення зупиняється, архів не розпаковується. Якщо в `resources/release_signing_key.asc` є непустий публічний ключ, додатково вимагається від’єднаний підпис `SHA256SUMS.asc` / `.sig` і перевірка через `gpg --verify` в ізольованому `GNUPGHOME`. Приватний ключ у репозиторій не кладеться.

Файли копіюються поверх поточної інсталяції (`_copy_update_files`), крім захищених (`_PRESERVE_FILES = {"settings.json", "request_queue.json", "redactor1.md"}`). На Windows копіювання відкладається до перезапуску через `_apply_update.py` (запускається з `_apply_update.bat`); на macOS/Linux застосовується одразу. Розпакування ZIP — через `archive_extract.safe_extract_zip` (відсікання zip-slip).

**Публікація релізу:** workflow `.github/workflows/release-checksums.yml` на подію `release: published` збирає `WhisperFastGUI-{version}-src.zip` і `SHA256SUMS` (`scripts/make_release_checksums.py`) і завантажує їх як асети. Якщо в secrets є `GPG_PRIVATE_KEY` (опційно `GPG_PASSPHRASE`), CI додає `SHA256SUMS.asc`. Публічний ключ для клієнтів — файл `resources/release_signing_key.asc` (додається окремо, коли ключ заведено).

FFmpeg/Pandoc з GitHub Releases усе ще ставляться без перевірки контрольної суми — див. [SETUP-AND-DEPENDENCIES.uk.md](SETUP-AND-DEPENDENCIES.uk.md).

**Що вже зроблено правильно:** оновлення ніколи не тихе — `_schedule_startup_app_update_check` завжди показує діалог підтвердження перед завантаженням і ще один — перед перезапуском; користувацькі файли ніколи не перезаписуються оновленням.

## Реліз-ноти

`updates/release_notes.py` читає `resources/release_notes.json` (тексти EN/UK/RU) — кнопка з версією `vX.Y.Z (дата)` у верхній частині вікна відкриває вікно «Що нового» цією ж мовою, якою обрано інтерфейс. Змістовна історія версій — [CHANGELOG.md](CHANGELOG.md).

## Оновлення моделі Whisper

Окремий, незалежний механізм — `updates/model_updates.py`. Перевіряє на Hugging Face Hub, чи є новіша ревізія ваг для вже вибраної моделі, ніж та, що в локальному кеші, і за підтвердженням користувача завантажує її. Детальніше про сам кеш моделей і singleton — [MODEL-AND-DEVICE-MANAGEMENT.uk.md](MODEL-AND-DEVICE-MANAGEMENT.uk.md).

## Оновлення torch/CUDA для NVIDIA

Кнопка **[Оновлення]** також перевіряє версії pip-пакетів із `config.UPDATE_PACKAGES`. Якщо виявлено NVIDIA-GPU (`settings.json: has_nvidia`), `torch` оновлюється саме з індексу **CUDA 12.1** (`cu121`, `config.CUDA_INDEX`), а не зі звичайного PyPI — щоб користувачу з GPU не запропонували CPU-збірку.

## Куди дивитися далі

- Формат `settings.json: skip_app_update_version` та інших пов’язаних ключів — [CONFIGURATION.uk.md](CONFIGURATION.uk.md)
- Загальна архітектура застосунку — [ARCHITECTURE.uk.md](ARCHITECTURE.uk.md)

import os

# Корневая папка приложения (каталог, где лежит config.py) — единая база для путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Системные константы
# Версия приложения и дата создания этой версии
APP_VERSION = "1.1.1"
APP_DATE = "15.07.2026"  # дата создания версии
GITHUB_REPO = "magnusua/WhisperFastGUI"
GITHUB_BRANCH = "main"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"
CUDA_INDEX = "https://download.pytorch.org/whl/cu121"
VALID_EXTS = ('.mp3', '.wav', '.m4a', '.flac', '.ogg', '.mp4', '.mkv', '.avi', '.mov')
# Расширения по типам (единый источник для gui и input_files)
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.flac', '.ogg')
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov')
DEFAULT_MODEL = "large-v3-turbo"
# Список моделей faster-whisper для выбора в GUI (короткие имена, как в WhisperModel)
WHISPER_MODELS = [
    "tiny", "base", "small", "medium",
    "large-v1", "large-v2", "large-v3", "large-v3-turbo",
    "distil-large-v3",
]
# Значение по умолчанию для поля «Начало» в очереди
DEFAULT_START_TIMESTAMP = "00:00:00,000"
# Ключи элемента очереди (единая схема для gui и input_files)
QUEUE_ITEM_KEYS = ("path", "start", "end_segment_1", "end_segment_2", "end", "processed")
# Интервалы обновления UI в process_queue (секунды)
PROGRESS_UPDATE_INTERVAL_S = 0.1
LOG_UPDATE_INTERVAL_S = 0.5
# Порог (сек): считаем обработку «отрезком» файла, если start >= EPS или (duration - end) >= EPS
FULL_VIDEO_SEGMENT_EPS_S = 0.5
# Пакети, для которых проверяются обновления при нажатии кнопки «Обновления»
UPDATE_PACKAGES = [
    "pip", "setuptools", "wheel",
    "pygame", "pydub", "tkinterdnd2-universal", "pystray", "Pillow",
    "torch", "faster-whisper", "ctranslate2",
    "pyaudioop",  # для Python 3.13+; если не установлен — проверка пропускается
]

# Языки интерфейса и значение «авто» для языка транскрипции
SUPPORTED_LANGUAGES = ("EN", "UK", "RU")
LANG_AUTO_VALUE = "None"

# Справка: README_{EN|UK|RU}.md по языку интерфейса; запасной вариант — README.md
_HELP_FILENAMES = {
    "EN": "README_EN.md",
    "UK": "README_UK.md",
    "RU": "README_RU.md",
}


def load_help_text(lang_code=None):
    """Загружает текст справки на языке интерфейса (EN/UK/RU)."""
    if not lang_code:
        try:
            from i18n import get_language
            lang_code = get_language()
        except ImportError:
            lang_code = "EN"
    lang_code = (lang_code or "EN").upper()
    candidates = []
    if lang_code in _HELP_FILENAMES:
        candidates.append(_HELP_FILENAMES[lang_code])
    candidates.append("README.md")
    for name in candidates:
        file_path = os.path.join(BASE_DIR, name)
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
    try:
        from i18n import t
        return t("help_file_not_found")
    except ImportError:
        return "Help file (README.md) not found."


# Справка загружается лениво при открытии Help (gui вызывает load_help_text())


def get_whisper_cache_dir():
    """Каталог, где Hugging Face Hub хранит загруженные модели (faster-whisper и др.)."""
    cache = os.environ.get("HF_HUB_CACHE")
    if cache:
        return os.path.abspath(cache)
    home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.abspath(os.path.join(home, "hub"))


def get_whisper_model_cache_folder(model_name):
    """Имя папки модели в кэше HF Hub (Systran): models--Systran--faster-whisper-{model_name}."""
    return f"models--Systran--faster-whisper-{model_name}"


def find_whisper_model_cache_path(cache_root, model_name):
    """
    Возвращает путь к папке модели в кэше HF Hub, если она есть.
    Учитывает разные репозитории: Systran, h2oai, mobiuslabsgmbh и др.
    (папки вида models--<org>--faster-whisper-{model_name})
    """
    if not cache_root or not os.path.isdir(cache_root):
        return None
    suffix = f"--faster-whisper-{model_name}"
    try:
        for folder in os.listdir(cache_root):
            if folder.endswith(suffix) and os.path.isdir(os.path.join(cache_root, folder)):
                return os.path.join(cache_root, folder)
    except OSError:
        pass
    standard = os.path.join(cache_root, get_whisper_model_cache_folder(model_name))
    return standard if os.path.isdir(standard) else None
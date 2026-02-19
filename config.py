import os

# Корневая папка приложения (каталог, где лежит config.py) — единая база для путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Системные константы
# Версия приложения и дата создания этой версии
APP_VERSION = "1.0.8"
APP_DATE = "19.02.2026"  # дата создания версии
CUDA_INDEX = "https://download.pytorch.org/whl/cu121"
VALID_EXTS = ('.mp3', '.wav', '.m4a', '.flac', '.ogg', '.mp4', '.mkv', '.avi', '.mov')
# Расширения по типам (единый источник для gui и input_files)
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.flac', '.ogg')
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov')
DEFAULT_MODEL = "large-v3-turbo"
# Значение по умолчанию для поля «Начало» в очереди
DEFAULT_START_TIMESTAMP = "00:00:00,000"
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

# Функция для загрузки справки из внешнего файла (README.md); путь относительно BASE_DIR
def load_help_text():
    file_path = os.path.join(BASE_DIR, "README.md")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    try:
        from lang_manager import t
        return t("help_file_not_found")
    except ImportError:
        return "Файл справки (README.md) не найден."

# Справка загружается лениво при первом открытии Help (gui вызывает load_help_text())
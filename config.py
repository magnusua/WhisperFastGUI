import os

# Системные константы
# Версия приложения и дата создания этой версии
APP_VERSION = "1.0.4"
APP_DATE = "01.02.2026"  # дата создания версии
CUDA_INDEX = "https://download.pytorch.org/whl/cu121"
VALID_EXTS = ('.mp3', '.wav', '.m4a', '.flac', '.ogg', '.mp4', '.mkv', '.avi', '.mov')
DEFAULT_MODEL = "large-v3-turbo"
UPDATE_PACKAGES = ["pip", "pygame", "torch", "faster-whisper", "ctranslate2"]

# Языки интерфейса и значение «авто» для языка транскрипции
SUPPORTED_LANGUAGES = ("EN", "UK", "RU")
LANG_AUTO_VALUE = "None"

# Функция для загрузки справки из внешнего файла (README.md)
def load_help_text():
    file_path = "README.md"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    # Импорт менеджера языков для перевода сообщения об ошибке
    try:
        from lang_manager import t
        return t("help_file_not_found")
    except ImportError:
        return "Файл справки (README.md) не найден."

# Переменная справки, которую будет использовать gui.py
HELP_TEXT = load_help_text()
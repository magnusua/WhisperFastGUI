import warnings
import os
import sys

# Блокируем предупреждения до основных импортов
warnings.filterwarnings("ignore", category=UserWarning, module="pygame")
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

# Импортируем только то, что гарантированно есть в стандартной поставке Python
from tkinter import messagebox
from installer import install_dependencies, check_system

# Импорт менеджера языков (может быть недоступен на ранних этапах)
try:
    from lang_manager import t, set_language
except ImportError:
    from i18n_fallback import t, set_language

def on_app_closing(root, app=None, WhisperModelSingleton=None):
    """Логика безопасного завершения работы приложения."""
    if messagebox.askokcancel(t("exit"), t("exit_message")):
        if app:
            app.prepare_close()
        if WhisperModelSingleton:
            WhisperModelSingleton.unload()
        root.destroy()

def main():
    # Иконка на панели задач Windows: задаём AppUserModelID до создания окна
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WhisperFastGUI.2026")
        except Exception:
            pass

    # 0. Проверка версии Python и установка pyaudioop для Python 3.13+
    python_version = sys.version_info[:2]
    if python_version >= (3, 13):
        try:
            import pyaudioop
        except ImportError:
            print(t("python_detected", major=python_version[0], minor=python_version[1]))
            print(t("installing_pyaudioop"))
            import subprocess
            result = subprocess.run([sys.executable, "-m", "pip", "install", "pyaudioop"], 
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                print(t("pyaudioop_installed"))
            else:
                print(t("pyaudioop_warning"))
                print(t("pyaudioop_manual"))
    
    # 1. Проверка наличия критических библиотек перед импортом GUI
    try:
        import pydub
        import faster_whisper
        import torch
        # Если импорт прошел успешно, проверяем систему в логах (опционально)
        print(t("all_dependencies_found"))
    except ImportError as e:
        # Если чего-то не хватает, запускаем процесс установки
        print(t("missing_components", error=str(e)))
        print(t("starting_installation"))
        install_dependencies(log_func=print)
        messagebox.showinfo(t("installation"), t("dependencies_installed"))

    # 2. Локальный импорт компонентов проекта после проверки зависимостей
    # Это предотвращает ошибку ModuleNotFoundError при старте
    try:
        from gui import WhisperGUI, BaseTk
        from model_manager import WhisperModelSingleton
    except ImportError as e:
        messagebox.showerror(t("import_error"), t("import_error_msg", error=str(e)))
        return

    # 3. Инициализация и запуск интерфейса
    try:
        root = BaseTk()
        app = WhisperGUI(root, on_close_request=None)
        on_close = lambda: on_app_closing(root, app, WhisperModelSingleton)
        app._on_close_request = on_close  # закрытие из трея или при X (если не режим «Трей»)
        root.protocol("WM_DELETE_WINDOW", app.on_window_close)

        # Запуск главного цикла
        root.mainloop()
    except Exception as e:
        messagebox.showerror(t("critical_error"), t("critical_error_msg", error=str(e)))

if __name__ == "__main__":
    main()
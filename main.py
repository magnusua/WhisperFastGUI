import warnings
import os
import sys

# Блокируем предупреждения до основных импортов
warnings.filterwarnings("ignore", category=UserWarning, module="pygame")
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

# Импортируем только то, что гарантированно есть в стандартной поставке Python
from tkinter import messagebox
from installer import install_dependencies, check_system

from i18n import t, set_language

def on_app_closing(root, app=None, WhisperModelSingleton=None):
    """Логика безопасного завершения работы приложения."""
    if messagebox.askokcancel(t("exit"), t("exit_message")):
        if app:
            app.prepare_close()
        if WhisperModelSingleton:
            WhisperModelSingleton.unload()
        root.destroy()

def main():
    # Python 3.14+: PyTorch / ctranslate2 / faster-whisper часто без колёс на PyPI — установка падает
    if sys.version_info >= (3, 14):
        if not messagebox.askokcancel(
            t("python_unsupported_title"),
            t("python_unsupported_msg", major=sys.version_info.major, minor=sys.version_info.minor),
        ):
            sys.exit(0)

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
    def _check_deps():
        try:
            import pydub  # noqa: F401
            import faster_whisper  # noqa: F401
            import torch  # noqa: F401
            return True, None
        except ImportError as e:
            return False, str(e)

    ok, dep_err = _check_deps()
    if not ok:
        print(t("missing_components", error=dep_err or ""))
        print(t("starting_installation"))
        install_dependencies(log_func=print)
        ok, _ = _check_deps()
        if not ok:
            messagebox.showerror(
                t("import_error"),
                t("deps_install_incomplete_msg", py=sys.executable),
            )
            return
        messagebox.showinfo(t("installation"), t("dependencies_installed"))
    else:
        print(t("all_dependencies_found"))

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
        app = WhisperGUI(
            root,
            on_close_factory=lambda r, a: lambda: on_app_closing(r, a, WhisperModelSingleton),
        )
        root.protocol("WM_DELETE_WINDOW", app.on_window_close)

        # При аргументе --transcribe автоматически запускаем транскрибацию текущей очереди
        if len(sys.argv) > 1 and sys.argv[1].strip().lower() == "--transcribe":
            root.after(500, app.auto_start_queue)

        # Запуск главного цикла
        root.mainloop()
    except Exception as e:
        messagebox.showerror(t("critical_error"), t("critical_error_msg", error=str(e)))

if __name__ == "__main__":
    main()
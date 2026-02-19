# installer.py
import sys
import subprocess
import importlib.metadata
import urllib.request
import json
from config import CUDA_INDEX, UPDATE_PACKAGES

# Импорт менеджера языков
try:
    from lang_manager import t
except ImportError:
    from i18n_fallback import t

def get_python_version():
    """Получает версию Python в виде кортежа (major, minor)."""
    return sys.version_info[:2]

def needs_pyaudioop():
    """Проверяет, нужен ли pyaudioop (для Python 3.13+)."""
    version = get_python_version()
    return version >= (3, 13)

def get_latest_pypi_version(package):
    """Получает последнюю версию пакета с PyPI."""
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode())["info"]["version"]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return None

def check_updates(log_func):
    """Проверяет наличие обновлений для всех компонентов, нужных для работы программы."""
    log_func(t("checking_updates"))
    updates_found = []
    for pkg in UPDATE_PACKAGES:
        if pkg == "pyaudioop" and not needs_pyaudioop():
            continue
        try:
            current = importlib.metadata.version(pkg)
            latest = get_latest_pypi_version(pkg)
            if latest and current != latest:
                if pkg == "torch" and "2.10" in latest:
                    continue
                updates_found.append((pkg, current, latest))
                log_func(t("package_update", package=pkg, current=current, latest=latest))
            else:
                log_func(t("package_ok", package=pkg, version=current))
        except (importlib.metadata.PackageNotFoundError, TypeError):
            continue
    return updates_found


def _get_full_install_commands(include_nvidia=False):
    """
    Возвращает единый список команд полной установки: [(label, cmd), ...].
    Используется в install_dependencies и run_full_installation.
    """
    multimedia_packages = ["pygame", "pydub", "tkinterdnd2-universal", "pystray", "Pillow"]
    if needs_pyaudioop():
        multimedia_packages.append("pyaudioop")
    commands = [
        [t("installing_tools"), [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]],
        [t("installing_torch"), [sys.executable, "-m", "pip", "install", "--upgrade", "torch", "torchvision", "torchaudio", "--index-url", CUDA_INDEX]],
        [t("installing_whisper"), [sys.executable, "-m", "pip", "install", "--upgrade", "faster-whisper", "ctranslate2"]],
        [t("installing_multimedia"), [sys.executable, "-m", "pip", "install", "--upgrade"] + multimedia_packages],
    ]
    if include_nvidia:
        commands.insert(3, [t("installing_nvidia"), [sys.executable, "-m", "pip", "install", "--upgrade", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]])
    return commands


def install_dependencies(force=False, log_func=print, packages_to_update=None, include_nvidia=False):
    """
    Универсальная функция: устанавливает зависимости с нуля или обновляет выбранные пакеты.
    include_nvidia: ставить nvidia-* только при вызове из GUI (кнопки «Обновления» / «Зависимости»).
    При первом запуске (install.bat или автоустановка из main) nvidia не ставится.
    """
    if packages_to_update:
        packages_list = [p[0] for p in packages_to_update]
        log_func(t("updating_packages", packages=str(packages_list)))
        commands = []
        for pkg, _, _ in packages_to_update:
            if pkg == "torch":
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "torch", "torchvision", "torchaudio", "--index-url", CUDA_INDEX]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
            commands.append([t("updating_package", package=pkg), cmd])
        if include_nvidia:
            commands.append([t("installing_nvidia"), [sys.executable, "-m", "pip", "install", "--upgrade", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]])
    else:
        log_func(t("full_install", force=force))
        if needs_pyaudioop():
            log_func(t("python_detected_info", major=sys.version_info.major, minor=sys.version_info.minor))
        commands = _get_full_install_commands(include_nvidia=include_nvidia)

    for name, cmd in commands:
        if force and not packages_to_update: 
            cmd.extend(["--force-reinstall", "--no-cache-dir"])
        log_func(f"📦 {name}...")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    log_func(t("install_complete"))

def check_system(log_func):
    """Проверяет состояние системы: Torch, CUDA и наличие FFmpeg."""
    # Информация о версии Python
    python_version = get_python_version()
    log_func(t("python_version", major=sys.version_info.major, minor=sys.version_info.minor, micro=sys.version_info.micro))
    
    # Проверка pyaudioop для Python 3.13+
    if needs_pyaudioop():
        try:
            import pyaudioop
            log_func(t("pyaudioop_installed_check"))
        except ImportError:
            log_func(t("pyaudioop_not_installed"))
            log_func(t("pyaudioop_install_cmd"))
    
    # Проверка PyTorch и CUDA
    try:
        import torch
        cuda_available = str(torch.cuda.is_available())
        log_func(t("torch_info", version=torch.__version__, available=cuda_available))
        if torch.cuda.is_available():
            log_func(t("gpu_info", name=torch.cuda.get_device_name(0)))
        else:
            log_func(t("cuda_unavailable"))
    except ImportError:
        log_func(t("torch_not_installed"))

    # Проверка FFmpeg (необходим для работы pydub и декодирования аудио/видео)
    try:
        # Пытаемся запустить ffmpeg для проверки его наличия в PATH
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_func(t("ffmpeg_found"))
    except FileNotFoundError:
        log_func(t("ffmpeg_not_found"))
        log_func(t("ffmpeg_required"))

def _check_package_verbose(pkg_name, import_name=None):
    """Проверяет наличие пакета и выводит сообщение через t(). Возвращает True если установлен."""
    if import_name is None:
        import_name = pkg_name
    try:
        mod = __import__(import_name)
        ver = getattr(mod, "__version__", None)
        print(t("install_pkg_ok", pkg=pkg_name) + (f" ({ver})" if ver else ""))
        return True
    except ImportError:
        print(t("install_pkg_missing", pkg=pkg_name))
        return False


def run_full_installation():
    """Выполняет полную установку всех зависимостей с подробным выводом. Использует _get_full_install_commands."""
    print("==========================================")
    print("  " + t("install_title"))
    print("==========================================")
    print()
    print(t("install_step_check"))
    print()
    _check_package_verbose("torch")
    _check_package_verbose("faster-whisper", "faster_whisper")
    _check_package_verbose("ctranslate2")
    _check_package_verbose("pygame")
    _check_package_verbose("pydub")
    _check_package_verbose("tkinterdnd2-universal", "tkinterdnd2")
    _check_package_verbose("pystray")
    _check_package_verbose("Pillow", "PIL")
    if needs_pyaudioop():
        if not _check_package_verbose("pyaudioop"):
            print(t("pyaudioop_not_installed"))
    print()
    print(t("install_continue"))
    print()
    commands = _get_full_install_commands(include_nvidia=False)
    step_labels = [
        ("install_step_pip", "install_tools_ok", "install_pip_error"),
        ("install_step_torch", "install_torch_ok", "install_torch_warn"),
        ("install_step_whisper", "install_whisper_ok", "install_whisper_error"),
        ("install_step_multimedia", "install_multimedia_ok", "install_multimedia_error"),
    ]
    for i, (name, cmd) in enumerate(commands):
        step_msg, ok_msg, err_msg = step_labels[i]
        print(t(step_msg))
        if i == 3 and needs_pyaudioop():
            print(t("install_multimedia_pyaudioop"))
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(t(ok_msg) if result.returncode == 0 else t(err_msg))
        print()
    print(t("install_step_verify"))
    print()
    _check_package_verbose("torch")
    _check_package_verbose("faster-whisper", "faster_whisper")
    _check_package_verbose("ctranslate2")
    _check_package_verbose("pygame")
    _check_package_verbose("pydub")
    _check_package_verbose("tkinterdnd2-universal", "tkinterdnd2")
    _check_package_verbose("pystray")
    _check_package_verbose("Pillow", "PIL")
    try:
        import tkinter
        print(t("install_tkinter_ok"))
    except ImportError:
        print(t("install_tkinter_error"))
    if needs_pyaudioop():
        _check_package_verbose("pyaudioop")
    print()
    print(t("install_step_ffmpeg"))
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(t("install_ffmpeg_ok"))
    except (FileNotFoundError, subprocess.CalledProcessError):
        print(t("install_ffmpeg_missing"))
    print()
    print(t("install_step_cuda"))
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        print(t("install_cuda_ok", value=cuda_available))
        if cuda_available:
            print(t("gpu_info", name=torch.cuda.get_device_name(0)))
        else:
            print(t("install_cuda_cpu"))
    except ImportError:
        print(t("install_cuda_cpu"))
    except Exception:
        print(t("install_cuda_cpu"))
    print()
    print("==========================================")
    print(t("install_done_title"))
    print("==========================================")
    print()
    print(t("install_run_hint"))
    print()
    print(t("install_cpu_note"))
    print()

if __name__ == "__main__":
    try:
        run_full_installation()
    except KeyboardInterrupt:
        print("\n\n" + t("install_cancelled"))
        sys.exit(1)
    except Exception as e:
        print("\n\n" + t("install_failed", error=str(e)))
        import traceback
        traceback.print_exc()
        sys.exit(1)
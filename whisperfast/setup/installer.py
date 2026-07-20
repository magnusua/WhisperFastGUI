# installer.py
import re
import sys
import subprocess
import importlib.metadata
import urllib.request
import json
from whisperfast.config import CUDA_INDEX, UPDATE_PACKAGES

from whisperfast.setup.gpu_info import refresh_gpu_settings
from whisperfast.updates.app_updates import check_app_update
from whisperfast.updates.model_updates import check_downloaded_whisper_model_updates
from whisperfast.i18n import t
from whisperfast.platform_util import win_no_window_kwargs

try:
    from packaging.version import Version
except ImportError:
    Version = None




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


def get_latest_pip_index_version(package, index_url):
    """Последняя версия пакета з індексу pip (наприклад PyTorch cu121)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", package, "--index-url", index_url],
            capture_output=True,
            text=True,
            timeout=20,
            **win_no_window_kwargs(),
        )
        if result.returncode != 0:
            return None
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("LATEST:"):
                return line.split(":", 1)[1].strip()
            m = re.match(rf"^{re.escape(package)}\s+\(([^)]+)\)", line)
            if m:
                return m.group(1)
        for line in (result.stdout or "").splitlines():
            if "Available versions:" in line:
                part = line.split(":", 1)[-1].strip()
                if part:
                    return part.split(",")[0].strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _torch_needs_update(current, latest):
    """Порівняння версій torch (з урахуванням +cu121)."""
    if not latest:
        return False
    if current == latest:
        return False
    if Version is not None:
        try:
            cur_v = Version(current)
            lat_v = Version(latest)
            if cur_v == lat_v:
                return False
            if cur_v.base_version != lat_v.base_version:
                return True
            return str(cur_v) != str(lat_v)
        except Exception:
            pass
    cur_base = (current or "").split("+")[0]
    lat_base = latest.split("+")[0]
    if cur_base != lat_base:
        return True
    return current != latest


def _torch_install_cmd(use_cuda_index):
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "torch", "torchvision", "torchaudio"]
    if use_cuda_index:
        cmd.extend(["--index-url", CUDA_INDEX])
    return cmd


def check_updates(log_func):
    """Проверяет наличие обновлений для всех компонентов, нужных для работы программы.
    Пакеты, которые не установлены, добавляются в список для установки (например pystray, Pillow)."""
    log_func(t("checking_updates"))
    has_nvidia, gpu_name = refresh_gpu_settings()
    if gpu_name:
        log_func(t("gpu_info", name=gpu_name))
    elif has_nvidia:
        log_func(t("gpu_info", name=t("gpu_detected_unknown")))
    if has_nvidia:
        log_func(t("torch_update_index_cu121"))
    updates_found = []
    for pkg in UPDATE_PACKAGES:
        if pkg == "pyaudioop" and not needs_pyaudioop():
            continue
        try:
            current = importlib.metadata.version(pkg)
            if pkg == "torch":
                if has_nvidia:
                    latest = get_latest_pip_index_version(pkg, CUDA_INDEX)
                else:
                    latest = get_latest_pypi_version(pkg)
                needs = _torch_needs_update(current, latest)
            else:
                latest = get_latest_pypi_version(pkg)
                needs = bool(latest and current != latest)
            if needs:
                updates_found.append((pkg, current, latest))
                log_func(t("package_update", package=pkg, current=current, latest=latest))
            else:
                log_func(t("package_ok", package=pkg, version=current))
        except (importlib.metadata.PackageNotFoundError, TypeError):
            if pkg == "torch":
                latest = (
                    get_latest_pip_index_version(pkg, CUDA_INDEX)
                    if has_nvidia
                    else get_latest_pypi_version(pkg)
                )
            else:
                latest = get_latest_pypi_version(pkg)
            if latest:
                updates_found.append((pkg, None, latest))
                log_func(t("package_not_installed", package=pkg, latest=latest))
    model_updates = check_downloaded_whisper_model_updates(log_func=log_func)
    app_update = check_app_update(log_func=log_func)
    return {"packages": updates_found, "models": model_updates, "app": app_update}


def _get_full_install_commands(include_nvidia=False, use_cuda_torch=None):
    """
    Возвращает единый список команд полной установки: [(label, cmd), ...].
    Используется в install_dependencies и run_full_installation.
    """
    if use_cuda_torch is None:
        use_cuda_torch, _ = refresh_gpu_settings()
    multimedia_packages = [
        "pygame", "pydub", "tkinterdnd2-universal", "pystray", "Pillow", "cursor-sdk",
    ]
    if needs_pyaudioop():
        multimedia_packages.append("pyaudioop")
    commands = [
        [t("installing_tools"), [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]],
        [t("installing_torch"), _torch_install_cmd(use_cuda_torch)],
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
    has_nvidia, gpu_name = refresh_gpu_settings()
    if gpu_name:
        log_func(t("gpu_info", name=gpu_name))
    use_cuda_torch = has_nvidia
    if packages_to_update:
        packages_list = [p[0] for p in packages_to_update]
        log_func(t("updating_packages", packages=str(packages_list)))
        commands = []
        for pkg, _, _ in packages_to_update:
            if pkg == "torch":
                cmd = _torch_install_cmd(use_cuda_torch)
            else:
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
            commands.append([t("updating_package", package=pkg), cmd])
        if include_nvidia and has_nvidia:
            commands.append([t("installing_nvidia"), [sys.executable, "-m", "pip", "install", "--upgrade", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]])
    else:
        log_func(t("full_install", force=force))
        if needs_pyaudioop():
            log_func(t("python_detected_info", major=sys.version_info.major, minor=sys.version_info.minor))
        commands = _get_full_install_commands(include_nvidia=include_nvidia, use_cuda_torch=use_cuda_torch)

    for name, cmd in commands:
        if force and not packages_to_update:
            cmd.extend(["--force-reinstall", "--no-cache-dir"])
        log_func(f"📦 {name}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **win_no_window_kwargs())
        if result.returncode != 0 and result.stderr:
            log_func(t("install_step_failed", name=name))
            err = result.stderr.strip()
            if len(err) > 800:
                err = err[:800] + "\n..."
            for line in err.splitlines():
                log_func(line)
    log_func(t("install_complete"))

def check_system(log_func):
    """Проверяет состояние системы: Torch, CUDA и наличие FFmpeg."""
    has_nvidia, gpu_name = refresh_gpu_settings()
    if gpu_name:
        log_func(t("gpu_info", name=gpu_name))
    elif has_nvidia:
        log_func(t("gpu_info", name=t("gpu_detected_unknown")))

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
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **win_no_window_kwargs())
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
    _check_package_verbose("cursor-sdk", "cursor_sdk")
    if needs_pyaudioop():
        if not _check_package_verbose("pyaudioop"):
            print(t("pyaudioop_not_installed"))
    print()
    print(t("install_continue"))
    print()
    commands = _get_full_install_commands(include_nvidia=False)
    # Длина step_labels должна совпадать с len(commands) (при include_nvidia=False — 4 шага)
    step_labels = [
        ("install_step_pip", "install_tools_ok", "install_pip_error"),
        ("install_step_torch", "install_torch_ok", "install_torch_warn"),
        ("install_step_whisper", "install_whisper_ok", "install_whisper_error"),
        ("install_step_multimedia", "install_multimedia_ok", "install_multimedia_error"),
    ]
    assert len(step_labels) == len(commands), "step_labels must match commands length"
    for i, (name, cmd) in enumerate(commands):
        step_msg, ok_msg, err_msg = step_labels[i]
        print(t(step_msg))
        if i == 3 and needs_pyaudioop():
            print(t("install_multimedia_pyaudioop"))
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **win_no_window_kwargs())
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
    _check_package_verbose("cursor-sdk", "cursor_sdk")
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
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, **win_no_window_kwargs())
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
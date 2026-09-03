# installer.py
import os
import re
import shutil
import sys
import subprocess
import importlib.metadata
import urllib.request
import json
from whisperfast.config import CUDA_INDEX, UPDATE_PACKAGES

from whisperfast.setup.gpu_info import nvidia_from_settings, nvidia_for_install, refresh_gpu_settings
from whisperfast.settings import save_app_settings
from whisperfast.updates.app_updates import check_app_update
from whisperfast.updates.model_updates import check_downloaded_whisper_model_updates
from whisperfast.i18n import t
from whisperfast.platform_util import run_logged_command, win_no_window_kwargs
from whisperfast.setup.python_selector import _to_python_exe

try:
    from packaging.version import Version
except ImportError:
    Version = None




def get_python_version():
    """Получает версию Python в виде кортежа (major, minor)."""
    return sys.version_info[:2]


def _pip_python():
    """python.exe for pip — pythonw.exe hides output and often fails the install."""
    return _to_python_exe(sys.executable) or sys.executable


def parse_installer_argv(argv=None):
    """CLI for install.bat: --cuda / --cpu override GPU auto-detect. None = auto."""
    args = [str(a).strip().lower() for a in (sys.argv[1:] if argv is None else argv)]
    if "--cuda" in args:
        return True
    if "--cpu" in args:
        return False
    return None


def _resolve_cuda_choice(use_cuda_arg):
    """
    Returns (use_cuda_torch, include_nvidia_libs, gpu_name).
    If settings.gpu_model already names NVIDIA, skip hardware probe and install
    CUDA torch + nvidia-cublas/cudnn. --cpu still skips CUDA for that run.
    """
    saved, saved_name = nvidia_from_settings()
    if saved:
        save_app_settings({"has_nvidia": True, "gpu_model": saved_name})
        if use_cuda_arg is False:
            return False, False, saved_name
        return True, True, saved_name

    detected, name = refresh_gpu_settings()
    if use_cuda_arg is False:
        save_app_settings({"has_nvidia": False})
        return False, False, name
    if use_cuda_arg is True:
        save_app_settings({
            "has_nvidia": True,
            "gpu_model": (name or "").strip() or "NVIDIA",
        })
        return True, True, name
    use_cuda = bool(detected)
    return use_cuda, use_cuda, name

def needs_pyaudioop():
    """Нужен ли шим audioop на Python 3.13+ (pydub)."""
    return get_python_version() >= (3, 13)


def audioop_available():
    """True if stdlib audioop or a 3.13 shim (audioop-lts / pyaudioop) can be imported."""
    for name in ("audioop", "pyaudioop"):
        try:
            __import__(name)
            return True
        except ImportError:
            continue
    return False


AUDIOOP_SHIM_PIP = "audioop-lts"


def _normalize_pip_spec(spec):
    """Map the dead PyPI name pyaudioop to audioop-lts (no 3.13 wheels)."""
    if not spec:
        return spec
    name = spec.split("[")[0].split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0]
    name = name.split("<")[0].split(">")[0].strip().lower()
    if name == "pyaudioop":
        return AUDIOOP_SHIM_PIP
    return spec


def _normalize_pip_specs(specs):
    out = []
    seen = set()
    for spec in specs:
        spec = _normalize_pip_spec(spec)
        if not spec:
            continue
        key = spec.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def ensure_audioop_shim(log_func=print, summarize=True):
    """Install audioop-lts so pydub can import audioop on Python 3.13+."""
    if not needs_pyaudioop() or audioop_available():
        return True
    log_func(t("python_detected", major=sys.version_info.major, minor=sys.version_info.minor))
    log_func(t("installing_pyaudioop"))
    import importlib

    _run_pip_specs([AUDIOOP_SHIM_PIP], log_func, summarize=summarize, retry_each=False)
    importlib.invalidate_caches()
    if audioop_available():
        log_func(t("pyaudioop_installed"))
        return True
    log_func(t("pyaudioop_warning"))
    log_func(t("pyaudioop_manual"))
    return False


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
            [_pip_python(), "-m", "pip", "index", "versions", package, "--index-url", index_url],
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
    cmd = [_pip_python(), "-m", "pip", "install", "--upgrade", "torch", "torchvision", "torchaudio"]
    if use_cuda_index:
        cmd.extend(["--index-url", CUDA_INDEX])
    return cmd


def check_updates(log_func):
    """Проверяет наличие обновлений для всех компонентов, нужных для работы программы.
    Пакеты, которые не установлены, добавляются в список для установки (например pystray, Pillow)."""
    log_func(t("checking_updates"))
    has_nvidia, gpu_name = nvidia_for_install()
    if gpu_name:
        log_func(t("gpu_info", name=gpu_name))
    elif has_nvidia:
        log_func(t("gpu_info", name=t("gpu_detected_unknown")))
    if has_nvidia:
        log_func(t("torch_update_index_cu121"))
    app_update = check_app_update(log_func=log_func)
    updates_found = []
    for pkg in UPDATE_PACKAGES:
        if pkg in ("pyaudioop", "audioop-lts") and not needs_pyaudioop():
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
            if pkg in ("audioop-lts", "pyaudioop") and audioop_available():
                log_func(t("package_ok", package=pkg, version="ok"))
                continue
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
    from whisperfast.setup.external_tools import check_external_tool_updates

    external_updates = check_external_tool_updates(log_func=log_func)
    return {
        "packages": updates_found,
        "models": model_updates,
        "app": app_update,
        "external": external_updates,
    }


# Spec for MarkItDown: all office formats used by the queue (no Azure extras).
MARKITDOWN_PIP_SPEC = "markitdown[pdf,docx,pptx,xlsx,xls]"

# faster-whisper 1.2.1 сама вимагає ctranslate2>=4.0,<5 (Requires-Dist у її METADATA).
# Той самий діапазон зафіксовано тут (і в requirements.txt), щоб `pip install --upgrade`
# без обмежень не поставив колись несумісну пару torch/ctranslate2/faster-whisper —
# див. CODE-REVIEW.md, розділ 7.
FASTER_WHISPER_PIP_SPEC = "faster-whisper>=1.0.0,<2.0.0"
CTRANSLATE2_PIP_SPEC = "ctranslate2>=4.0,<5.0"
_TORCH_INSTALL_TIMEOUT = 1800
MULTIMEDIA_REQUIRED = (
    "pygame",
    "pydub",
    "tkinterdnd2-universal",
    "pystray",
    "Pillow",
    "packaging",
)
MULTIMEDIA_OPTIONAL = (
    "cursor-sdk",
    MARKITDOWN_PIP_SPEC,
)

_PIP_NOISE = (
    "requirement already satisfied",
    "ignoring invalid distribution",
    "looking in indexes",
    "using cached",
    "downloading ",
)


def _pip_should_stream(line: str) -> bool:
    low = line.lower().strip()
    if not low or any(n in low for n in _PIP_NOISE):
        return False
    if low.startswith("warning:"):
        return False
    if low.startswith(("collecting ", "installing collected", "error")):
        return True
    if "no matching distribution" in low or "could not find a version" in low:
        return True
    return False


def _pip_is_error(line: str) -> bool:
    low = line.lower().strip()
    if "ignoring invalid" in low:
        return False
    return (
        low.startswith("error")
        or "error:" in low
        or "no matching distribution" in low
        or "could not find a version" in low
    )


def _pip_installed_names(lines) -> str:
    for line in reversed(lines):
        low = line.lower()
        if low.startswith("successfully installed"):
            return line.split(":", 1)[-1].strip()
        if "successfully installed" in low:
            return line.split("successfully installed", 1)[-1].strip(" :")
    return ""


def _cleanup_broken_pip_dists(log_func) -> None:
    """Remove leftover '~ip' / '~umpy' dirs from interrupted pip upgrades."""
    dirs = []
    try:
        import site

        dirs.extend(site.getsitepackages() or [])
        user = site.getusersitepackages()
        if user:
            dirs.append(user)
    except Exception:
        pass
    for p in sys.path:
        if p and os.path.basename(p).lower() == "site-packages":
            dirs.append(p)
    seen = set()
    cleaned = []
    for base in dirs:
        n = os.path.normcase(os.path.abspath(base)) if base else ""
        if not n or n in seen or not os.path.isdir(base):
            continue
        seen.add(n)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not name.startswith("~"):
                continue
            path = os.path.join(base, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                cleaned.append(name)
            except OSError:
                pass
    if cleaned:
        log_func(t("install_cleaned_broken_dist", names=", ".join(sorted(set(cleaned)))))


def _run_install_cmd(cmd, log_func, timeout=600, summarize=True):
    """Run pip and log a short per-step result instead of the full pip dump."""
    pip_cmd = list(cmd)
    if len(pip_cmd) >= 3 and pip_cmd[1:3] == ["-m", "pip"] and "--progress-bar" not in pip_cmd:
        pip_cmd.extend(["--progress-bar", "off"])
    collected = []

    def _on_line(line):
        collected.append(line)
        if summarize:
            if _pip_should_stream(line):
                log_func("   " + line)
        else:
            log_func(line)

    code = run_logged_command(pip_cmd, log_func=_on_line, timeout=timeout)
    if not summarize:
        return code
    if code != 0:
        errors = [ln for ln in collected if _pip_is_error(ln) and "ignoring invalid" not in ln.lower()]
        for ln in (errors or collected)[-8:]:
            log_func("   " + ln)
        return code
    installed = _pip_installed_names(collected)
    if installed:
        log_func(t("install_step_ok_changed", names=installed))
    else:
        log_func(t("install_step_ok_unchanged"))
    return code


def _pip_install_cmd(specs, extra_args=None, force=False):
    cmd = [_pip_python(), "-m", "pip", "install", "--upgrade"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(specs)
    if force:
        cmd.extend(["--force-reinstall", "--no-cache-dir"])
    return cmd


def _run_pip_specs(
    specs,
    log_func,
    extra_args=None,
    force=False,
    summarize=True,
    timeout=600,
    retry_each=True,
):
    """Install specs as a group; if that fails, retry each spec so one bad wheel does not block the rest."""
    specs = _normalize_pip_specs(specs)
    if not specs:
        return 0
    code = _run_install_cmd(
        _pip_install_cmd(specs, extra_args, force),
        log_func,
        timeout=timeout,
        summarize=summarize,
    )
    if code == 0 or not retry_each or len(specs) <= 1:
        return code
    log_func(t("install_retry_each_package"))
    failed = False
    for spec in specs:
        one = _run_install_cmd(
            _pip_install_cmd([spec], extra_args, force),
            log_func,
            timeout=timeout,
            summarize=summarize,
        )
        if one != 0:
            failed = True
            log_func(t("install_step_failed", name=spec))
    return 1 if failed else 0


def _run_torch_install(log_func, use_cuda, force=False, summarize=True):
    specs = ["torch", "torchvision", "torchaudio"]
    extra = ["--index-url", CUDA_INDEX] if use_cuda else None
    code = _run_pip_specs(
        specs,
        log_func,
        extra_args=extra,
        force=force,
        summarize=summarize,
        timeout=_TORCH_INSTALL_TIMEOUT,
        retry_each=False,
    )
    if code != 0 and use_cuda:
        log_func(t("install_torch_cuda_fallback"))
        code = _run_pip_specs(
            specs,
            log_func,
            extra_args=None,
            force=force,
            summarize=summarize,
            timeout=_TORCH_INSTALL_TIMEOUT,
            retry_each=False,
        )
    return code


def _multimedia_required_specs():
    specs = list(MULTIMEDIA_REQUIRED)
    if needs_pyaudioop() and not audioop_available():
        specs.insert(0, AUDIOOP_SHIM_PIP)
    return specs


def _run_full_package_install(log_func, include_nvidia=False, use_cuda_torch=False, force=False, summarize=True):
    """pip/setuptools → torch (CUDA then CPU fallback) → whisper → required GUI pkgs → optional extras."""
    steps = [
        ("installing_tools", lambda: _run_pip_specs(
            ["pip", "setuptools", "wheel"], log_func, force=force, summarize=summarize
        )),
        ("installing_torch", lambda: _run_torch_install(
            log_func, use_cuda=use_cuda_torch, force=force, summarize=summarize
        )),
        ("installing_whisper", lambda: _run_pip_specs(
            [FASTER_WHISPER_PIP_SPEC, CTRANSLATE2_PIP_SPEC],
            log_func, force=force, summarize=summarize,
        )),
    ]
    if include_nvidia:
        steps.append((
            "installing_nvidia",
            lambda: _run_pip_specs(
                ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"],
                log_func, force=force, summarize=summarize,
            ),
        ))

    def _multimedia():
        if needs_pyaudioop():
            log_func(t("install_multimedia_pyaudioop"))
        return _run_pip_specs(
            _multimedia_required_specs(), log_func, force=force, summarize=summarize
        )

    steps.append(("installing_multimedia", _multimedia))
    steps.append((
        "installing_optional",
        lambda: _run_pip_specs(
            list(MULTIMEDIA_OPTIONAL), log_func, force=force, summarize=summarize
        ),
    ))
    codes = {}
    for key, runner in steps:
        log_func(t("install_step_progress", name=t(key)))
        code = runner()
        codes[key] = code
        if code != 0:
            log_func(t("install_step_failed", name=t(key)))
    return codes


def _get_full_install_commands(include_nvidia=False, use_cuda_torch=None):
    """Command list for tests/docs; runtime install uses _run_full_package_install."""
    if use_cuda_torch is None:
        use_cuda_torch, _ = nvidia_for_install()
    py = _pip_python()
    commands = [
        [t("installing_tools"), [py, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]],
        [t("installing_torch"), _torch_install_cmd(use_cuda_torch)],
        [t("installing_whisper"), [py, "-m", "pip", "install", "--upgrade", FASTER_WHISPER_PIP_SPEC, CTRANSLATE2_PIP_SPEC]],
        [t("installing_multimedia"), [py, "-m", "pip", "install", "--upgrade"] + _multimedia_required_specs()],
        [t("installing_optional"), [py, "-m", "pip", "install", "--upgrade"] + list(MULTIMEDIA_OPTIONAL)],
    ]
    if include_nvidia:
        commands.insert(3, [t("installing_nvidia"), [py, "-m", "pip", "install", "--upgrade", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]])
    return commands


def install_dependencies(force=False, log_func=print, packages_to_update=None, include_nvidia=False, install_external=None):
    """
    Универсальная функция: устанавливает зависимости с нуля или обновляет выбранные пакеты.
    include_nvidia: ставить nvidia-cublas/cudnn. Если в settings уже NVIDIA GPU —
    CUDA torch и эти библиотеки ставятся всегда, даже если флаг False.
    install_external: ставить FFmpeg/Pandoc. По умолчанию — да при полной установке, нет при точечном pip.
    """
    has_nvidia, gpu_name = nvidia_for_install()
    if gpu_name:
        log_func(t("gpu_info", name=gpu_name))
    use_cuda_torch = has_nvidia
    if use_cuda_torch:
        include_nvidia = True
    _cleanup_broken_pip_dists(log_func)
    if packages_to_update:
        packages_list = [p[0] for p in packages_to_update]
        log_func(t("updating_packages", packages=str(packages_list)))
        commands = []
        py = _pip_python()
        for pkg, _, _ in packages_to_update:
            pkg = _normalize_pip_spec(pkg)
            if pkg == "torch":
                commands.append([t("updating_package", package=pkg), None])
            elif pkg == "markitdown":
                cmd = [py, "-m", "pip", "install", "--upgrade", MARKITDOWN_PIP_SPEC]
                commands.append([t("updating_package", package=pkg), cmd])
            elif pkg == "faster-whisper":
                # Точечное обновление тоже должно уважать пиннинг совместимости с
                # ctranslate2 (см. FASTER_WHISPER_PIP_SPEC/CTRANSLATE2_PIP_SPEC выше и
                # CODE-REVIEW.md, разд. 7) — иначе кнопка «Обновления» в GUI могла
                # молча поставить faster-whisper без диапазона версий.
                cmd = [py, "-m", "pip", "install", "--upgrade", FASTER_WHISPER_PIP_SPEC]
                commands.append([t("updating_package", package=pkg), cmd])
            elif pkg == "ctranslate2":
                cmd = [py, "-m", "pip", "install", "--upgrade", CTRANSLATE2_PIP_SPEC]
                commands.append([t("updating_package", package=pkg), cmd])
            else:
                cmd = [py, "-m", "pip", "install", "--upgrade", pkg]
                commands.append([t("updating_package", package=pkg), cmd])
        if include_nvidia and has_nvidia:
            commands.append([t("installing_nvidia"), [py, "-m", "pip", "install", "--upgrade", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"]])
        for name, cmd in commands:
            log_func(t("install_step_progress", name=name))
            if cmd is None:
                code = _run_torch_install(log_func, use_cuda=use_cuda_torch, summarize=True)
            else:
                code = _run_install_cmd(cmd, log_func)
            if code != 0:
                log_func(t("install_step_failed", name=name))
    else:
        log_func(t("full_install_force") if force else t("full_install"))
        if needs_pyaudioop():
            log_func(t("python_detected_info", major=sys.version_info.major, minor=sys.version_info.minor))
        _run_full_package_install(
            log_func,
            include_nvidia=include_nvidia,
            use_cuda_torch=use_cuda_torch,
            force=force,
            summarize=True,
        )
    if install_external is None:
        install_external = not packages_to_update
    if install_external:
        from whisperfast.setup.external_tools import install_external_tools

        install_external_tools(log_func, missing_only=True)


def check_system(log_func):
    """Проверяет состояние системы: Torch, CUDA, FFmpeg, Pandoc и pip-пакеты."""
    has_nvidia, gpu_name = refresh_gpu_settings()
    if gpu_name:
        log_func(t("gpu_info", name=gpu_name))
    elif has_nvidia:
        log_func(t("gpu_info", name=t("gpu_detected_unknown")))

    # Информация о версии Python
    python_version = get_python_version()
    log_func(t("python_version", major=sys.version_info.major, minor=sys.version_info.minor, micro=sys.version_info.micro))
    
    # Проверка audioop для Python 3.13+
    if needs_pyaudioop():
        if audioop_available():
            log_func(t("pyaudioop_installed_check"))
        else:
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

    # Ключевые pip-пакеты приложения
    for pkg, import_name in (
        ("faster-whisper", "faster_whisper"),
        ("ctranslate2", "ctranslate2"),
        ("pygame", "pygame"),
        ("pydub", "pydub"),
        ("tkinterdnd2-universal", "tkinterdnd2"),
        ("pystray", "pystray"),
        ("Pillow", "PIL"),
        ("cursor-sdk", "cursor_sdk"),
        ("markitdown", "markitdown"),
        ("packaging", "packaging"),
    ):
        try:
            ver = importlib.metadata.version(pkg)
            log_func(t("package_ok", package=pkg, version=ver))
        except importlib.metadata.PackageNotFoundError:
            log_func(t("package_missing_hint", package=pkg))

    from whisperfast.setup.external_tools import log_external_tools_status

    log_external_tools_status(log_func)

def _distribution_installed(dist_name):
    try:
        importlib.metadata.version(dist_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _check_package_verbose(pkg_name, import_name=None):
    """Проверяет наличие пакета и выводит сообщение через t(). Возвращает True если установлен."""
    if import_name is None:
        import_name = pkg_name
    try:
        ver = importlib.metadata.version(pkg_name)
    except importlib.metadata.PackageNotFoundError:
        print(t("install_pkg_missing", pkg=pkg_name))
        return False
    try:
        __import__(import_name)
    except ImportError:
        pass
    print(t("install_pkg_ok", pkg=pkg_name) + (f" ({ver})" if ver else ""))
    return True


def run_full_installation(use_cuda_arg=None):
    """Выполняет полную установку всех зависимостей с подробным выводом."""
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
    _check_package_verbose("markitdown")
    _check_package_verbose("packaging")
    if needs_pyaudioop() and not audioop_available():
        print(t("pyaudioop_not_installed"))
    print()
    print(t("install_continue"))
    print()
    use_cuda_torch, include_nvidia, gpu_name = _resolve_cuda_choice(use_cuda_arg)
    if use_cuda_torch:
        if gpu_name:
            print(t("gpu_info", name=gpu_name))
        print(t("install_cuda_chosen_yes"))
    else:
        print(t("install_cuda_chosen_no"))
    print()

    print(t("install_step_pip"))
    code = _run_pip_specs(["pip", "setuptools", "wheel"], print, summarize=False)
    print(t("install_tools_ok") if code == 0 else t("install_pip_error"))
    print()

    print(t("install_step_torch_cuda") if use_cuda_torch else t("install_step_torch_cpu"))
    code = _run_torch_install(print, use_cuda=use_cuda_torch, summarize=False)
    print(t("install_torch_ok") if code == 0 else t("install_torch_warn"))
    print()

    print(t("install_step_whisper"))
    code = _run_pip_specs(
        [FASTER_WHISPER_PIP_SPEC, CTRANSLATE2_PIP_SPEC], print, summarize=False
    )
    print(t("install_whisper_ok") if code == 0 else t("install_whisper_error"))
    print()

    if include_nvidia:
        print(t("install_step_nvidia_libs"))
        nv = _run_pip_specs(
            ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"], print, summarize=False
        )
        print(t("install_nvidia_libs_ok") if nv == 0 else t("install_nvidia_libs_warn"))
        print()

    print(t("install_step_multimedia"))
    if needs_pyaudioop():
        print(t("install_multimedia_pyaudioop"))
    code = _run_pip_specs(_multimedia_required_specs(), print, summarize=False)
    print(t("install_multimedia_ok") if code == 0 else t("install_multimedia_error"))
    print()

    print(t("install_step_optional"))
    opt = _run_pip_specs(list(MULTIMEDIA_OPTIONAL), print, summarize=False)
    print(t("install_optional_ok") if opt == 0 else t("install_optional_warn"))
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
    _check_package_verbose("markitdown")
    _check_package_verbose("packaging")
    try:
        import tkinter
        print(t("install_tkinter_ok"))
    except ImportError:
        print(t("install_tkinter_error"))
    if needs_pyaudioop():
        if audioop_available():
            print(t("pyaudioop_installed_check"))
        else:
            print(t("pyaudioop_not_installed"))
    print()
    from whisperfast.setup.external_tools import install_external_tools

    install_external_tools(print, missing_only=True)
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

    missing = []
    for pkg in ("torch", "faster-whisper", "pydub"):
        if not _distribution_installed(pkg):
            missing.append(pkg)
    if missing:
        print(t("install_critical_missing", packages=", ".join(missing)))
        print(t("install_critical_missing_hint"))
        sys.exit(1)

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
        run_full_installation(use_cuda_arg=parse_installer_argv())
    except KeyboardInterrupt:
        print("\n\n" + t("install_cancelled"))
        sys.exit(1)
    except Exception as e:
        print("\n\n" + t("install_failed", error=str(e)))
        import traceback
        traceback.print_exc()
        sys.exit(1)
"""Перевірка та оновлення самої програми WhisperFastGUI з GitHub."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Dict, Optional

from config import (
    APP_VERSION,
    BASE_DIR,
    GITHUB_BRANCH,
    GITHUB_REPO,
    GITHUB_URL,
)
from i18n import t

try:
    from packaging.version import Version
except ImportError:
    Version = None

_UPDATE_STAGING_DIR = "_update_staging"
_PRESERVE_FILES = frozenset({"settings.json", "request_queue.json"})
_RAW_CONFIG_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/config.py"
_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"


def _win_no_window_kwargs():
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def get_local_app_version() -> str:
    return APP_VERSION


def _parse_config_version(config_text: str) -> tuple[Optional[str], Optional[str]]:
    version_match = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', config_text)
    date_match = re.search(r'APP_DATE\s*=\s*["\']([^"\']+)["\']', config_text)
    version = version_match.group(1).strip() if version_match else None
    date = date_match.group(1).strip() if date_match else None
    return version, date


def fetch_remote_app_version(timeout: int = 15) -> tuple[Optional[str], Optional[str]]:
    """Читає APP_VERSION / APP_DATE з config.py на GitHub (гілка main)."""
    request = urllib.request.Request(
        _RAW_CONFIG_URL,
        headers={"User-Agent": "WhisperFastGUI-Updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None
    return _parse_config_version(text)


def _version_is_newer(remote: str, current: str) -> bool:
    if not remote or not current:
        return False
    if remote == current:
        return False
    if Version is not None:
        try:
            return Version(remote) > Version(current)
        except Exception:
            pass
    return remote != current


def is_git_repo(base_dir: Optional[str] = None) -> bool:
    base_dir = base_dir or BASE_DIR
    return os.path.isdir(os.path.join(base_dir, ".git"))


def check_app_update(log_func: Optional[Callable[[str], None]] = None) -> Dict:
    """
    Порівнює локальну версію з config.py на GitHub.
    Повертає словник: needs_update, current, remote, remote_date, url.
    """
    current = get_local_app_version()
    remote, remote_date = fetch_remote_app_version()
    info = {
        "needs_update": False,
        "current": current,
        "remote": remote or "",
        "remote_date": remote_date or "",
        "url": GITHUB_URL,
    }
    if log_func:
        log_func(t("checking_app_update", url=GITHUB_URL))
    if not remote:
        if log_func:
            log_func(t("app_update_check_failed"))
        return info
    if _version_is_newer(remote, current):
        info["needs_update"] = True
        if log_func:
            log_func(t("app_update_available", current=current, latest=remote))
    elif log_func:
        log_func(t("app_update_ok", version=current))
    return info


def _update_via_git(log_func: Callable[[str], None]) -> bool:
    if not is_git_repo():
        return False
    log_func(t("app_updating_git"))
    try:
        fetch = subprocess.run(
            ["git", "-C", BASE_DIR, "fetch", "origin", GITHUB_BRANCH],
            capture_output=True,
            text=True,
            timeout=120,
            **_win_no_window_kwargs(),
        )
        if fetch.returncode != 0:
            err = (fetch.stderr or fetch.stdout or "").strip()
            log_func(t("app_update_error", error=err or f"git fetch exit {fetch.returncode}"))
            return False
        pull = subprocess.run(
            ["git", "-C", BASE_DIR, "pull", "--ff-only", "origin", GITHUB_BRANCH],
            capture_output=True,
            text=True,
            timeout=120,
            **_win_no_window_kwargs(),
        )
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout or "").strip()
            log_func(t("app_update_error", error=err or f"git pull exit {pull.returncode}"))
            return False
        if pull.stdout.strip():
            log_func(pull.stdout.strip())
        log_func(t("app_update_done"))
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        log_func(t("app_update_error", error=str(e)))
        return False


def _download_repo_zip(log_func: Callable[[str], None]) -> Optional[str]:
    log_func(t("app_updating_download"))
    staging_root = os.path.join(BASE_DIR, _UPDATE_STAGING_DIR)
    os.makedirs(staging_root, exist_ok=True)
    zip_path = os.path.join(staging_root, "repo.zip")
    request = urllib.request.Request(_ZIP_URL, headers={"User-Agent": "WhisperFastGUI-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            with open(zip_path, "wb") as out:
                shutil.copyfileobj(response, out)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log_func(t("app_update_error", error=str(e)))
        return None
    extract_dir = os.path.join(staging_root, "extracted")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except (zipfile.BadZipFile, OSError) as e:
        log_func(t("app_update_error", error=str(e)))
        return None
    try:
        os.remove(zip_path)
    except OSError:
        pass
    try:
        entries = os.listdir(extract_dir)
    except OSError:
        return None
    for name in entries:
        candidate = os.path.join(extract_dir, name)
        if os.path.isdir(candidate) and name.lower().startswith("whisperfastgui"):
            return candidate
    return None


def _copy_update_files(source_dir: str, log_func: Callable[[str], None]) -> int:
    copied = 0
    for name in os.listdir(source_dir):
        if name in _PRESERVE_FILES or name.startswith("."):
            continue
        src = os.path.join(source_dir, name)
        dst = os.path.join(BASE_DIR, name)
        if name == _UPDATE_STAGING_DIR:
            continue
        try:
            if os.path.isdir(src):
                if os.path.basename(src) == "__pycache__":
                    continue
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(src, dst)
            copied += 1
        except OSError as e:
            log_func(t("app_update_copy_failed", name=name, error=str(e)))
    return copied


def _launcher_command() -> list:
    vbs = os.path.join(BASE_DIR, "run_whisper.vbs")
    if sys.platform == "win32" and os.path.isfile(vbs):
        return ["wscript.exe", vbs]
    return [sys.executable, os.path.join(BASE_DIR, "main.py")]


def _write_restart_script(source_dir: str) -> str:
    """Створює скрипт, який застосує оновлення після закриття програми."""
    if sys.platform == "win32":
        script_path = os.path.join(BASE_DIR, "_apply_update.bat")
        launcher = _launcher_command()
        launcher_line = subprocess.list2cmdline(launcher)
        lines = [
            "@echo off",
            "cd /d \"%~dp0\"",
            "timeout /t 2 /nobreak >nul",
        ]
        for name in os.listdir(source_dir):
            if name in _PRESERVE_FILES or name.startswith("."):
                continue
            if name == _UPDATE_STAGING_DIR:
                continue
            src = os.path.join(source_dir, name)
            if os.path.isdir(src):
                lines.append(f'if exist "{name}" rmdir /s /q "{name}"')
                lines.append(f'xcopy /E /I /Y "{src}" "{name}\\" >nul')
            else:
                lines.append(f'copy /Y "{src}" "{name}" >nul')
        lines.extend([
            f"start \"\" {launcher_line}",
            f"rmdir /s /q \"{_UPDATE_STAGING_DIR}\"",
            "del \"%~f0\"",
        ])
        with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\r\n".join(lines) + "\r\n")
        return script_path

    script_path = os.path.join(BASE_DIR, "_apply_update.sh")
    launcher = _launcher_command()
    launcher_line = subprocess.list2cmdline(launcher)
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/bin/sh\n")
        f.write("cd \"$(dirname \"$0\")\"\n")
        f.write("sleep 2\n")
        f.write(f"rsync -a --delete --exclude settings.json --exclude request_queue.json --exclude {_UPDATE_STAGING_DIR} \"{source_dir}/\" ./\n")
        f.write(f"{launcher_line} &\n")
        f.write(f"rm -rf \"{_UPDATE_STAGING_DIR}\"\n")
        f.write("rm -- \"$0\"\n")
    os.chmod(script_path, 0o755)
    return script_path


def _update_via_zip(log_func: Callable[[str], None]) -> tuple[bool, Optional[str]]:
    source_dir = _download_repo_zip(log_func)
    if not source_dir:
        return False, None
    if sys.platform == "win32":
        script = _write_restart_script(source_dir)
        log_func(t("app_update_staged_restart"))
        return True, script
    copied = _copy_update_files(source_dir, log_func)
    if copied <= 0:
        log_func(t("app_update_error", error="no files copied"))
        return False, None
    staging_root = os.path.join(BASE_DIR, _UPDATE_STAGING_DIR)
    shutil.rmtree(staging_root, ignore_errors=True)
    log_func(t("app_update_done"))
    return True, None


def apply_app_update(log_func: Callable[[str], None] = print) -> Dict:
    """
    Оновлює програму: git pull (якщо є .git) або завантаження ZIP з GitHub.
    На Windows ZIP-оновлення застосовується після перезапуску (повертає restart_script).
    """
    log_func(t("app_updating"))
    needs_restart = False
    restart_script = None
    success = False

    if is_git_repo():
        success = _update_via_git(log_func)
        needs_restart = success
    else:
        success, restart_script = _update_via_zip(log_func)
        needs_restart = bool(restart_script)

    return {
        "success": success,
        "needs_restart": needs_restart,
        "restart_script": restart_script,
    }

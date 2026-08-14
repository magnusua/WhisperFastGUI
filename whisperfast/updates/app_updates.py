"""Перевірка та оновлення самої програми WhisperFastGUI з GitHub."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Dict, Optional, Tuple

from whisperfast.config import (
    APP_VERSION,
    BASE_DIR,
    GITHUB_BRANCH,
    GITHUB_REPO,
    GITHUB_URL,
    RESOURCES_DIR,
)
from whisperfast.archive_extract import UnsafeArchiveMember, safe_extract_zip
from whisperfast.i18n import t
from whisperfast.platform_util import win_no_window_kwargs
from whisperfast.updates.checksums import (
    expected_digest_for_filename,
    parse_sha256sums,
    verify_file_sha256,
)

try:
    from packaging.version import Version
except ImportError:
    Version = None

_UPDATE_STAGING_DIR = "_update_staging"
_PRESERVE_FILES = frozenset({"settings.json", "request_queue.json", "redactor1.md"})
_RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_RELEASE_SIGNING_KEY = os.path.join(RESOURCES_DIR, "release_signing_key.asc")
_USER_AGENT = "WhisperFastGUI-Updater"
_CHECKSUM_FILENAMES = frozenset({"sha256sums", "sha256sums.txt"})
_SIG_FILENAMES = frozenset({"sha256sums.asc", "sha256sums.txt.asc", "sha256sums.sig"})


def get_local_app_version() -> str:
    return APP_VERSION



def _normalize_tag_version(tag: str) -> str:
    tag = (tag or "").strip()
    if len(tag) >= 2 and tag[0] in "vV" and tag[1].isdigit():
        return tag[1:]
    return tag


def _format_release_date(iso: str) -> str:
    raw = (iso or "").strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return f"{raw[8:10]}.{raw[5:7]}.{raw[0:4]}"
    return ""


def _github_json(url: str, timeout: int = 20) -> Optional[dict]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fetch_latest_github_release(timeout: int = 20) -> Optional[dict]:
    return _github_json(_RELEASES_API_URL, timeout=timeout)


def _asset_name(asset: dict) -> str:
    return (asset.get("name") or "").strip()


def _pick_release_assets(release: dict) -> dict:
    """Pick zip / SHA256SUMS / detached signature assets from a GitHub release."""
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    checksums = None
    signature = None
    zips = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = _asset_name(asset).lower()
        if name in _CHECKSUM_FILENAMES:
            checksums = asset
        elif name in _SIG_FILENAMES:
            signature = asset
        elif name.endswith(".zip"):
            zips.append(asset)
    zip_asset = None
    for asset in zips:
        if _asset_name(asset).lower().startswith("whisperfastgui"):
            zip_asset = asset
            break
    if zip_asset is None and zips:
        zip_asset = zips[0]
    return {"zip": zip_asset, "checksums": checksums, "signature": signature}


def fetch_remote_app_version(timeout: int = 15) -> tuple[Optional[str], Optional[str]]:
    """Version and date from the latest GitHub Release (immutable tag)."""
    release = fetch_latest_github_release(timeout=timeout)
    if not release:
        return None, None
    version = _normalize_tag_version(release.get("tag_name") or "")
    if not version or version.lower() == "unknown":
        return None, None
    date = _format_release_date(release.get("published_at") or "")
    return version, date or None


def _is_unknown_version(value: Optional[str]) -> bool:
    return not value or str(value).strip().lower() == "unknown"


def _version_is_newer(remote: str, current: str) -> bool:
    if _is_unknown_version(remote) or _is_unknown_version(current):
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
    Compare the local version with the latest GitHub Release.
    Offers an update only if the release includes SHA256SUMS (ZIP path).
    """
    current = get_local_app_version()
    info = {
        "needs_update": False,
        "current": current,
        "remote": "",
        "remote_date": "",
        "url": GITHUB_URL,
    }
    if log_func:
        log_func(t("checking_app_update", url=GITHUB_URL))
    if _is_unknown_version(current):
        if log_func:
            log_func(t("app_update_local_version_unknown"))
        return info

    release = fetch_latest_github_release()
    if not release:
        if log_func:
            log_func(t("app_update_no_release"))
        return info

    remote = _normalize_tag_version(release.get("tag_name") or "")
    info["remote"] = remote or ""
    info["remote_date"] = _format_release_date(release.get("published_at") or "")
    if _is_unknown_version(remote):
        if log_func:
            log_func(t("app_update_check_failed"))
        return info

    if not _version_is_newer(remote, current):
        if log_func:
            log_func(t("app_update_ok", version=current))
        return info

    picked = _pick_release_assets(release)
    if not picked.get("checksums"):
        if log_func:
            log_func(t("app_update_checksum_missing"))
        return info

    info["needs_update"] = True
    if log_func:
        log_func(t("app_update_available", current=current, latest=remote))
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
            **win_no_window_kwargs(),
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
            **win_no_window_kwargs(),
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


def _http_download(url: str, dest: str, timeout: int = 180) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with open(dest, "wb") as out:
            shutil.copyfileobj(response, out)


def _signing_key_configured() -> bool:
    try:
        return os.path.isfile(_RELEASE_SIGNING_KEY) and os.path.getsize(_RELEASE_SIGNING_KEY) > 0
    except OSError:
        return False


def verify_detached_gpg_signature(data_path: str, sig_path: str, pubkey_path: str) -> Tuple[bool, str]:
    """Verify a detached ASCII/binary signature in an isolated GNUPGHOME."""
    gpg = shutil.which("gpg") or shutil.which("gpg.exe")
    if not gpg:
        return False, "gpg not found"
    homedir = tempfile.mkdtemp(prefix="wf_gpg_")
    try:
        imported = subprocess.run(
            [gpg, "--homedir", homedir, "--batch", "--yes", "--import", pubkey_path],
            capture_output=True,
            text=True,
            timeout=30,
            **win_no_window_kwargs(),
        )
        if imported.returncode != 0:
            err = (imported.stderr or imported.stdout or "").strip()
            return False, err or "gpg import failed"
        verified = subprocess.run(
            [gpg, "--homedir", homedir, "--batch", "--verify", sig_path, data_path],
            capture_output=True,
            text=True,
            timeout=30,
            **win_no_window_kwargs(),
        )
        if verified.returncode != 0:
            err = (verified.stderr or verified.stdout or "").strip()
            return False, err or "gpg verify failed"
        return True, ""
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    finally:
        shutil.rmtree(homedir, ignore_errors=True)


def _find_extracted_root(extract_dir: str) -> Optional[str]:
    try:
        entries = os.listdir(extract_dir)
    except OSError:
        return None
    for name in entries:
        candidate = os.path.join(extract_dir, name)
        if os.path.isdir(candidate) and name.lower().startswith("whisperfastgui"):
            return candidate
    return None


def _download_verified_release_zip(log_func: Callable[[str], None]) -> Optional[str]:
    """Download the latest GitHub Release ZIP, verify SHA-256 (and GPG if a key is bundled)."""
    log_func(t("app_updating_download"))
    release = fetch_latest_github_release()
    if not release:
        log_func(t("app_update_no_release"))
        return None
    picked = _pick_release_assets(release)
    checksums_asset = picked.get("checksums")
    zip_asset = picked.get("zip")
    sig_asset = picked.get("signature")
    if not checksums_asset or not checksums_asset.get("browser_download_url"):
        log_func(t("app_update_checksum_missing"))
        return None
    if not zip_asset or not zip_asset.get("browser_download_url"):
        log_func(t("app_update_error", error="no zip asset on GitHub Release"))
        return None

    staging_root = os.path.join(BASE_DIR, _UPDATE_STAGING_DIR)
    os.makedirs(staging_root, exist_ok=True)
    sums_path = os.path.join(staging_root, "SHA256SUMS")
    zip_name = _asset_name(zip_asset) or "release.zip"
    zip_path = os.path.join(staging_root, zip_name)
    try:
        _http_download(checksums_asset["browser_download_url"], sums_path)
        _http_download(zip_asset["browser_download_url"], zip_path)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log_func(t("app_update_error", error=str(e)))
        return None

    if _signing_key_configured():
        if not sig_asset or not sig_asset.get("browser_download_url"):
            log_func(t("app_update_signature_missing"))
            return None
        sig_path = os.path.join(staging_root, _asset_name(sig_asset) or "SHA256SUMS.asc")
        try:
            _http_download(sig_asset["browser_download_url"], sig_path)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log_func(t("app_update_error", error=str(e)))
            return None
        ok, err = verify_detached_gpg_signature(sums_path, sig_path, _RELEASE_SIGNING_KEY)
        if not ok:
            log_func(t("app_update_signature_failed", error=err))
            return None
        log_func(t("app_update_signature_ok"))

    try:
        with open(sums_path, "r", encoding="utf-8") as f:
            checksums = parse_sha256sums(f.read())
    except OSError as e:
        log_func(t("app_update_error", error=str(e)))
        return None
    expected = expected_digest_for_filename(checksums, zip_name)
    if not expected:
        log_func(t("app_update_checksum_missing"))
        return None
    if not verify_file_sha256(zip_path, expected):
        log_func(t("app_update_checksum_mismatch"))
        return None
    api_digest = (zip_asset.get("digest") or "").strip()
    if api_digest.lower().startswith("sha256:"):
        if api_digest.split(":", 1)[1].strip().lower() != expected:
            log_func(t("app_update_checksum_mismatch"))
            return None
    log_func(t("app_update_checksum_ok"))

    extract_dir = os.path.join(staging_root, "extracted")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)
    try:
        safe_extract_zip(zip_path, extract_dir)
    except (zipfile.BadZipFile, OSError, UnsafeArchiveMember) as e:
        log_func(t("app_update_error", error=str(e)))
        return None
    try:
        os.remove(zip_path)
    except OSError:
        pass
    root = _find_extracted_root(extract_dir)
    if root:
        return root
    # Zip of repo root (no wrapping folder)
    if os.path.isfile(os.path.join(extract_dir, "main.py")):
        return extract_dir
    return None


def _is_safe_update_entry_name(name: str) -> bool:
    """Reject names that cannot be a single relative path component."""
    if not name or name.startswith("."):
        return False
    if name in _PRESERVE_FILES or name == _UPDATE_STAGING_DIR:
        return False
    if os.path.sep in name or (os.path.altsep and os.path.altsep in name):
        return False
    if ".." in name:
        return False
    return True


def _copy_update_files(source_dir: str, log_func: Callable[[str], None]) -> int:
    copied = 0
    for name in os.listdir(source_dir):
        if not _is_safe_update_entry_name(name):
            continue
        src = os.path.join(source_dir, name)
        dst = os.path.join(BASE_DIR, name)
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


_APPLY_UPDATE_PY = os.path.join(BASE_DIR, "_apply_update.py")


def _write_apply_update_py(source_dir: str) -> str:
    """Self-contained copier — no shell interpolation of archive filenames."""
    script = (
        '"""Apply a staged WhisperFastGUI zip update. Generated; do not edit."""\n'
        "import os\n"
        "import shutil\n"
        "import sys\n"
        f"SRC = {os.path.abspath(source_dir)!r}\n"
        f"DST = {os.path.abspath(BASE_DIR)!r}\n"
        f"PRESERVE = {set(_PRESERVE_FILES)!r}\n"
        f"STAGING_NAME = {_UPDATE_STAGING_DIR!r}\n"
        "\n"
        "def _ok(name):\n"
        "    if not name or name.startswith('.'):\n"
        "        return False\n"
        "    if name in PRESERVE or name == STAGING_NAME:\n"
        "        return False\n"
        "    if os.path.sep in name or (os.path.altsep and os.path.altsep in name):\n"
        "        return False\n"
        "    if '..' in name:\n"
        "        return False\n"
        "    return True\n"
        "\n"
        "def main():\n"
        "    if not os.path.isdir(SRC):\n"
        "        sys.exit(1)\n"
        "    for name in os.listdir(SRC):\n"
        "        if not _ok(name):\n"
        "            continue\n"
        "        src = os.path.join(SRC, name)\n"
        "        dst = os.path.join(DST, name)\n"
        "        try:\n"
        "            if os.path.isdir(src):\n"
        "                if os.path.basename(src) == '__pycache__':\n"
        "                    continue\n"
        "                if os.path.isdir(dst):\n"
        "                    shutil.rmtree(dst, ignore_errors=True)\n"
        "                shutil.copytree(\n"
        "                    src, dst,\n"
        "                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),\n"
        "                )\n"
        "            else:\n"
        "                shutil.copy2(src, dst)\n"
        "        except OSError:\n"
        "            pass\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    with open(_APPLY_UPDATE_PY, "w", encoding="utf-8", newline="\n") as f:
        f.write(script)
    return _APPLY_UPDATE_PY


def _write_restart_script(source_dir: str) -> str:
    """Створює скрипт, який застосує оновлення після закриття програми.

    Імена файлів з ZIP не потрапляють у bat/sh — копіювання робить
    згенерований ``_apply_update.py`` через shutil.
    """
    apply_py = _write_apply_update_py(source_dir)
    python_line = subprocess.list2cmdline([sys.executable, apply_py])
    launcher_line = subprocess.list2cmdline(_launcher_command())

    if sys.platform == "win32":
        script_path = os.path.join(BASE_DIR, "_apply_update.bat")
        lines = [
            "@echo off",
            'cd /d "%~dp0"',
            "timeout /t 2 /nobreak >nul",
            python_line,
            f'start "" {launcher_line}',
            f'rmdir /s /q "{_UPDATE_STAGING_DIR}"',
            f'del /q "{os.path.basename(apply_py)}" >nul 2>&1',
            'del "%~f0"',
        ]
        with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\r\n".join(lines) + "\r\n")
        return script_path

    script_path = os.path.join(BASE_DIR, "_apply_update.sh")
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/bin/sh\n")
        f.write('cd "$(dirname "$0")"\n')
        f.write("sleep 2\n")
        f.write(f"{python_line}\n")
        f.write(f"{launcher_line} &\n")
        f.write(f'rm -rf "{_UPDATE_STAGING_DIR}"\n')
        f.write(f'rm -f -- "{os.path.basename(apply_py)}"\n')
        f.write('rm -- "$0"\n')
    os.chmod(script_path, 0o755)
    return script_path


def _update_via_zip(log_func: Callable[[str], None]) -> tuple[bool, Optional[str]]:
    source_dir = _download_verified_release_zip(log_func)
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
    Оновлює програму: git pull (якщо є .git) або ZIP з GitHub Release
    після перевірки SHA-256 (і GPG, якщо в resources/ є ключ).
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

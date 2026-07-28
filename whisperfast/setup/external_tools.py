"""Hints and update checks for system tools that cannot be installed via pip (FFmpeg, Pandoc)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Callable, List, Optional, TypedDict

from whisperfast.i18n import t
from whisperfast.platform_util import win_no_window_kwargs

try:
    from packaging.version import Version
except ImportError:
    Version = None

LogFunc = Callable[..., None]

# External CLI tools. github_repo → compare installed version to latest release.
# FFmpeg has no reliable GitHub Releases feed, so we only flag if missing.
_EXTERNAL_TOOLS = (
    {
        "name": "pandoc",
        "display": "Pandoc",
        "github_repo": "jgm/pandoc",
        "howto": "pandoc",
    },
    {
        "name": "ffmpeg",
        "display": "FFmpeg",
        "github_repo": None,
        "howto": "ffmpeg",
    },
)


class ExternalToolUpdate(TypedDict, total=False):
    name: str
    display: str
    current: Optional[str]
    latest: Optional[str]
    needs_update: bool
    missing: bool
    howto: str


def log_ffmpeg_install_howto(log_func: LogFunc) -> None:
    """Print platform-specific FFmpeg install instructions."""
    log_func(t("ffmpeg_install_howto_title"))
    log_func(t("ffmpeg_install_howto_url"))
    if sys.platform == "win32":
        log_func(t("ffmpeg_install_howto_win_winget"))
        log_func(t("ffmpeg_install_howto_win_choco"))
    elif sys.platform == "darwin":
        log_func(t("ffmpeg_install_howto_mac"))
    else:
        log_func(t("ffmpeg_install_howto_linux"))
    log_func(t("external_tool_path_hint"))


def log_pandoc_install_howto(log_func: LogFunc) -> None:
    """Print platform-specific Pandoc install instructions (MD → Word)."""
    log_func(t("pandoc_install_howto_title"))
    log_func(t("pandoc_install_howto_url"))
    if sys.platform == "win32":
        log_func(t("pandoc_install_howto_win_winget"))
        log_func(t("pandoc_install_howto_win_choco"))
        log_func(t("pandoc_install_howto_win_msi"))
    elif sys.platform == "darwin":
        log_func(t("pandoc_install_howto_mac"))
    else:
        log_func(t("pandoc_install_howto_linux"))
    log_func(t("external_tool_path_hint"))
    log_func(t("pandoc_install_howto_restart"))


def log_external_tool_howto(tool_name: str, log_func: LogFunc) -> None:
    """Print install/update instructions for a known external tool."""
    if tool_name == "pandoc":
        log_pandoc_install_howto(log_func)
    elif tool_name == "ffmpeg":
        log_ffmpeg_install_howto(log_func)


def ffmpeg_version() -> Optional[str]:
    """First line of `ffmpeg -version`, or None if FFmpeg is missing / fails."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (result.stdout or result.stderr or "").strip().splitlines()
    return first[0].strip() if first else "ffmpeg"


def parse_tool_version(text: Optional[str]) -> Optional[str]:
    """Extract a comparable version string from tool output or a GitHub tag."""
    if not text:
        return None
    cleaned = text.strip()
    # FFmpeg tags often look like n7.1 or n6.1.1
    if cleaned.lower().startswith("n") and cleaned[1:2].isdigit():
        cleaned = cleaned[1:]
    cleaned = cleaned.lstrip("vV")
    m = re.search(r"(\d+(?:\.\d+)+)", cleaned)
    return m.group(1) if m else None


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


def fetch_latest_github_release_tag(repo: str, timeout: int = 15) -> Optional[str]:
    """Return tag_name of the latest GitHub release, or None on failure."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "WhisperFastGUI-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    tag = (data.get("tag_name") or "").strip()
    return tag or None


def _local_tool_version_line(name: str) -> Optional[str]:
    if name == "pandoc":
        from whisperfast.core.pandoc_export import pandoc_version

        return pandoc_version()
    if name == "ffmpeg":
        return ffmpeg_version()
    return None


def check_external_tool_updates(
    log_func: Optional[LogFunc] = None,
) -> List[ExternalToolUpdate]:
    """
    Compare installed Pandoc/FFmpeg versions with the latest GitHub releases.
    Returns only tools that are missing or outdated (needs_update).
    """
    if log_func:
        log_func(t("checking_external_tools_update"))

    found: List[ExternalToolUpdate] = []
    for spec in _EXTERNAL_TOOLS:
        name = spec["name"]
        display = spec["display"]
        local_line = _local_tool_version_line(name)
        current = parse_tool_version(local_line)
        missing = local_line is None

        latest = None
        repo = spec.get("github_repo")
        if repo:
            tag = fetch_latest_github_release_tag(repo)
            latest = parse_tool_version(tag)

        entry: ExternalToolUpdate = {
            "name": name,
            "display": display,
            "current": current,
            "latest": latest,
            "needs_update": False,
            "missing": missing,
            "howto": spec["howto"],
        }

        if missing:
            entry["needs_update"] = True
            found.append(entry)
            if log_func:
                if latest:
                    log_func(t("external_tool_missing", tool=display, latest=latest))
                else:
                    log_func(t("external_tool_missing_no_latest", tool=display))
            continue

        if not repo:
            if log_func:
                log_func(t("external_tool_ok", tool=display, version=current or local_line or "?"))
            continue

        if not latest:
            if log_func:
                log_func(
                    t(
                        "external_tool_check_failed",
                        tool=display,
                        version=current or local_line or "?",
                    )
                )
            continue

        if current and _version_is_newer(latest, current):
            entry["needs_update"] = True
            found.append(entry)
            if log_func:
                log_func(t("external_tool_update", tool=display, current=current, latest=latest))
        elif log_func:
            log_func(t("external_tool_ok", tool=display, version=current or local_line or "?"))

    return found


def log_external_tools_status(log_func: LogFunc) -> None:
    """Check FFmpeg and Pandoc; if missing, print install howto."""
    from whisperfast.core.pandoc_export import is_pandoc_available, pandoc_version

    ff_ver = ffmpeg_version()
    if ff_ver:
        parsed = parse_tool_version(ff_ver)
        log_func(t("ffmpeg_found_version", version=parsed or ff_ver))
    else:
        log_func(t("ffmpeg_not_found"))
        log_func(t("ffmpeg_required"))
        log_ffmpeg_install_howto(log_func)

    if is_pandoc_available():
        log_func(t("pandoc_found", version=pandoc_version() or "pandoc"))
    else:
        log_func(t("pandoc_not_found"))
        log_func(t("pandoc_required"))
        log_pandoc_install_howto(log_func)


def pandoc_missing_dialog_text() -> str:
    """Long message for messagebox when enabling MD → Word without Pandoc."""
    lines = [t("pandoc_missing_prompt"), "", t("pandoc_install_howto_url")]
    if sys.platform == "win32":
        lines.extend([
            "",
            t("pandoc_install_howto_win_winget"),
            t("pandoc_install_howto_win_choco"),
            t("pandoc_install_howto_win_msi"),
        ])
    elif sys.platform == "darwin":
        lines.extend(["", t("pandoc_install_howto_mac")])
    else:
        lines.extend(["", t("pandoc_install_howto_linux")])
    lines.extend(["", t("external_tool_path_hint"), t("pandoc_install_howto_restart")])
    return "\n".join(lines)

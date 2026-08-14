"""FFmpeg / Pandoc: check, PATH refresh, and install (winget/choco/brew or GitHub zip)."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Dict, Iterable, List, Optional, Sequence, TypedDict

from whisperfast.config import BASE_DIR
from whisperfast.i18n import t
from whisperfast.platform_util import run_logged_command, win_no_window_kwargs

try:
    from packaging.version import Version
except ImportError:
    Version = None

try:
    import tarfile
except ImportError:
    tarfile = None

LogFunc = Callable[..., None]
TOOLS_DIR = os.path.join(BASE_DIR, "tools")

# External CLI tools. github_repo → compare installed version to latest release.
# FFmpeg GitHub feed is noisy (BtbN nightlies); we still use it as a zip fallback.
_EXTERNAL_TOOLS = (
    {
        "name": "pandoc",
        "display": "Pandoc",
        "github_repo": "jgm/pandoc",
        "howto": "pandoc",
        "winget_id": "JohnMacFarlane.Pandoc",
        "choco_id": "pandoc",
        "brew": "pandoc",
        "apt": "pandoc",
        "exe": "pandoc",
    },
    {
        "name": "ffmpeg",
        "display": "FFmpeg",
        "github_repo": None,
        "howto": "ffmpeg",
        "winget_id": "Gyan.FFmpeg",
        "choco_id": "ffmpeg",
        "brew": "ffmpeg",
        "apt": "ffmpeg",
        "exe": "ffmpeg",
        "ffmpeg_zip_repo": "BtbN/FFmpeg-Builds",
    },
)

_PATH_REFRESHED = False


class ExternalToolUpdate(TypedDict, total=False):
    name: str
    display: str
    current: Optional[str]
    latest: Optional[str]
    needs_update: bool
    missing: bool
    howto: str


def _tool_exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _extra_bin_dirs() -> List[str]:
    dirs: List[str] = [
        os.path.join(TOOLS_DIR, "pandoc"),
        os.path.join(TOOLS_DIR, "ffmpeg"),
        os.path.join(TOOLS_DIR, "ffmpeg", "bin"),
    ]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        dirs.extend([
            os.path.join(local, "Pandoc") if local else "",
            os.path.join(pf, "Pandoc"),
            os.path.join(pf86, "Pandoc"),
            os.path.join(local, "Microsoft", "WinGet", "Links") if local else "",
            os.path.join(local, "Microsoft", "WindowsApps") if local else "",
            os.path.join(pf, "ffmpeg", "bin"),
            os.path.join(pf, "FFmpeg", "bin"),
            os.path.join(pf86, "ffmpeg", "bin"),
            r"C:\ffmpeg\bin",
        ])
    elif sys.platform == "darwin":
        dirs.extend(["/opt/homebrew/bin", "/usr/local/bin"])
    else:
        dirs.extend(["/usr/local/bin", os.path.expanduser("~/.local/bin")])
    return [d for d in dirs if d and os.path.isdir(d)]


def refresh_os_path(force: bool = False) -> None:
    """Reload PATH from the OS (Windows registry) and known tool folders."""
    global _PATH_REFRESHED
    if _PATH_REFRESHED and not force:
        return
    parts: List[str] = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        raw, typ = winreg.QueryValueEx(key, "Path")
                    if typ == getattr(winreg, "REG_EXPAND_SZ", 2):
                        raw = os.path.expandvars(raw)
                    if raw:
                        parts.extend(p for p in str(raw).split(os.pathsep) if p)
                except OSError:
                    pass
        except ImportError:
            pass
    current = os.environ.get("PATH", "").split(os.pathsep)
    merged: List[str] = []
    seen = set()
    for p in _extra_bin_dirs() + parts + current:
        if not p:
            continue
        n = os.path.normcase(os.path.normpath(p))
        if n in seen:
            continue
        seen.add(n)
        merged.append(p)
    os.environ["PATH"] = os.pathsep.join(merged)
    _PATH_REFRESHED = True


def find_tool_exe(name: str) -> Optional[str]:
    """Absolute path to a CLI tool (pandoc, ffmpeg, ffprobe), or None."""
    refresh_os_path()
    found = shutil.which(name)
    if found:
        return found
    exe = _tool_exe_name(name)
    for folder in _extra_bin_dirs():
        candidate = os.path.join(folder, exe)
        if os.path.isfile(candidate):
            return candidate
    return None


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


def _tool_version_line(exe: str, version_flag: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [exe, version_flag],
            capture_output=True,
            text=True,
            timeout=15,
            **win_no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (result.stdout or result.stderr or "").strip().splitlines()
    return first[0].strip() if first else os.path.basename(exe)


def ffmpeg_version() -> Optional[str]:
    """First line of `ffmpeg -version`, or None if FFmpeg is missing / fails."""
    exe = find_tool_exe("ffmpeg")
    if not exe:
        return None
    return _tool_version_line(exe, "-version")


def pandoc_version() -> Optional[str]:
    """First line of `pandoc -v`, or None if Pandoc is missing / fails."""
    exe = find_tool_exe("pandoc")
    if not exe:
        return None
    line = _tool_version_line(exe, "-v")
    if line:
        return line
    return None


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


def _github_request(url: str, timeout: int = 20):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "WhisperFastGUI-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def fetch_latest_github_release(repo: str, timeout: int = 20) -> Optional[dict]:
    """Latest GitHub release JSON, or None on failure."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        data = _github_request(url, timeout=timeout)
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fetch_latest_github_release_tag(repo: str, timeout: int = 15) -> Optional[str]:
    """Return tag_name of the latest GitHub release, or None on failure."""
    data = fetch_latest_github_release(repo, timeout=timeout)
    if not data:
        return None
    tag = (data.get("tag_name") or "").strip()
    return tag or None


def _local_tool_version_line(name: str) -> Optional[str]:
    if name == "pandoc":
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
    refresh_os_path(force=True)
    ff_ver = ffmpeg_version()
    if ff_ver:
        parsed = parse_tool_version(ff_ver)
        log_func(t("ffmpeg_found_version", version=parsed or ff_ver))
    else:
        log_func(t("ffmpeg_not_found"))
        log_func(t("ffmpeg_required"))
        log_ffmpeg_install_howto(log_func)

    pd_ver = pandoc_version()
    if pd_ver:
        log_func(t("pandoc_found", version=pd_ver))
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


def _find_manager(name: str) -> Optional[str]:
    refresh_os_path()
    return shutil.which(name) or find_tool_exe(name)


def _cmd_ok(code: Optional[int]) -> bool:
    # winget: 0 success; -1978335189 already installed
    if code == 0:
        return True
    if sys.platform == "win32" and code in (-1978335189, 0x8A15000D):
        return True
    return False


def _run_manager_cmd(cmd: Sequence[str], log_func: LogFunc, timeout: int = 180) -> bool:
    log_func(t("install_running_cmd", cmd=" ".join(str(c) for c in cmd)))
    code = run_logged_command(list(cmd), log_func=log_func, timeout=timeout)
    return _cmd_ok(code)


def _install_via_winget(spec: dict, upgrade: bool, log_func: LogFunc) -> bool:
    winget = _find_manager("winget")
    if not winget:
        return False
    action = "upgrade" if upgrade and find_tool_exe(spec["exe"]) else "install"
    base = [
        winget,
        action,
        "--id",
        spec["winget_id"],
        "-e",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    if action == "install":
        user_cmd = base + ["--scope", "user"]
        if _run_manager_cmd(user_cmd, log_func):
            return True
    if _run_manager_cmd(base, log_func):
        return True
    # Older winget without --disable-interactivity
    fallback = [c for c in base if c != "--disable-interactivity"]
    return _run_manager_cmd(fallback, log_func)


def _install_via_choco(spec: dict, upgrade: bool, log_func: LogFunc) -> bool:
    choco = _find_manager("choco")
    if not choco:
        return False
    action = "upgrade" if upgrade else "install"
    return _run_manager_cmd([choco, action, spec["choco_id"], "-y"], log_func)


def _install_via_brew(spec: dict, upgrade: bool, log_func: LogFunc) -> bool:
    brew = _find_manager("brew")
    if not brew:
        return False
    if upgrade:
        return _run_manager_cmd([brew, "upgrade", spec["brew"]], log_func) or _run_manager_cmd(
            [brew, "install", spec["brew"]], log_func
        )
    return _run_manager_cmd([brew, "install", spec["brew"]], log_func)


def _machine_tag() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine or "x86_64"


def _pick_github_asset(assets: Iterable[dict], name: str) -> Optional[dict]:
    machine = _machine_tag()
    items = []
    for asset in assets:
        aname = (asset.get("name") or "").lower()
        url = asset.get("browser_download_url") or ""
        if aname and url:
            items.append((aname, asset))

    def match(*needles: str, exclude: Sequence[str] = ()) -> Optional[dict]:
        for aname, asset in items:
            if aname.endswith(".asc") or aname.endswith(".sig"):
                continue
            if any(x in aname for x in exclude):
                continue
            if all(n in aname for n in needles):
                return asset
        return None

    if name == "pandoc":
        if sys.platform == "win32":
            return match("windows", "x86_64", ".zip") or match("windows", ".zip")
        if sys.platform == "darwin":
            arch = "arm64" if machine == "arm64" else "x86_64"
            return match(arch.lower(), "macos", ".zip") or match("macos", ".zip")
        arch = "arm64" if machine == "arm64" else "amd64"
        return match("linux", arch, ".tar.gz") or match("linux", ".tar.gz")

    if name == "ffmpeg":
        if sys.platform == "win32":
            return match("win64", "gpl", ".zip", exclude=("shared",))
        if sys.platform.startswith("linux"):
            arch = "linuxarm64" if machine == "arm64" else "linux64"
            return match(arch, "gpl", ".tar.xz", exclude=("shared",)) or match(
                arch, "gpl", ".tar.gz", exclude=("shared",)
            )
    return None


def _copy_tool_binaries(src_dir: str, dest_dir: str, exe_name: str) -> Optional[str]:
    os.makedirs(dest_dir, exist_ok=True)
    wanted = _tool_exe_name(exe_name)
    copied_main = None
    extras = {"ffprobe", "ffprobe.exe", "ffplay", "ffplay.exe", "pandoc-lua", "pandoc-lua.exe"}
    for fname in os.listdir(src_dir):
        src = os.path.join(src_dir, fname)
        if not os.path.isfile(src):
            continue
        low = fname.lower()
        if fname == wanted or low in extras:
            dest = os.path.join(dest_dir, fname)
            shutil.copy2(src, dest)
            if fname == wanted:
                copied_main = dest
    return copied_main


def _extract_tool_archive(archive_path: str, dest_dir: str, exe_name: str) -> Optional[str]:
    tmp = tempfile.mkdtemp(prefix="wf_tool_")
    try:
        lower = archive_path.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(tmp)
        elif tarfile is not None and (
            lower.endswith(".tar.gz") or lower.endswith(".tgz") or lower.endswith(".tar.xz")
        ):
            with tarfile.open(archive_path) as tf:
                tf.extractall(tmp)
        else:
            return None
        wanted = _tool_exe_name(exe_name)
        found = None
        for root, _dirs, files in os.walk(tmp):
            if wanted in files:
                found = os.path.join(root, wanted)
                break
        if not found:
            return None
        return _copy_tool_binaries(os.path.dirname(found), dest_dir, exe_name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _download_url(url: str, dest: str, log_func: LogFunc) -> bool:
    log_func(t("external_tool_downloading", url=url))
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "WhisperFastGUI-Updater"})
        with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as out:
            shutil.copyfileobj(response, out)
        return True
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log_func(t("external_tool_download_failed", error=str(e)))
        return False


def _install_via_github_zip(spec: dict, log_func: LogFunc) -> bool:
    name = spec["name"]
    repo = spec.get("github_repo") or spec.get("ffmpeg_zip_repo")
    if not repo:
        return False
    if name == "ffmpeg" and sys.platform == "darwin":
        return False
    data = fetch_latest_github_release(repo)
    if not data:
        log_func(t("external_tool_download_failed", error=repo))
        return False
    asset = _pick_github_asset(data.get("assets") or [], name)
    url = (asset or {}).get("browser_download_url") if asset else None
    if not url:
        log_func(t("external_tool_no_asset", tool=spec["display"]))
        return False
    dest_dir = os.path.join(TOOLS_DIR, name)
    os.makedirs(TOOLS_DIR, exist_ok=True)
    suffix = os.path.splitext(url.split("?")[0])[-1] or ".zip"
    if url.endswith(".tar.gz"):
        suffix = ".tar.gz"
    elif url.endswith(".tar.xz"):
        suffix = ".tar.xz"
    tmp_path = os.path.join(tempfile.gettempdir(), f"wf_{name}_dl{suffix}")
    try:
        if not _download_url(url, tmp_path, log_func):
            return False
        exe_path = _extract_tool_archive(tmp_path, dest_dir, spec["exe"])
        if not exe_path:
            log_func(t("external_tool_extract_failed", tool=spec["display"]))
            return False
        log_func(t("external_tool_local_ok", tool=spec["display"], path=exe_path))
        return True
    finally:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _tool_is_available(name: str) -> bool:
    refresh_os_path(force=True)
    return find_tool_exe(name) is not None


def _install_one_tool(spec: dict, upgrade: bool, log_func: LogFunc) -> bool:
    display = spec["display"]
    was_present = find_tool_exe(spec["exe"]) is not None
    log_func(t("external_tool_installing", tool=display))
    if sys.platform == "win32":
        _install_via_winget(spec, upgrade, log_func) or _install_via_choco(spec, upgrade, log_func)
    else:
        _install_via_brew(spec, upgrade, log_func)

    refresh_os_path(force=True)
    if _tool_is_available(spec["exe"]):
        ver = parse_tool_version(_local_tool_version_line(spec["name"])) or spec["exe"]
        log_func(t("external_tool_install_ok", tool=display, version=ver))
        return True

    if was_present:
        log_func(t("external_tool_keep_current", tool=display))
        return True

    log_func(t("external_tool_trying_github", tool=display))
    if _install_via_github_zip(spec, log_func):
        refresh_os_path(force=True)
        if _tool_is_available(spec["exe"]):
            ver = parse_tool_version(_local_tool_version_line(spec["name"])) or spec["exe"]
            log_func(t("external_tool_install_ok", tool=display, version=ver))
            return True

    log_func(t("external_tool_install_failed", tool=display))
    log_external_tool_howto(spec["howto"], log_func)
    return False


def install_external_tools(
    log_func: LogFunc,
    names: Optional[Sequence[str]] = None,
    *,
    missing_only: bool = True,
    upgrade: bool = False,
) -> Dict[str, bool]:
    """Install or upgrade system tools (Pandoc, FFmpeg). Not pip packages."""
    wanted = [n.lower() for n in names] if names else [s["name"] for s in _EXTERNAL_TOOLS]
    results: Dict[str, bool] = {}
    log_func(t("install_external_tools_start"))
    for spec in _EXTERNAL_TOOLS:
        name = spec["name"]
        if name not in wanted:
            continue
        present = find_tool_exe(spec["exe"]) is not None
        if present and missing_only and not upgrade:
            line = _local_tool_version_line(name)
            ver = parse_tool_version(line) or line or name
            log_func(t("external_tool_already", tool=spec["display"], version=ver))
            results[name] = True
            continue
        if present and upgrade:
            results[name] = _install_one_tool(spec, upgrade=True, log_func=log_func)
            continue
        if not present:
            results[name] = _install_one_tool(spec, upgrade=False, log_func=log_func)
    bind_runtime_tool_paths()
    return results


def bind_runtime_tool_paths() -> None:
    """Put local/system FFmpeg on PATH and point pydub at the real binaries."""
    refresh_os_path(force=True)
    ffmpeg = find_tool_exe("ffmpeg")
    if not ffmpeg:
        return
    try:
        from pydub import AudioSegment

        AudioSegment.converter = ffmpeg
        AudioSegment.ffmpeg = ffmpeg
        ffprobe = find_tool_exe("ffprobe")
        if ffprobe:
            AudioSegment.ffprobe = ffprobe
    except Exception:
        pass

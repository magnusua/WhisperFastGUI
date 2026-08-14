#!/usr/bin/env python3
"""Build WhisperFastGUI-{version}-src.zip and GNU SHA256SUMS for a GitHub Release.

Usage:
  python scripts/make_release_checksums.py --zip-repo [--version 1.2.11] [--out dist]
  python scripts/make_release_checksums.py [--out DIR] FILE [FILE ...]

Does not create or require a GPG private key. Optional signing is done in CI
when the GPG_PRIVATE_KEY secret is present.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from whisperfast.config import README_PATH, parse_app_metadata  # noqa: E402
from whisperfast.updates.checksums import sha256_file  # noqa: E402

EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".idea",
        ".vscode",
        ".cursor",
        "tools",
        "_update_staging",
    }
)
EXCLUDE_FILE_NAMES = frozenset(
    {
        "settings.json",
        "request_queue.json",
        "app_log.json",
        "app_log.json.tmp",
        ".whisperfastgui.pid",
        ".whisperfastgui.pid.tmp",
        "_apply_update.bat",
        "_apply_update.sh",
        "_apply_update.py",
    }
)


def read_readme_version() -> str:
    with open(README_PATH, "r", encoding="utf-8") as f:
        version, _ = parse_app_metadata(f.read())
    if not version or version.lower() == "unknown":
        raise SystemExit("Could not parse **Версія:** from README.md")
    return version


def should_skip(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in EXCLUDE_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    if name in EXCLUDE_FILE_NAMES:
        return True
    if name.endswith((".pyc", ".pyo", ".pyd")):
        return True
    return False


def iter_repo_files(repo_root: str):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, repo_root)
            if should_skip(rel):
                continue
            yield full, rel.replace("\\", "/")


def make_source_zip(repo_root: str, zip_path: str, version: str) -> None:
    wrap = f"WhisperFastGUI-{version}"
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for full, rel in iter_repo_files(repo_root):
            zf.write(full, f"{wrap}/{rel}")


def write_sha256sums(paths: list, out_path: str) -> None:
    lines = []
    for path in paths:
        digest = sha256_file(path)
        lines.append(f"{digest}  {os.path.basename(path)}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Existing files to hash")
    parser.add_argument("--zip-repo", action="store_true", help="Pack the repository into a source ZIP")
    parser.add_argument("--version", default="", help="Release version (default: README)")
    parser.add_argument("--out", default="dist", help="Output directory")
    parser.add_argument("--repo", default=ROOT, help="Repository root")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    hashed = list(args.files)

    if args.zip_repo:
        version = (args.version or "").strip() or read_readme_version()
        if version.lower().startswith("v") and version[1:2].isdigit():
            version = version[1:]
        zip_path = os.path.join(out_dir, f"WhisperFastGUI-{version}-src.zip")
        make_source_zip(args.repo, zip_path, version)
        hashed.append(zip_path)

    if not hashed:
        parser.error("pass FILE(s) and/or --zip-repo")

    write_sha256sums(hashed, os.path.join(out_dir, "SHA256SUMS"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

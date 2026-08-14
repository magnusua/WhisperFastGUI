"""Safe archive extraction — reject zip-slip / tar-slip paths."""
from __future__ import annotations

import os
import zipfile
from typing import Optional

try:
    import tarfile
except ImportError:
    tarfile = None


class UnsafeArchiveMember(ValueError):
    """Archive entry would extract outside the destination directory."""


def is_within_directory(directory: str, target: str) -> bool:
    directory = os.path.abspath(directory)
    target = os.path.abspath(target)
    try:
        return os.path.commonpath([directory, target]) == directory
    except ValueError:
        return False


def archive_member_destination(dest_dir: str, member_name: str) -> Optional[str]:
    """Absolute extract path if ``member_name`` stays inside dest_dir, else None."""
    if not member_name or "\x00" in member_name:
        return None
    parts = member_name.replace("\\", "/").split("/")
    if not parts or parts[0] == "":
        return None
    first = parts[0]
    if len(first) >= 2 and first[1] == ":":
        return None
    if ".." in parts:
        return None
    dest_dir = os.path.abspath(dest_dir)
    target = os.path.abspath(os.path.join(dest_dir, *parts))
    if not is_within_directory(dest_dir, target):
        return None
    return target


def safe_extract_zip(archive_path: str, dest_dir: str) -> None:
    dest_dir = os.path.abspath(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if archive_member_destination(dest_dir, info.filename) is None:
                raise UnsafeArchiveMember(info.filename)
        for info in zf.infolist():
            zf.extract(info, dest_dir)
            target = os.path.abspath(os.path.join(dest_dir, info.filename))
            if not is_within_directory(dest_dir, target):
                raise UnsafeArchiveMember(info.filename)


def safe_extract_tar(archive_path: str, dest_dir: str) -> None:
    if tarfile is None:
        raise UnsafeArchiveMember("tarfile module is not available")
    dest_dir = os.path.abspath(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    with tarfile.open(archive_path) as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise UnsafeArchiveMember(member.name)
            if archive_member_destination(dest_dir, member.name) is None:
                raise UnsafeArchiveMember(member.name)
        tf.extractall(dest_dir)
        for member in tf.getmembers():
            target = os.path.abspath(os.path.join(dest_dir, member.name))
            if not is_within_directory(dest_dir, target):
                raise UnsafeArchiveMember(member.name)

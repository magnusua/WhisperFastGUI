"""CLI: sessions, process <folder>, record start|stop."""
from __future__ import annotations

import os
import sys
from typing import List

from whisperfast.core.ipc_cmd import write_command
from whisperfast.core.session_store import list_session_folders, load_meta
from whisperfast.library import get_library
from whisperfast.single_instance import find_other_instance_pid


def maybe_run_cli(argv: List[str]) -> bool:
    if len(argv) < 2:
        return False
    cmd = argv[1].strip().lower()
    if cmd not in ("sessions", "process", "record", "--telegram", "telegram"):
        return False
    if cmd in ("--telegram", "telegram"):
        from whisperfast.telegram.worker import run_from_settings

        code = run_from_settings()
        if code:
            sys.exit(code)
        return True
    if cmd == "sessions":
        return _cmd_sessions()
    if cmd == "process":
        folder = argv[2] if len(argv) > 2 else ""
        return _cmd_process(folder)
    if cmd == "record":
        action = argv[2].strip().lower() if len(argv) > 2 else ""
        return _cmd_record(action)
    return False


def _cmd_sessions() -> bool:
    from whisperfast.core.capture import default_capture_dir

    folders = list(list_session_folders(default_capture_dir()))
    try:
        lib = get_library()
        jobs = lib.list_jobs(limit=50)
    except Exception:
        jobs = []
    print("sessions:")
    for folder in folders:
        meta = load_meta(folder)
        title = meta.get("calendar_title") or os.path.basename(folder)
        print(f"  {folder}  {title}")
    if jobs:
        print("archive:")
        for job in jobs:
            print(f"  {job.get('id')}  {job.get('name')}  {job.get('status')}")
    if not folders and not jobs:
        print("  (none)")
    return True


def _cmd_process(folder: str) -> bool:
    folder = os.path.abspath(folder or "")
    if not folder or not os.path.isdir(folder):
        print("usage: process <folder>", file=sys.stderr)
        sys.exit(2)
    other = find_other_instance_pid()
    if other:
        write_command("process", folder=folder)
        print(f"queued folder for running instance (pid {other})")
        return True
    # Headless: add to request queue file and start GUI --transcribe
    from whisperfast.core.input_files import get_valid_files_from_directory
    from whisperfast.core.queue_manager import QueueController
    from whisperfast.utils import make_queue_item

    files = get_valid_files_from_directory(folder)
    if not files:
        print("no media files in folder")
        return True
    ctrl = QueueController()
    ctrl.load_from_file()
    existing = {item.get("path") for item in ctrl.queue}
    for path in files:
        if path not in existing:
            ctrl.queue.append(make_queue_item(path))
    ctrl.save_to_file()
    print(f"queued {len(files)} file(s); starting GUI")
    sys.argv = [sys.argv[0], "--transcribe"]
    return False  # continue into GUI


def _cmd_record(action: str) -> bool:
    if action not in ("start", "stop"):
        print("usage: record start|stop", file=sys.stderr)
        sys.exit(2)
    other = find_other_instance_pid()
    if other:
        write_command("record_start" if action == "start" else "record_stop")
        print(f"sent record {action} to pid {other}")
        return True
    if action == "stop":
        print("no running instance")
        return True
    sys.argv = [sys.argv[0], "--record"]
    return False

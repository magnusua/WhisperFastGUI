"""Open a file or reveal it in the system file manager."""
import os
import subprocess
import sys


def open_file(path: str) -> bool:
    """Open path with the default application. Returns True if launched."""
    if not path or not os.path.exists(path):
        return False
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)
    return True


def open_file_location(path: str) -> bool:
    """Reveal path in the file manager (select file when supported).

    Important (Windows): do NOT use CREATE_NO_WINDOW / SW_HIDE — that hides Explorer.
    """
    if not path:
        return False
    path = os.path.abspath(os.path.normpath(path.strip().strip('"')))
    if not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent and os.path.isdir(parent):
            path = parent
        else:
            return False

    if sys.platform == "win32":
        if os.path.isdir(path):
            # Visible Explorer window — no CREATE_NO_WINDOW / SW_HIDE
            subprocess.Popen(["explorer", path])
        else:
            # Classic form: separate "/select," and path (spaces OK)
            subprocess.Popen(["explorer", "/select,", path])
        return True

    if sys.platform == "darwin":
        if os.path.isdir(path):
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["open", "-R", path], check=False)
        return True

    target = path if os.path.isdir(path) else (os.path.dirname(path) or path)
    subprocess.run(["xdg-open", target], check=False)
    return True

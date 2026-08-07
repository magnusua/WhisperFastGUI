"""Move processed source files next to their outputs and sync queue/log paths."""
from __future__ import annotations

import os
import shutil
from typing import Optional, Tuple


def unique_dest_path(dest_dir: str, basename: str) -> str:
    """Return dest_dir/basename, or name_1.ext / name_2.ext if taken."""
    dest_dir = os.path.abspath(dest_dir)
    candidate = os.path.join(dest_dir, basename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(basename)
    n = 1
    while True:
        candidate = os.path.join(dest_dir, f"{stem}_{n}{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def move_source_to_output_dir(source_path: str, output_dir: str) -> Tuple[str, bool, Optional[str]]:
    """
    Move source into output_dir if it is not already there.

    Returns (final_path, moved, error_message_or_None).
    Same-directory → no-op (moved=False).
    """
    if not source_path:
        return source_path, False, None
    try:
        source_path = os.path.abspath(source_path)
        output_dir = os.path.abspath(output_dir)
    except OSError as e:
        return source_path, False, str(e)

    if not os.path.isfile(source_path):
        return source_path, False, None

    src_dir = os.path.dirname(source_path)
    if os.path.normcase(src_dir) == os.path.normcase(output_dir):
        return source_path, False, None

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        return source_path, False, str(e)

    dest = unique_dest_path(output_dir, os.path.basename(source_path))
    try:
        shutil.move(source_path, dest)
    except OSError as e:
        return source_path, False, str(e)
    return dest, True, None


def finalize_source_after_processing(app, source_path: str, output_dir: str, file_id=None) -> str:
    """
    After successful processing: move source beside outputs (if needed),
    update app_log source + outputs[role=source], update queue path and mark done.

    Returns final absolute source path (moved or original).
    """
    from whisperfast.i18n import t

    source_path = os.path.abspath(source_path) if source_path else source_path
    output_dir = os.path.abspath(output_dir) if output_dir else source_path and os.path.dirname(source_path)

    final, moved, err = move_source_to_output_dir(source_path, output_dir)

    if err:
        try:
            app.log_file_event(
                t("source_move_error", name=os.path.basename(source_path), error=err),
                file_id=file_id,
            )
        except Exception:
            pass

    if hasattr(app, "set_file_source") and file_id:
        app.set_file_source(file_id, final)
    if hasattr(app, "add_file_output") and file_id:
        app.add_file_output("source", final, file_id=file_id)

    try:
        if hasattr(app, "queue_ctrl"):
            app.queue_ctrl.register_output_paths([final])
    except Exception:
        pass

    if moved:
        try:
            app.log_file_event(
                t("source_moved", path=final),
                file_id=file_id,
            )
        except Exception:
            pass

    def _apply_queue():
        try:
            app.queue_ctrl.relocate_and_mark_done(source_path, final)
        except Exception:
            try:
                app._mark_done_by_path(final)
            except Exception:
                pass

    try:
        app.root.after(0, _apply_queue)
    except Exception:
        _apply_queue()

    return final

"""Disposable live transcript preview on tiny/base. Does not write to disk."""
from __future__ import annotations

import os
import threading
from collections import deque
from typing import Deque, Optional

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_model = None
_model_name = ""
_lines: Deque[str] = deque(maxlen=24)
_error = ""
_running = False


def is_running() -> bool:
    return _running


def snapshot_text() -> str:
    with _lock:
        return "\n".join(_lines)


def last_error() -> str:
    return _error


def start_preview(session, *, model_name: str = "tiny", device_mode: str = "CPU") -> None:
    global _thread, _running, _error, _model_name
    stop_preview()
    _stop.clear()
    _error = ""
    _model_name = (model_name or "tiny").strip() or "tiny"
    if _model_name not in ("tiny", "base", "small"):
        _model_name = "tiny"
    _running = True
    _thread = threading.Thread(
        target=_loop,
        args=(session, _model_name, device_mode),
        daemon=True,
        name="ftw-live-preview",
    )
    _thread.start()


def stop_preview() -> None:
    global _thread, _model, _running
    _stop.set()
    thread = _thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=3.0)
    _unload_model()
    _running = False
    _thread = None


def _unload_model() -> None:
    global _model
    with _lock:
        _model = None
    try:
        import gc

        gc.collect()
    except Exception:
        pass


def _loop(session, model_name: str, device_mode: str) -> None:
    global _error, _model
    while not _stop.wait(4.0):
        if not getattr(session, "running", False):
            continue
        try:
            chunk = session.copy_last_seconds(5.0)
        except Exception as e:
            _error = str(e)
            continue
        if not chunk or not os.path.isfile(chunk):
            continue
        try:
            model = _get_model(model_name, device_mode)
            segments, _info = model.transcribe(chunk, vad_filter=False, beam_size=1)
            texts = []
            for seg in segments:
                t = (getattr(seg, "text", None) or "").strip()
                if t:
                    texts.append(t)
            if texts:
                with _lock:
                    _lines.append(" ".join(texts)[-400:])
        except Exception as e:
            _error = str(e)
        finally:
            try:
                os.remove(chunk)
            except OSError:
                pass
    _unload_model()


def _get_model(name: str, device_mode: str):
    global _model
    with _lock:
        if _model is not None:
            return _model
    from faster_whisper import WhisperModel

    try:
        import torch

        want_cuda = device_mode in ("GPU", "AUTO") and torch.cuda.is_available()
    except Exception:
        want_cuda = False
    # Keep live preview off the GPU when a main model may load later — CPU tiny is enough.
    device = "cpu"
    compute = "int8"
    del want_cuda
    model = WhisperModel(name, device=device, compute_type=compute)
    with _lock:
        _model = model
    return model

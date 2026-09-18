"""Meeting capture: microphone + WASAPI loopback → stereo WAV, then the queue.

Windows-first. sounddevice is optional; if it is missing the UI shows an install
hint. Left channel = microphone, right channel = system playback (loopback).

PCM is written to disk as it arrives so a crash still leaves a recoverable WAV.
Pause drops incoming audio but keeps the file open.
"""
from __future__ import annotations

import os
import struct
import threading
import wave
import time
from datetime import datetime
from typing import Callable, List, Optional, Tuple

LogFunc = Callable[..., None]

_WAV_HEADER_BYTES = 44
_INPROGRESS_SUFFIX = ".inprogress"


def capture_available() -> bool:
    try:
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401

        return True
    except ImportError:
        return False


def _wasapi_loopback_device(sd) -> Optional[int]:
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        return None
    wasapi_index = None
    for i, api in enumerate(hostapis):
        name = str(api.get("name") or "").lower()
        if "wasapi" in name:
            wasapi_index = i
            break
    if wasapi_index is None:
        return None
    api = hostapis[wasapi_index]
    default_out = api.get("default_output_device")
    if default_out is None or int(default_out) < 0:
        return None
    return int(default_out)


def repair_wav_header(path: str) -> bool:
    """Fix RIFF/data sizes when the process died before wave.close().

    Returns True if the header was rewritten.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < _WAV_HEADER_BYTES:
        return False
    try:
        with open(path, "r+b") as f:
            header = f.read(_WAV_HEADER_BYTES)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return False
            data_size = size - _WAV_HEADER_BYTES
            current = struct.unpack_from("<I", header, 40)[0]
            if current == data_size and struct.unpack_from("<I", header, 4)[0] == size - 8:
                return False
            f.seek(4)
            f.write(struct.pack("<I", size - 8))
            f.seek(40)
            f.write(struct.pack("<I", data_size))
    except OSError:
        return False
    return True


def recover_captures(directory: str, recursive: bool = True) -> List[str]:
    """Repair interrupted capture_*.wav files. Returns recovered paths."""
    recovered: List[str] = []
    if not directory or not os.path.isdir(directory):
        return recovered
    folders = [directory]
    if recursive:
        try:
            for name in os.listdir(directory):
                child = os.path.join(directory, name)
                if os.path.isdir(child):
                    folders.append(child)
        except OSError:
            pass
    for folder in folders:
        recovered.extend(_recover_in_dir(folder))
    return recovered


def _recover_in_dir(directory: str) -> List[str]:
    recovered: List[str] = []
    try:
        names = os.listdir(directory)
    except OSError:
        return recovered
    for name in names:
        if not name.lower().startswith("capture_") or not name.lower().endswith(".wav"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        marker = path + _INPROGRESS_SUFFIX
        needs = os.path.isfile(marker)
        if not needs:
            try:
                with open(path, "rb") as f:
                    header = f.read(_WAV_HEADER_BYTES)
                if len(header) >= 44:
                    declared = struct.unpack_from("<I", header, 40)[0]
                    actual = os.path.getsize(path) - _WAV_HEADER_BYTES
                    needs = declared != actual
            except OSError:
                continue
        if not needs:
            continue
        if repair_wav_header(path):
            recovered.append(os.path.abspath(path))
        try:
            if os.path.isfile(marker):
                os.remove(marker)
        except OSError:
            pass
    return recovered


def list_loopback_devices() -> List[Tuple[str, str]]:
    """[(id, label)] WASAPI output devices for loopback. Empty if sounddevice missing."""
    if not capture_available():
        return []
    import sounddevice as sd

    out: List[Tuple[str, str]] = []
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:
        return out
    wasapi = None
    for i, api in enumerate(hostapis):
        if "wasapi" in str(api.get("name") or "").lower():
            wasapi = i
            break
    for idx, dev in enumerate(devices):
        if int(dev.get("max_output_channels") or 0) <= 0:
            continue
        if wasapi is not None and int(dev.get("hostapi") or -1) != wasapi:
            continue
        name = str(dev.get("name") or f"#{idx}")
        out.append((str(idx), f"{idx}: {name}"))
    return out


class CaptureSession:
    """Background stereo recorder. start() / pause() / resume() / stop() from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._path = ""
        self._error = ""
        self._running = False
        self._samplerate = 48000
        self._started_at = 0.0
        self._last_sound_at = 0.0
        self._last_rms = 0.0
        self.trigger = "manual"
        self._include_mic = True
        self._include_system = True
        self._loopback_device: Optional[int] = None
        self.calendar_title = ""
        self.calendar_attendees: List[str] = []
        self.log_file_id = ""

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._running and self._paused.is_set()

    @property
    def path(self) -> str:
        return self._path

    @property
    def error(self) -> str:
        return self._error

    @property
    def samplerate(self) -> int:
        return int(self._samplerate)

    @property
    def started_at(self) -> float:
        return float(self._started_at)

    def elapsed_seconds(self) -> float:
        if not self._running or self._started_at <= 0:
            return 0.0
        return max(0.0, time.time() - self._started_at)

    def last_sound_age(self) -> float:
        if not self._running or self._last_sound_at <= 0:
            return 0.0
        return max(0.0, time.time() - self._last_sound_at)

    def start(
        self,
        out_dir: str,
        log_func: Optional[LogFunc] = None,
        *,
        trigger: str = "manual",
        include_mic: bool = True,
        include_system: bool = True,
        loopback_device: Optional[int] = None,
        calendar_title: str = "",
        calendar_attendees: Optional[List[str]] = None,
    ) -> str:
        if not capture_available():
            from whisperfast.i18n import t

            raise RuntimeError(t("capture_need_sounddevice"))
        with self._lock:
            if self._running:
                return self._path
            os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._path = os.path.join(out_dir, f"capture_{stamp}.wav")
            self._error = ""
            self._stop.clear()
            self._paused.clear()
            self._running = True
            self._started_at = time.time()
            self._last_sound_at = self._started_at
            self.trigger = (trigger or "manual").strip() or "manual"
            self._include_mic = bool(include_mic)
            self._include_system = bool(include_system)
            self._loopback_device = loopback_device
            self.calendar_title = calendar_title or ""
            self.calendar_attendees = list(calendar_attendees or [])
            self.log_file_id = ""
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        if log_func:
            try:
                from whisperfast.i18n import t

                log_func(t("capture_started", path=self._path))
            except Exception:
                pass
        return self._path

    def pause(self, log_func: Optional[LogFunc] = None) -> None:
        if not self._running:
            return
        self._paused.set()
        if log_func:
            try:
                from whisperfast.i18n import t

                log_func(t("capture_paused"))
            except Exception:
                pass

    def resume(self) -> None:
        self._paused.clear()

    def toggle_pause(self, log_func: Optional[LogFunc] = None) -> bool:
        """Returns True if now paused."""
        if not self._running:
            return False
        if self._paused.is_set():
            self.resume()
            return False
        self.pause(log_func=log_func)
        return True

    def stop(self, log_func: Optional[LogFunc] = None) -> str:
        with self._lock:
            self._stop.set()
            thread = self._thread
            path = self._path
        if thread is not None:
            thread.join(timeout=8.0)
        self._running = False
        self._paused.clear()
        if log_func:
            try:
                from whisperfast.i18n import t

                if self._error:
                    log_func(t("capture_error", error=self._error))
                elif path and os.path.isfile(path):
                    log_func(t("capture_saved", path=path))
            except Exception:
                pass
        return path

    def copy_last_seconds(self, seconds: float, dest_path: Optional[str] = None) -> str:
        """Copy the last N seconds of the growing PCM WAV. Session keeps recording."""
        seconds = max(0.1, float(seconds or 0))
        src = self._path
        if not src or not os.path.isfile(src):
            raise RuntimeError("no capture file")
        if dest_path is None:
            parent = os.path.dirname(src)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_path = os.path.join(parent, f"capture_clip_{stamp}.wav")
        channels = 2
        width = 2
        frame = channels * width
        bps = int(self._samplerate) * frame
        take = int(seconds * bps)
        take = take - (take % frame)
        with self._write_lock:
            try:
                size = os.path.getsize(src)
            except OSError as e:
                raise RuntimeError(str(e)) from e
            data_len = max(0, size - _WAV_HEADER_BYTES)
            take = min(data_len, take)
            take = take - (take % frame)
            with open(src, "rb") as f:
                header = f.read(_WAV_HEADER_BYTES)
                f.seek(_WAV_HEADER_BYTES + data_len - take)
                pcm = f.read(take)
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with wave.open(dest_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(width)
            wf.setframerate(int(self._samplerate))
            wf.writeframes(pcm)
        del header
        return dest_path

    def _note_energy(self, samples) -> None:
        try:
            rms = float((samples * samples).mean()) ** 0.5
        except Exception:
            return
        self._last_rms = rms
        if rms >= 0.008:
            self._last_sound_at = time.time()

    def _run(self) -> None:
        try:
            self._record_loop()
        except Exception as e:
            self._error = str(e)
        finally:
            self._running = False
            marker = self._path + _INPROGRESS_SUFFIX
            try:
                if os.path.isfile(marker):
                    os.remove(marker)
            except OSError:
                pass

    def _open_wav(self, path: str, samplerate: int):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        wf = wave.open(path, "wb")
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        try:
            with open(path + _INPROGRESS_SUFFIX, "w", encoding="utf-8") as marker:
                marker.write("recording\n")
        except OSError:
            pass
        return wf

    def _write_float_stereo(self, wf, stereo, write_lock: threading.Lock) -> None:
        import numpy as np

        if stereo is None or getattr(stereo, "size", 0) == 0:
            return
        clipped = np.clip(stereo, -1.0, 1.0)
        self._note_energy(clipped)
        pcm = (clipped * 32767.0).astype("<i2")
        with write_lock:
            wf.writeframes(pcm.tobytes())

    def _record_loop(self) -> None:
        import numpy as np
        import sounddevice as sd

        samplerate = int(self._samplerate)
        blocksize = 2048
        extra = None
        loopback_dev = None
        if self._include_system and hasattr(sd, "WasapiSettings"):
            try:
                extra = sd.WasapiSettings(loopback=True)
                if self._loopback_device is not None:
                    loopback_dev = int(self._loopback_device)
                else:
                    loopback_dev = _wasapi_loopback_device(sd)
            except Exception:
                extra = None
        mic_dev = None
        if self._include_mic:
            try:
                mic_dev = sd.default.device[0]
            except Exception:
                mic_dev = None

        if (
            extra is not None
            and loopback_dev is not None
            and mic_dev is not None
            and self._include_system
        ):
            try:
                self._record_two_streams(
                    sd, np, mic_dev, loopback_dev, extra, samplerate, blocksize
                )
                return
            except RuntimeError as e:
                if str(e) == "no audio captured":
                    raise
            except Exception:
                pass
        if extra is not None and loopback_dev is not None and not self._include_mic:
            try:
                self._record_system_only(
                    sd, np, loopback_dev, extra, samplerate, blocksize
                )
                return
            except RuntimeError as e:
                if str(e) == "no audio captured":
                    raise
            except Exception:
                pass
        self._record_mic_only(sd, np, mic_dev, samplerate, blocksize)

    def _record_mic_only(self, sd, np, mic_dev, samplerate, blocksize) -> None:
        write_lock = self._write_lock
        wf = self._open_wav(self._path, samplerate)
        wrote = False

        def mix_callback(indata, frames_n, time_info, status):
            nonlocal wrote
            del frames_n, time_info, status
            if self._paused.is_set():
                return
            mono = indata[:, :1] if indata.ndim > 1 else indata.reshape(-1, 1)
            stereo = np.repeat(mono, 2, axis=1)
            self._write_float_stereo(wf, stereo, write_lock)
            wrote = True

        try:
            kwargs = {"samplerate": samplerate, "blocksize": blocksize, "dtype": "float32"}
            with sd.InputStream(device=mic_dev, channels=1, callback=mix_callback, **kwargs):
                while not self._stop.wait(0.1):
                    pass
        finally:
            with write_lock:
                wf.close()
        if not wrote and os.path.isfile(self._path) and os.path.getsize(self._path) <= _WAV_HEADER_BYTES:
            try:
                os.remove(self._path)
            except OSError:
                pass
            raise RuntimeError("no audio captured")

    def _record_system_only(self, sd, np, loop_dev, extra, samplerate, blocksize) -> None:
        write_lock = self._write_lock
        wf = self._open_wav(self._path, samplerate)
        wrote = False

        def cb_loop(indata, frames, time_info, status):
            nonlocal wrote
            del frames, time_info, status
            if self._paused.is_set():
                return
            if indata.ndim > 1:
                chunk = indata.mean(axis=1)
            else:
                chunk = indata.reshape(-1)
            stereo = np.column_stack((chunk, chunk))
            self._write_float_stereo(wf, stereo, write_lock)
            wrote = True

        try:
            with sd.InputStream(
                device=loop_dev,
                channels=2,
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="float32",
                extra_settings=extra,
                callback=cb_loop,
            ):
                while not self._stop.wait(0.1):
                    pass
        finally:
            with write_lock:
                wf.close()
        if not wrote and os.path.isfile(self._path) and os.path.getsize(self._path) <= _WAV_HEADER_BYTES:
            try:
                os.remove(self._path)
            except OSError:
                pass
            raise RuntimeError("no audio captured")

    def _record_two_streams(self, sd, np, mic_dev, loop_dev, extra, samplerate, blocksize):
        write_lock = self._write_lock
        state_lock = threading.Lock()
        mic_buf = np.zeros(0, dtype=np.float32)
        loop_buf = np.zeros(0, dtype=np.float32)
        wf = self._open_wav(self._path, samplerate)
        wrote = False

        def flush_aligned():
            nonlocal mic_buf, loop_buf, wrote
            with state_lock:
                n = min(len(mic_buf), len(loop_buf))
                if n <= 0:
                    return
                stereo = np.column_stack((mic_buf[:n], loop_buf[:n]))
                mic_buf = mic_buf[n:]
                loop_buf = loop_buf[n:]
            self._write_float_stereo(wf, stereo, write_lock)
            wrote = True

        def cb_mic(indata, frames, time_info, status):
            del frames, time_info, status
            if self._paused.is_set():
                return
            chunk = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)
            with state_lock:
                nonlocal mic_buf
                mic_buf = np.concatenate((mic_buf, chunk.astype(np.float32, copy=False)))
            flush_aligned()

        def cb_loop(indata, frames, time_info, status):
            del frames, time_info, status
            if self._paused.is_set():
                return
            if indata.ndim > 1:
                chunk = indata.mean(axis=1)
            else:
                chunk = indata.reshape(-1)
            with state_lock:
                nonlocal loop_buf
                loop_buf = np.concatenate((loop_buf, chunk.astype(np.float32, copy=False)))
            flush_aligned()

        try:
            with sd.InputStream(
                device=mic_dev,
                channels=1,
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="float32",
                callback=cb_mic,
            ), sd.InputStream(
                device=loop_dev,
                channels=2,
                samplerate=samplerate,
                blocksize=blocksize,
                dtype="float32",
                extra_settings=extra,
                callback=cb_loop,
            ):
                while not self._stop.wait(0.1):
                    pass
            flush_aligned()
        finally:
            with write_lock:
                wf.close()
        if not wrote and os.path.isfile(self._path) and os.path.getsize(self._path) <= _WAV_HEADER_BYTES:
            try:
                os.remove(self._path)
            except OSError:
                pass
            raise RuntimeError("no audio captured")


_session: Optional[CaptureSession] = None
_session_lock = threading.Lock()


def get_capture_session() -> CaptureSession:
    global _session
    with _session_lock:
        if _session is None:
            _session = CaptureSession()
        return _session


def default_capture_dir() -> str:
    from whisperfast.config import BASE_DIR

    return os.path.join(BASE_DIR, "captures")

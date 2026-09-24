"""WASAPI render-endpoint loopback via pycaw.

sounddevice's PortAudio build does not expose loopback (WasapiSettings has no
such argument). System audio is captured with IAudioClient and
AUDCLNT_STREAMFLAGS_LOOPBACK on a dedicated MTA thread, then mixed to mono
float32 at the target rate.
"""
from __future__ import annotations

import ctypes
import queue
import struct
import sys
import threading
import time
from ctypes import POINTER, c_uint32, c_uint64, c_void_p
from ctypes.wintypes import DWORD
from typing import List, Optional, Tuple

import numpy as np

_LOOPBACK = 0x00020000
_SILENT = 0x2
_WAVE_EXTENSIBLE = 0xFFFE
_TAG_PCM = 1
_TAG_FLOAT = 3


def loopback_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import comtypes  # noqa: F401
        from pycaw.pycaw import AudioUtilities  # noqa: F401

        return True
    except ImportError:
        return False


def list_render_devices() -> List[Tuple[str, str]]:
    """Active playback endpoints as (id, friendly name)."""
    if not loopback_available():
        return []
    import comtypes
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        devices = AudioUtilities.GetAllDevices(
            EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value
        )
    except Exception:
        return []
    out: List[Tuple[str, str]] = []
    for dev in devices:
        name = str(getattr(dev, "FriendlyName", "") or "").strip()
        eid = str(getattr(dev, "id", "") or "").strip()
        if eid and name:
            out.append((eid, name))
    return out


def resolve_endpoint_id(stored) -> Optional[str]:
    """Map a saved capture device to a render endpoint id.

    None means the default playback device. Accepts an endpoint id, a
    friendly name, or a legacy sounddevice output index.
    """
    if stored is None:
        return None
    text = str(stored).strip()
    if not text:
        return None
    devices = list_render_devices()
    for eid, name in devices:
        if text == eid or text == name:
            return eid
    if text.isdigit():
        sd_name = _sounddevice_output_name(int(text))
        if sd_name:
            matched = _match_name(sd_name, devices)
            if matched:
                return matched
    return _match_name(text, devices)


def _match_name(label: str, devices: List[Tuple[str, str]]) -> Optional[str]:
    needle = label.strip().lower()
    if not needle:
        return None
    for eid, name in devices:
        if name.lower() == needle:
            return eid
    for eid, name in devices:
        low = name.lower()
        if needle.startswith(low) or low.startswith(needle):
            return eid
    return None


def _sounddevice_output_name(index: int) -> str:
    try:
        import sounddevice as sd

        info = sd.query_devices(index)
    except Exception:
        return ""
    if int(info.get("max_output_channels") or 0) <= 0:
        return ""
    return str(info.get("name") or "")


class _Resampler:
    def __init__(self, src_rate: int, dst_rate: int):
        self.src = int(src_rate)
        self.dst = int(dst_rate)
        self._hold = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def process(self, mono: np.ndarray) -> np.ndarray:
        if mono.size == 0 or self.src == self.dst:
            return mono
        self._hold = np.concatenate((self._hold, mono))
        if len(self._hold) < 2:
            return np.zeros(0, dtype=np.float32)
        step = self.src / self.dst
        out_n = int((len(self._hold) - 1 - self._pos) / step)
        if out_n <= 0:
            return np.zeros(0, dtype=np.float32)
        idx = self._pos + step * np.arange(out_n, dtype=np.float64)
        i0 = idx.astype(np.int64)
        frac = (idx - i0).astype(np.float32)
        sampled = self._hold[i0] * (1.0 - frac) + self._hold[i0 + 1] * frac
        consumed = int(i0[-1])
        self._hold = self._hold[consumed:]
        self._pos = float(idx[-1] - consumed)
        return sampled.astype(np.float32, copy=False)


class WasapiLoopback:
    """Pull mono float32 from a render endpoint in loopback mode."""

    def __init__(self, endpoint_id: Optional[str] = None, samplerate: int = 48000):
        self.endpoint_id = endpoint_id or None
        self.samplerate = int(samplerate)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._chunks: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=128)
        self._error: Optional[BaseException] = None

    def open(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="wasapi-loopback", daemon=True)
        self._thread.start()
        if not self._ready.wait(8.0):
            self.close()
            raise RuntimeError("WASAPI loopback timed out")
        if self._error is not None:
            err = self._error
            self.close()
            raise err

    def read(self) -> np.ndarray:
        parts: List[np.ndarray] = []
        while True:
            try:
                parts.append(self._chunks.get_nowait())
            except queue.Empty:
                break
        if not parts:
            return np.zeros(0, dtype=np.float32)
        if len(parts) == 1:
            return parts[0]
        return np.concatenate(parts)

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None

    def _run(self) -> None:
        import sys

        sys.coinit_flags = 0  # MTA before comtypes initializes this thread
        import comtypes

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        client = None
        try:
            client, capture, spec, resampler = _open_client(self.endpoint_id, self.samplerate)
            self._ready.set()
            while not self._stop.is_set():
                mono = _drain(capture, spec, resampler)
                if mono.size:
                    try:
                        self._chunks.put(mono, timeout=0.2)
                    except queue.Full:
                        pass
                else:
                    time.sleep(0.01)
        except Exception as exc:
            self._error = exc
            self._ready.set()
        finally:
            if client is not None:
                try:
                    client.Stop()
                except Exception:
                    pass
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def _open_client(endpoint_id: Optional[str], samplerate: int):
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    from pycaw.api.audioclient import IAudioClient
    from pycaw.pycaw import AudioUtilities

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = (
            COMMETHOD(
                [],
                HRESULT,
                "GetBuffer",
                (["out"], POINTER(c_void_p), "ppData"),
                (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                (["out"], POINTER(DWORD), "pdwFlags"),
                (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                (["out"], POINTER(c_uint64), "pu64QPCPosition"),
            ),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
            COMMETHOD(
                [],
                HRESULT,
                "GetNextPacketSize",
                (["out"], POINTER(c_uint32), "pNumFramesInNextPacket"),
            ),
        )

    if endpoint_id:
        device = AudioUtilities.GetDeviceEnumerator().GetDevice(endpoint_id)
    else:
        speakers = AudioUtilities.GetSpeakers()
        if speakers is None:
            raise RuntimeError("no default playback device")
        device = speakers._dev
    client = device.Activate(IAudioClient._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(
        IAudioClient
    )
    mix = client.GetMixFormat()
    try:
        tag, channels, rate, bits, block = _describe_format(mix)
        client.Initialize(0, _LOOPBACK, 0, 0, mix, None)
    finally:
        _free_mix(mix)
    capture = client.GetService(IAudioCaptureClient._iid_).QueryInterface(IAudioCaptureClient)
    client.Start()
    spec = (channels, tag, bits, block)
    return client, capture, spec, _Resampler(rate, samplerate)


def _drain(capture, spec, resampler: _Resampler) -> np.ndarray:
    channels, tag, bits, block = spec
    chunks: List[np.ndarray] = []
    while True:
        pending = int(capture.GetNextPacketSize() or 0)
        if pending <= 0:
            break
        data, frames, flags, _pos, _qpc = capture.GetBuffer()
        frames = int(frames)
        try:
            if frames <= 0:
                continue
            if int(flags) & _SILENT:
                mono = np.zeros(frames, dtype=np.float32)
            else:
                mono = _frames_to_mono(data, frames, channels, tag, bits, block)
        finally:
            capture.ReleaseBuffer(frames)
        chunks.append(mono)
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    mono = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
    return resampler.process(mono)


def _describe_format(mix) -> Tuple[int, int, int, int, int]:
    fmt = mix.contents
    tag = int(fmt.wFormatTag)
    channels = int(fmt.nChannels)
    rate = int(fmt.nSamplesPerSec)
    bits = int(fmt.wBitsPerSample)
    block = int(fmt.nBlockAlign)
    if tag == _WAVE_EXTENSIBLE:
        raw = ctypes.string_at(ctypes.addressof(fmt), 40)
        tag = struct.unpack_from("<I", raw, 24)[0]
    if channels <= 0 or rate <= 0 or block <= 0:
        raise RuntimeError("unsupported playback mix format")
    if tag not in (_TAG_PCM, _TAG_FLOAT):
        raise RuntimeError(f"unsupported playback mix format tag {tag}")
    return tag, channels, rate, bits, block


def _free_mix(mix) -> None:
    try:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(mix, c_void_p))
    except Exception:
        pass


def _frames_to_mono(data, frames: int, channels: int, tag: int, bits: int, block: int) -> np.ndarray:
    address = int(getattr(data, "value", data) or 0)
    if address == 0:
        return np.zeros(frames, dtype=np.float32)
    raw = ctypes.string_at(address, frames * block)
    if tag == _TAG_FLOAT and bits == 32:
        samples = np.frombuffer(raw, dtype="<f4").reshape(frames, channels)
    elif tag == _TAG_PCM and bits == 16:
        samples = np.frombuffer(raw, dtype="<i2").reshape(frames, channels).astype(np.float32)
        samples *= 1.0 / 32768.0
    elif tag == _TAG_PCM and bits == 32:
        samples = np.frombuffer(raw, dtype="<i4").reshape(frames, channels).astype(np.float32)
        samples *= 1.0 / 2147483648.0
    else:
        raise RuntimeError(f"unsupported playback sample format {tag}/{bits}")
    if channels == 1:
        return np.ascontiguousarray(samples[:, 0], dtype=np.float32)
    return samples.mean(axis=1).astype(np.float32, copy=False)

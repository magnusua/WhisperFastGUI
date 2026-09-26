#!/usr/bin/env python3
"""Diagnose NVIDIA GPU detection and PyTorch CUDA readiness for FTW.

Use this on a new PC after install.bat, or when transcription stays on CPU
despite an NVIDIA card.

Usage:
  python scripts/diagnose_nvidia_torch.py
  py -3.12 scripts/diagnose_nvidia_torch.py

Prints:
  - nvidia-smi / DXGI / settings detection
  - torch version, CUDA build tag, torch.cuda.is_available()
  - whether FTW would request a torch reinstall (CPU → cu128)
  - a tiny faster-whisper CUDA load smoke test (optional model download)

Exit codes:
  0 — CUDA usable for Whisper
  1 — NVIDIA seen but torch is CPU / wrong CUDA / Whisper CUDA failed
  2 — no NVIDIA detected
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    from whisperfast.config import CUDA_INDEX
    from whisperfast.setup.gpu_info import (
        detect_nvidia_gpu,
        dxgi_nvidia_name,
        gpu_needs_cuda128,
        install_gpu_status_line,
        nvidia_from_settings,
        nvidia_smi_name,
        torch_build_too_old_for,
    )
    from whisperfast.setup.installer import (
        _torch_needs_update,
        get_latest_pip_index_version,
    )

    print("FTW NVIDIA / PyTorch diagnose")
    print("CUDA index:", CUDA_INDEX)
    print("install.bat status:", install_gpu_status_line())
    saved_ok, saved_name = nvidia_from_settings()
    print("settings NVIDIA:", saved_ok, saved_name or "-")
    print("nvidia-smi:", nvidia_smi_name() or "-")
    print("DXGI:", dxgi_nvidia_name() or "-")
    has, name = detect_nvidia_gpu()
    print("detect_nvidia_gpu:", has, name or "-")
    if not has and not saved_ok:
        print("RESULT: no NVIDIA GPU detected")
        return 2

    gpu = (name or saved_name or "").strip()
    print("needs cu128 (Blackwell/RTX50):", gpu_needs_cuda128(gpu))

    try:
        import torch
    except Exception as exc:
        print("torch import failed:", exc)
        print("RESULT: install PyTorch cu128 via install.bat or FTW Update")
        return 1

    version = getattr(torch, "__version__", "") or ""
    cuda = getattr(torch.version, "cuda", None)
    available = bool(torch.cuda.is_available())
    print("torch:", version)
    print("torch.version.cuda:", cuda)
    print("torch.cuda.is_available:", available)
    if available:
        try:
            print("device:", torch.cuda.get_device_name(0))
            print("capability:", torch.cuda.get_device_capability(0))
        except Exception as exc:
            print("device query failed:", exc)

    latest = get_latest_pip_index_version("torch", CUDA_INDEX) or ""
    print("latest on cu128 index:", latest or "-")
    if latest:
        print(
            "FTW would reinstall torch:",
            _torch_needs_update(version, latest, gpu_name=gpu),
        )
    if torch_build_too_old_for(gpu):
        print("RESULT: torch CUDA build too old / CPU — run Update (cu128), restart FTW")
        return 1
    if not available:
        print("RESULT: CUDA not available — reinstall torch from cu128, restart FTW")
        return 1

    try:
        from faster_whisper import WhisperModel

        print("loading faster-whisper tiny on CUDA (float16)...")
        WhisperModel("tiny", device="cuda", compute_type="float16")
        print("RESULT: OK — Whisper CUDA works")
        return 0
    except Exception as exc:
        print("Whisper CUDA load failed:", type(exc).__name__, exc)
        print("RESULT: torch CUDA ok, but faster-whisper/ctranslate2 failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

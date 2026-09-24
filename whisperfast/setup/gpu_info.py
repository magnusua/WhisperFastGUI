"""Визначення NVIDIA GPU та збереження моделі в settings.json."""
import ctypes
import os
import subprocess
import sys
import uuid

from whisperfast.settings import load_app_settings, save_app_settings
from whisperfast.platform_util import win_no_window_kwargs

# Live D3D11 device on the NVIDIA adapter. Releasing it lets Windows put the
# laptop GPU back into D3 when the lid is closed and no monitor is attached.
_d3d_keepalive = []
NVIDIA_VENDOR_ID = 0x10DE


def gpu_needs_cuda128(name) -> bool:
    """RTX 50-series (Blackwell) needs a PyTorch wheel built with CUDA 12.8+."""
    text = (name or "").lower()
    if "blackwell" in text:
        return True
    if "ada" in text:
        return False
    return "rtx50" in text.replace(" ", "")


def installed_torch_cuda():
    """(major, minor) of the installed torch CUDA build, or None for a CPU wheel."""
    try:
        import torch
        raw = getattr(torch.version, "cuda", None)
    except Exception:
        return None
    if not raw:
        return None
    parts = str(raw).split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def torch_build_too_old_for(gpu_name) -> bool:
    if not gpu_needs_cuda128(gpu_name):
        return False
    ver = installed_torch_cuda()
    return ver is None or ver < (12, 8)


def _cuda_tag_version(version_text):
    """'2.5.1+cu121' -> (12, 1). Missing tag -> None."""
    text = str(version_text or "")
    if "+cu" not in text.lower():
        return None
    tag = text.lower().split("+cu", 1)[1]
    digits = "".join(ch for ch in tag if ch.isdigit())
    if len(digits) < 2:
        return None
    return int(digits[:-1]), int(digits[-1])


def prefer_discrete_gpu() -> bool:
    """Tell Windows hybrid graphics to run this program on the NVIDIA GPU."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        paths = {os.path.abspath(sys.executable)}
        argv0 = os.path.abspath(sys.argv[0]) if sys.argv else ""
        if argv0:
            paths.add(argv0)
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\DirectX\UserGpuPreferences",
        )
        try:
            for path in paths:
                winreg.SetValueEx(key, path, 0, winreg.REG_SZ, "GpuPreference=2;")
        finally:
            winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _guid(value):
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _com_method(com_ptr, index, restype, *argtypes):
    vtbl = ctypes.cast(com_ptr, ctypes.POINTER(ctypes.c_void_p))[0]
    slot = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(slot)


def hold_nvidia_display_client() -> bool:
    """Keep a D3D11 device on the NVIDIA adapter so CUDA can see it with the lid shut."""
    if _d3d_keepalive:
        return True
    if sys.platform != "win32":
        return False
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
        d3d11 = ctypes.WinDLL("d3d11.dll")
        factory = ctypes.c_void_p()
        iid = _guid("770aae78-f26f-4dba-a829-253c83d1b387")
        dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(type(iid)), ctypes.POINTER(ctypes.c_void_p)]
        dxgi.CreateDXGIFactory1.restype = ctypes.c_long
        if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) < 0 or not factory:
            return False

        class DXGI_ADAPTER_DESC(ctypes.Structure):
            _fields_ = [
                ("Description", ctypes.c_wchar * 128),
                ("VendorId", ctypes.c_uint),
                ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint),
                ("Revision", ctypes.c_uint),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuidLow", ctypes.c_ulong),
                ("AdapterLuidHigh", ctypes.c_long),
            ]

        enum_adapters = _com_method(
            factory, 7, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)
        )
        nvidia = None
        for index in range(8):
            adapter = ctypes.c_void_p()
            if enum_adapters(factory, index, ctypes.byref(adapter)) < 0 or not adapter:
                break
            desc = DXGI_ADAPTER_DESC()
            get_desc = _com_method(adapter, 8, ctypes.c_long, ctypes.POINTER(DXGI_ADAPTER_DESC))
            if get_desc(adapter, ctypes.byref(desc)) >= 0 and desc.VendorId == NVIDIA_VENDOR_ID:
                nvidia = adapter
                break
        if not nvidia:
            return False

        device = ctypes.c_void_p()
        context = ctypes.c_void_p()
        levels = (ctypes.c_int * 2)(0xB000, 0xA100)
        chosen = ctypes.c_int()
        d3d11.D3D11CreateDevice.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        d3d11.D3D11CreateDevice.restype = ctypes.c_long
        hr = d3d11.D3D11CreateDevice(
            nvidia, 0, None, 0, levels, 2, 7,
            ctypes.byref(device), ctypes.byref(chosen), ctypes.byref(context),
        )
        if hr < 0 or not device:
            return False
        _d3d_keepalive.append((factory, nvidia, device, context))
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _cu_init() -> bool:
    if sys.platform != "win32":
        return False
    try:
        nvcuda = ctypes.WinDLL("nvcuda.dll")
        nvcuda.cuInit.argtypes = [ctypes.c_uint]
        nvcuda.cuInit.restype = ctypes.c_int
        return nvcuda.cuInit(0) == 0
    except OSError:
        return False


def prepare_nvidia_gpu() -> None:
    """Wake a laptop dGPU that Windows powered off with the lid closed and no monitor."""
    prefer_discrete_gpu()
    hold_nvidia_display_client()
    _cu_init()


def nvidia_smi_name() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            **win_no_window_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    line = (result.stdout or "").strip().splitlines()
    return line[0].strip() if line else ""


def poke_nvidia_gpu() -> bool:
    """Wake a powered-down discrete GPU, then ask the driver to list it."""
    prepare_nvidia_gpu()
    name = nvidia_smi_name()
    poke_nvidia_gpu.last_name = name
    return bool(name)


poke_nvidia_gpu.last_name = ""


def detect_nvidia_gpu():
    """
    Повертає (has_nvidia, gpu_name).
    gpu_name — рядок з nvidia-smi / torch або None.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return True, torch.cuda.get_device_name(0)
    except Exception:
        pass

    name = nvidia_smi_name()
    if name:
        return True, name

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1 -ExpandProperty Name)",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                **win_no_window_kwargs(),
            )
            if result.returncode == 0:
                wmi_name = (result.stdout or "").strip()
                if wmi_name:
                    return True, wmi_name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    return False, None


def gpu_model_looks_nvidia(name):
    """True if settings.gpu_model already names an NVIDIA card (no hardware probe)."""
    return "nvidia" in (name or "").strip().lower()


def nvidia_from_settings():
    """
    Trust settings.json when gpu_model contains 'NVIDIA'.
    Returns (True, name) or (False, name_or_empty). Does not probe hardware.
    """
    name = (load_app_settings().get("gpu_model") or "").strip()
    if gpu_model_looks_nvidia(name):
        return True, name
    return False, name


def install_gpu_status_line():
    """One-line status for install.bat: SAVED:/FOUND:/NOTFOUND. Skips probe if settings name NVIDIA."""
    saved, name = nvidia_from_settings()
    if saved:
        return "SAVED:" + name
    has, live = detect_nvidia_gpu()
    if has:
        return "FOUND:" + ((live or "").strip() or "NVIDIA")
    return "NOTFOUND"


def nvidia_for_install():
    """Install path: trust settings.gpu_model if it names NVIDIA; otherwise probe."""
    saved, name = nvidia_from_settings()
    if saved:
        save_app_settings({"has_nvidia": True, "gpu_model": name})
        return True, name
    return refresh_gpu_settings()


def refresh_gpu_settings():
    """
    Оновлює has_nvidia та gpu_model у settings.json. Повертає (has_nvidia, gpu_name).
    If gpu_model already names NVIDIA, a failed probe does not clear it.
    """
    saved, saved_name = nvidia_from_settings()
    has_nvidia, name = detect_nvidia_gpu()
    live = (name or "").strip()
    if has_nvidia:
        final = live or saved_name or "NVIDIA"
        save_app_settings({"has_nvidia": True, "gpu_model": final})
        return True, final
    if saved:
        save_app_settings({"has_nvidia": True, "gpu_model": saved_name})
        return True, saved_name
    save_app_settings({"has_nvidia": False, "gpu_model": live})
    return False, name


def get_saved_gpu_settings():
    """Читає збережені значення з settings.json."""
    data = load_app_settings()
    return bool(data.get("has_nvidia")), (data.get("gpu_model") or "").strip()

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
    """'2.5.1+cu121' -> (12, 1). '+cpu' and a missing tag -> None."""
    text = str(version_text or "").lower()
    marker = "+cu"
    idx = text.find(marker)
    if idx < 0:
        return None
    rest = text[idx + len(marker):]
    if not rest or not rest[0].isdigit():
        return None
    digits = []
    for ch in rest:
        if not ch.isdigit():
            break
        digits.append(ch)
    digits = "".join(digits)
    if len(digits) < 2:
        return None
    return int(digits[:-1]), int(digits[-1])


def log_cuda_fallback(log_func, gpu_name=""):
    """Explain why CUDA is off: CPU wheel, old CUDA build, asleep GPU, or no NVIDIA."""
    from whisperfast.i18n import t

    name = (gpu_name or "").strip() or nvidia_smi_name() or poke_nvidia_gpu.last_name
    version = ""
    cuda = None
    try:
        import torch
        version = getattr(torch, "__version__", "") or ""
        cuda = getattr(torch.version, "cuda", None)
    except Exception:
        pass
    try:
        if name and not cuda and "+cpu" in version.lower():
            log_func(t("cuda_torch_cpu", name=name, version=version or "cpu"))
            return
        if name and torch_build_too_old_for(name):
            log_func(t("cuda_torch_too_old", name=name, cuda=cuda or "cpu"))
            return
        if name:
            log_func(t("cuda_headless", name=name))
            return
        log_func(t("cuda_unavailable"))
    except Exception:
        log_func("⚠ CUDA is not available — running on CPU (including AMD Radeon GPUs).")


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


def release_nvidia_display_client() -> None:
    """Drop the held D3D11 device so Windows may power the discrete GPU off again."""
    held = list(_d3d_keepalive)
    _d3d_keepalive.clear()
    for item in held:
        for com_ptr in reversed(item):
            try:
                if not com_ptr:
                    continue
                release = _com_method(com_ptr, 2, ctypes.c_ulong)
                release(com_ptr)
            except (OSError, AttributeError, ValueError):
                pass


def prepare_nvidia_gpu(hold=True) -> None:
    """Wake a laptop dGPU that Windows powered off with the lid closed and no monitor."""
    prefer_discrete_gpu()
    if hold:
        hold_nvidia_display_client()
    else:
        release_nvidia_display_client()
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


def dxgi_nvidia_name() -> str:
    """NVIDIA adapter description from DXGI (works even when nvidia-smi is not on PATH)."""
    if sys.platform != "win32":
        return ""
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
        factory = ctypes.c_void_p()
        iid = _guid("770aae78-f26f-4dba-a829-253c83d1b387")
        dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(type(iid)), ctypes.POINTER(ctypes.c_void_p)]
        dxgi.CreateDXGIFactory1.restype = ctypes.c_long
        if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) < 0 or not factory:
            return ""

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
        name = ""
        for index in range(8):
            adapter = ctypes.c_void_p()
            if enum_adapters(factory, index, ctypes.byref(adapter)) < 0 or not adapter:
                break
            try:
                desc = DXGI_ADAPTER_DESC()
                get_desc = _com_method(adapter, 8, ctypes.c_long, ctypes.POINTER(DXGI_ADAPTER_DESC))
                if get_desc(adapter, ctypes.byref(desc)) >= 0 and desc.VendorId == NVIDIA_VENDOR_ID:
                    name = (desc.Description or "").strip()
                    break
            finally:
                try:
                    release = _com_method(adapter, 2, ctypes.c_ulong)
                    release(adapter)
                except (OSError, AttributeError, ValueError):
                    pass
        try:
            release = _com_method(factory, 2, ctypes.c_ulong)
            release(factory)
        except (OSError, AttributeError, ValueError):
            pass
        return name
    except (OSError, AttributeError, ValueError):
        return ""


def poke_nvidia_gpu(hold=False) -> bool:
    """Wake a powered-down discrete GPU, then ask the driver to list it."""
    prepare_nvidia_gpu(hold=hold)
    name = nvidia_smi_name() or dxgi_nvidia_name()
    poke_nvidia_gpu.last_name = name
    return bool(name)


poke_nvidia_gpu.last_name = ""


def detect_nvidia_gpu():
    """
    Return (has_nvidia, gpu_name).
    gpu_name comes from torch / nvidia-smi / DXGI / WMI, or None.
    Wakes a laptop dGPU first so first-run install.bat sees the card.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return True, torch.cuda.get_device_name(0)
    except Exception:
        pass

    # Lid-closed / hybrid laptops often need a poke before nvidia-smi answers.
    prepare_nvidia_gpu(hold=False)

    name = nvidia_smi_name()
    if name:
        poke_nvidia_gpu.last_name = name
        return True, name

    name = dxgi_nvidia_name()
    if name:
        poke_nvidia_gpu.last_name = name
        return True, name

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA|GeForce|RTX|GTX|Quadro' } | Select-Object -First 1 -ExpandProperty Name)",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                **win_no_window_kwargs(),
            )
            if result.returncode == 0:
                wmi_name = (result.stdout or "").strip()
                if wmi_name:
                    poke_nvidia_gpu.last_name = wmi_name
                    return True, wmi_name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    return False, None


def gpu_model_looks_nvidia(name):
    """True if settings.gpu_model already names an NVIDIA card (no hardware probe)."""
    text = (name or "").strip().lower()
    if not text:
        return False
    if "nvidia" in text:
        return True
    # Drivers / WMI sometimes omit the vendor word.
    markers = ("geforce", "rtx", "gtx", "quadro", "tesla", "titan")
    return any(m in text for m in markers)


def nvidia_from_settings():
    """
    Trust settings.json when gpu_model names an NVIDIA card.
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
    Update has_nvidia and gpu_model in settings.json. Returns (has_nvidia, gpu_name).
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

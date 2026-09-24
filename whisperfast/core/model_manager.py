import gc
import threading
import time
import torch
from faster_whisper import WhisperModel
from whisperfast.config import DEFAULT_MODEL, WHISPER_MODELS

from whisperfast.i18n import t

class WhisperModelSingleton:
    """
    Класс-синглтон для управления моделью Whisper.
    Обеспечивает однократную загрузку и безопасную выгрузку из памяти.
    """
    _model = None
    _mode = None
    _model_name = None
    _device = None
    # Защищает _model/_mode/_model_name от гонки между потоком обработки очереди
    # и потоком диалога «Обновить модель» (ui/dialogs.py), который может вызвать
    # reset()/get() параллельно активной транскрибации. Не защищает сам вызов
    # model.transcribe() — он выполняется вне этого класса, на уже полученном
    # объекте модели.
    _lock = threading.RLock()

    @classmethod
    def get(cls, log_func, mode, model_name=None):
        """
        Загружает модель, если она еще не в памяти или если сменилось устройство/модель.
        model_name — короткое имя (tiny, base, large-v3-turbo и т.д.) или None для DEFAULT_MODEL.
        """
        name = (model_name or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        if name not in WHISPER_MODELS:
            name = DEFAULT_MODEL

        # Определяем устройство (cuda или cpu). Сплячу відеокарту спочатку будимо.
        want_gpu = mode in ["GPU", "AUTO"]
        device = "cuda" if (want_gpu and cuda_available(log_func)) else "cpu"

        # Определяем точность вычислений
        if device == "cuda":
            try:
                major, minor = torch.cuda.get_device_capability(0)
            except Exception:
                major, minor = 0, 0
            compute = "float16" if major >= 7 else "int8"
        else:
            compute = "int8"

        if device == "cpu" and mode in ["GPU", "AUTO"] and not torch.cuda.is_available():
            _log_cuda_fallback(log_func)

        with cls._lock:
            need_load = (
                cls._model is None
                or cls._mode != mode
                or cls._model_name != name
                or cls._device != device
            )
            if need_load:
                if cls._model is not None:
                    cls._model = None
                    cls._mode = None
                    cls._model_name = None
                    cls._device = None
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                log_func(t("initializing_model", model=name))
                log_func(t("device_info", device=device.upper(), precision=compute))
                try:
                    cls._model = WhisperModel(name, device=device, compute_type=compute)
                    cls._mode = mode
                    cls._model_name = name
                    cls._device = device
                    log_func(t("model_ready"))
                except Exception as e:
                    log_func(t("model_load_error", error=str(e)))
                    raise e
            return cls._model

    @classmethod
    def unload(cls):
        """
        Полностью освобождает ресурсы: удаляет модель и чистит кэш CUDA.
        """
        with cls._lock:
            if cls._model is not None:
                cls._model = None
                cls._mode = None
                cls._model_name = None
                cls._device = None

                # Принудительный запуск сборщика мусора Python
                gc.collect()

                # Очистка зарезервированной видеопамяти
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                print(t("model_unloaded"))

    @classmethod
    def reset(cls):
        """Сброс состояния: при следующем get() модель будет загружена заново."""
        with cls._lock:
            cls._model = None
            cls._mode = None
            cls._model_name = None
            cls._device = None


def _log_cuda_fallback(log_func):
    from whisperfast.setup.gpu_info import nvidia_smi_name, poke_nvidia_gpu, torch_build_too_old_for

    name = nvidia_smi_name() or poke_nvidia_gpu.last_name
    try:
        if name and torch_build_too_old_for(name):
            cuda = getattr(torch.version, "cuda", None) or "cpu"
            log_func(t("cuda_torch_too_old", name=name, cuda=cuda))
            return
        if name:
            log_func(t("cuda_headless", name=name))
            return
        log_func(t("cuda_unavailable"))
    except Exception:
        log_func("⚠ CUDA is not available — running on CPU (including AMD Radeon GPUs).")


def cuda_available(log_func=None, attempts=6, pause=0.75) -> bool:
    """True when CUDA answers. If the GPU powered down, poke the driver and retry."""
    if torch.cuda.is_available():
        return True
    from whisperfast.setup.gpu_info import poke_nvidia_gpu, torch_build_too_old_for

    misses = 0
    announced = False
    for _ in range(attempts):
        if poke_nvidia_gpu():
            if torch_build_too_old_for(poke_nvidia_gpu.last_name):
                return False
            misses = 0
            if not announced and log_func is not None:
                announced = True
                try:
                    log_func(t("cuda_waking"))
                except Exception:
                    pass
        else:
            misses += 1
            if misses >= 2:
                return False
        time.sleep(pause)
        if torch.cuda.is_available():
            return True
    return False
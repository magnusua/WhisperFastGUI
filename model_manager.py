import gc
import torch
from faster_whisper import WhisperModel
from config import DEFAULT_MODEL, WHISPER_MODELS

# Импорт менеджера языков
try:
    from lang_manager import t
except ImportError:
    from i18n_fallback import t

class WhisperModelSingleton:
    """
    Класс-синглтон для управления моделью Whisper.
    Обеспечивает однократную загрузку и безопасную выгрузку из памяти.
    """
    _model = None
    _mode = None
    _model_name = None

    @classmethod
    def get(cls, log_func, mode, model_name=None):
        """
        Загружает модель, если она еще не в памяти или если сменилось устройство/модель.
        model_name — короткое имя (tiny, base, large-v3-turbo и т.д.) или None для DEFAULT_MODEL.
        """
        name = (model_name or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        if name not in WHISPER_MODELS:
            name = DEFAULT_MODEL

        # Определяем устройство (cuda или cpu)
        device = "cuda" if (mode in ["GPU", "AUTO"] and torch.cuda.is_available()) else "cpu"

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
            try:
                log_func(t("cuda_unavailable"))
            except Exception:
                log_func("⚠ CUDA is not available — running on CPU (including AMD Radeon GPUs).")

        need_load = (
            cls._model is None
            or cls._mode != mode
            or cls._model_name != name
        )
        if need_load:
            if cls._model is not None:
                cls._model = None
                cls._mode = None
                cls._model_name = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            log_func(t("initializing_model", model=name))
            log_func(t("device_info", device=device.upper(), precision=compute))
            try:
                cls._model = WhisperModel(name, device=device, compute_type=compute)
                cls._mode = mode
                cls._model_name = name
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
        if cls._model is not None:
            cls._model = None
            cls._mode = None
            cls._model_name = None
            
            # Принудительный запуск сборщика мусора Python
            gc.collect()
            
            # Очистка зарезервированной видеопамяти
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            print("AI Resources: Unloaded successfully.")

    @classmethod
    def reset(cls):
        """Сброс состояния: при следующем get() модель будет загружена заново."""
        cls._model = None
        cls._mode = None
        cls._model_name = None
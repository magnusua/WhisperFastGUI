import gc
import torch
from faster_whisper import WhisperModel
from config import DEFAULT_MODEL

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

    @classmethod
    def get(cls, log_func, mode):
        """
        Загружает модель, если она еще не в памяти или если сменилось устройство.
        """
        # Определяем устройство (cuda или cpu)
        device = "cuda" if (mode in ["GPU", "AUTO"] and torch.cuda.is_available()) else "cpu"

        # Определяем точность вычислений
        # Для новых GPU (compute capability >= 7.0) используем float16,
        # для более старых карт (например, GTX 1060, 6.1) и CPU — int8.
        if device == "cuda":
            try:
                major, minor = torch.cuda.get_device_capability(0)
            except Exception:
                major, minor = 0, 0

            if major >= 7:
                compute = "float16"
            else:
                compute = "int8"
        else:
            compute = "int8"

        # Если пользователь выбрал GPU или AUTO, но CUDA недоступна — явно логируем,
        # что работаем в режиме CPU (актуально для систем с AMD Radeon и без NVIDIA).
        if device == "cpu" and mode in ["GPU", "AUTO"] and not torch.cuda.is_available():
            try:
                log_func(t("cuda_unavailable"))
            except Exception:
                # На всякий случай, если t/log_func недоступны
                log_func("⚠ CUDA is not available — running on CPU (including AMD Radeon GPUs).")
        
        if cls._model is None or cls._mode != mode:
            log_func(t("initializing_model", model=DEFAULT_MODEL))
            log_func(t("device_info", device=device.upper(), precision=compute))
            
            try:
                cls._model = WhisperModel(
                    DEFAULT_MODEL, 
                    device=device, 
                    compute_type=compute
                )
                cls._mode = mode
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
            # Удаляем объект модели
            cls._model = None
            cls._mode = None
            
            # Принудительный запуск сборщика мусора Python
            gc.collect()
            
            # Очистка зарезервированной видеопамяти
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            print("AI Resources: Unloaded successfully.")

    @classmethod
    def reset(cls):
        """Сброс состояния без полной очистки кэша."""
        cls._model = None
        cls._mode = None
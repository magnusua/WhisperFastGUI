"""Перевірка та оновлення ваг Whisper (Hugging Face Hub) для faster-whisper."""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from whisperfast.config import WHISPER_MODELS, find_whisper_model_cache_path, get_whisper_cache_dir
from whisperfast.i18n import t

try:
    from faster_whisper.utils import _MODELS, download_model
except ImportError:
    _MODELS = {}
    download_model = None

try:
    from huggingface_hub import HfApi, scan_cache_dir, snapshot_download
except ImportError:
    HfApi = None
    scan_cache_dir = None
    snapshot_download = None

_MODEL_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

ModelUpdateEntry = Tuple[str, str, str]  # (model_name, local_rev_short, remote_rev_short)


def get_model_repo_id(model_name: str) -> Optional[str]:
    return _MODELS.get(model_name) if _MODELS else None


def _short_rev(revision: Optional[str]) -> str:
    if not revision:
        return "—"
    return revision[:8] if len(revision) >= 8 else revision


def get_local_model_revision(repo_id: str, cache_root: Optional[str] = None) -> Optional[str]:
    if not scan_cache_dir or not repo_id:
        return None
    cache_root = cache_root or get_whisper_cache_dir()
    try:
        cache_info = scan_cache_dir(cache_root)
    except Exception:
        return None
    for repo in cache_info.repos:
        if repo.repo_id == repo_id:
            ref = repo.refs.get("main")
            if ref:
                return ref.commit_hash
    return None


def get_remote_model_revision(repo_id: str) -> Optional[str]:
    if not HfApi or not repo_id:
        return None
    try:
        return HfApi().model_info(repo_id).sha
    except Exception:
        return None


def is_model_downloaded(model_name: str, cache_root: Optional[str] = None) -> bool:
    cache_root = cache_root or get_whisper_cache_dir()
    return bool(find_whisper_model_cache_path(cache_root, model_name))


def model_needs_update(model_name: str, cache_root: Optional[str] = None) -> bool:
    repo_id = get_model_repo_id(model_name)
    if not repo_id:
        return False
    cache_root = cache_root or get_whisper_cache_dir()
    local = get_local_model_revision(repo_id, cache_root)
    if not local:
        return False
    remote = get_remote_model_revision(repo_id)
    if not remote:
        return False
    return local != remote


def check_whisper_model_updates(
    model_names: Optional[List[str]] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> List[ModelUpdateEntry]:
    """
    Перевіряє завантажені моделі на наявність нової ревізії на Hub.
    Повертає список (ім'я моделі, локальна ревізія, віддалена ревізія).
    """
    cache_root = get_whisper_cache_dir()
    names = model_names or list(WHISPER_MODELS)
    updates: List[ModelUpdateEntry] = []

    if log_func:
        log_func(t("checking_model_updates"))

    for name in names:
        if name not in WHISPER_MODELS:
            continue
        repo_id = get_model_repo_id(name)
        if not repo_id:
            continue
        local = get_local_model_revision(repo_id, cache_root)
        if not local:
            continue
        remote = get_remote_model_revision(repo_id)
        if not remote:
            if log_func:
                log_func(t("model_update_check_failed", model=name))
            continue
        if local != remote:
            entry = (name, _short_rev(local), _short_rev(remote))
            updates.append(entry)
            if log_func:
                log_func(t("model_update_available", model=name, current=entry[1], latest=entry[2]))
        elif log_func:
            log_func(t("model_update_ok", model=name, revision=_short_rev(local)))

    return updates


def check_downloaded_whisper_model_updates(log_func: Optional[Callable[[str], None]] = None) -> List[ModelUpdateEntry]:
    """Перевіряє всі моделі, які вже є в локальному кеші."""
    cache_root = get_whisper_cache_dir()
    downloaded = [n for n in WHISPER_MODELS if is_model_downloaded(n, cache_root)]
    return check_whisper_model_updates(downloaded, log_func=log_func)


def update_whisper_model(
    model_name: str,
    log_func: Callable[[str], None] = print,
    force: bool = False,
) -> bool:
    """Завантажує/оновлює ваги моделі з Hugging Face Hub."""
    if not download_model and not snapshot_download:
        log_func(t("model_update_no_hub"))
        return False
    repo_id = get_model_repo_id(model_name)
    if not repo_id:
        log_func(t("model_update_unknown", model=model_name))
        return False
    cache_dir = get_whisper_cache_dir()
    log_func(t("model_updating", model=model_name))
    try:
        if force and snapshot_download:
            snapshot_download(
                repo_id,
                cache_dir=cache_dir,
                allow_patterns=_MODEL_ALLOW_PATTERNS,
                force_download=True,
                local_files_only=False,
            )
        elif download_model:
            download_model(model_name, cache_dir=cache_dir, local_files_only=False)
        else:
            snapshot_download(
                repo_id,
                cache_dir=cache_dir,
                allow_patterns=_MODEL_ALLOW_PATTERNS,
                local_files_only=False,
            )
        log_func(t("model_update_done", model=model_name))
        return True
    except Exception as e:
        log_func(t("model_update_error", model=model_name, error=str(e)))
        return False


def apply_whisper_model_updates(
    model_names: List[str],
    log_func: Callable[[str], None] = print,
    force: bool = False,
) -> None:
    for name in model_names:
        update_whisper_model(name, log_func=log_func, force=force)

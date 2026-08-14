"""
Модуль для добавления файлов в очередь обработки.
Поддерживает добавление одного файла, группы файлов и каталогов (рекурсивно).
Централизованная логика валидации и обработки всех способов добавления файлов.

Функции в этом модуле — чистая логика без зависимости от Tkinter (валидация,
фильтрация, разбор Drag & Drop, запись в очередь), поэтому его можно тестировать
без запущенного GUI. Диалоги выбора файла/каталога (которые ПОКАЗЫВАЮТ Tk-окно
пользователю) вынесены в whisperfast/ui/dialogs.py — add_single_file(),
add_multiple_files(), add_directory() — см. docs/INTERNAL-ARCHITECTURE.uk.md и
docs/CODE-REVIEW.md, раздел 1.
"""
import os
from whisperfast.config import (
    VALID_EXTS,
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
)

from whisperfast.i18n import t
from whisperfast.utils import make_queue_item, normalize_queue_path


def get_file_dialog_filetypes():
    """Единый список типов файлов для диалогов выбора (один/несколько файлов)."""
    exts_str = ";".join(f"*{e}" for e in VALID_EXTS)
    audio_exts = ";".join(f"*{e}" for e in AUDIO_EXTENSIONS)
    video_exts = ";".join(f"*{e}" for e in VIDEO_EXTENSIONS)
    doc_exts = ";".join(f"*{e}" for e in DOCUMENT_EXTENSIONS)
    return [
        (t("all_supported"), exts_str),
        (t("audio_files"), audio_exts or exts_str),
        (t("video_files"), video_exts or exts_str),
        (t("document_files"), doc_exts or exts_str),
        (t("all_files_type"), "*.*"),
    ]


def is_valid_file(file_path):
    """
    Проверяет, является ли файл валидным для обработки.
    
    Args:
        file_path: Путь к файлу
    
    Returns:
        True если файл валидный, False иначе
    """
    if not os.path.isfile(file_path):
        return False
    return file_path.lower().endswith(VALID_EXTS)


def validate_and_filter_files(file_paths, existing_files=None):
    """
    Валидирует и фильтрует список файлов.
    
    Args:
        file_paths: Список путей к файлам
        existing_files: Список уже существующих файлов (для исключения дубликатов)
    
    Returns:
        Кортеж (valid_files, invalid_files, duplicate_files):
        - valid_files: Список валидных новых файлов
        - invalid_files: Список невалидных файлов
        - duplicate_files: Список дубликатов
    """
    if existing_files is None:
        existing_files = []
    
    valid_files = []
    invalid_files = []
    duplicate_files = []
    
    for file_path in file_paths:
        # Нормализация пути
        file_path = os.path.normpath(file_path)
        
        # Проверка на дубликат
        if file_path in existing_files:
            duplicate_files.append(file_path)
            continue
        
        # Проверка валидности
        if is_valid_file(file_path):
            valid_files.append(file_path)
        else:
            invalid_files.append(file_path)
    
    return valid_files, invalid_files, duplicate_files


def process_dropped_files(dropped_data, tk_root=None):
    """
    Обрабатывает данные из Drag & Drop события.
    Поддерживает файлы и каталоги.
    
    Args:
        dropped_data: Данные из события Drop (строка или список)
        tk_root: Корневое окно Tkinter (опционально, для использования splitlist)
    
    Returns:
        Список путей к файлам (включая файлы из каталогов)
    """
    if not dropped_data:
        return []
    
    # Разделяем пути (tkinterdnd2 использует специальный формат)
    paths = []
    try:
        # Если передан tk_root, используем splitlist для корректной обработки путей с пробелами
        if tk_root and hasattr(tk_root, 'tk'):
            paths = list(tk_root.tk.splitlist(dropped_data))
        # Если это уже список, используем как есть
        elif isinstance(dropped_data, (list, tuple)):
            paths = list(dropped_data)
        else:
            # Иначе пытаемся разделить строку
            # tkinterdnd2 может передавать как строку с фигурными скобками
            paths = dropped_data.replace('{', '').replace('}', '').split()
    except (AttributeError, TypeError, ValueError):
        paths = [dropped_data] if dropped_data else []
    
    all_files = []
    
    for path in paths:
        if not path:
            continue
        
        path = os.path.normpath(path.strip())
        
        if not path:
            continue
        
        if os.path.isfile(path):
            # Это файл - добавляем если валидный
            if is_valid_file(path):
                all_files.append(path)
        elif os.path.isdir(path):
            # Это каталог - получаем все валидные файлы рекурсивно
            dir_files = get_valid_files_from_directory(path, recursive=True)
            all_files.extend(dir_files)
    
    return all_files


def get_valid_files_from_directory(directory, recursive=True):
    """
    Получает список всех валидных файлов из каталога.
    
    Args:
        directory: Путь к каталогу
        recursive: Если True, обрабатывает вложенные каталоги рекурсивно
    
    Returns:
        Список путей к валидным файлам
    """
    valid_files = []
    
    if not os.path.isdir(directory):
        return valid_files
    
    try:
        if recursive:
            # Рекурсивный обход всех подкаталогов
            for root, dirs, files in os.walk(directory):
                for file in files:
                    file_path = os.path.join(root, file)
                    if is_valid_file(file_path):
                        valid_files.append(file_path)
        else:
            # Только файлы в корне каталога
            for file in os.listdir(directory):
                file_path = os.path.join(directory, file)
                if is_valid_file(file_path):
                    valid_files.append(file_path)
    except (PermissionError, OSError) as e:
        if not isinstance(e, PermissionError):
            try:
                from whisperfast.i18n import t
                print(t("dir_scan_error", directory=directory, error=str(e)))
            except ImportError:
                print(f"{directory}: {e}")
    return valid_files


def add_files_to_queue_controller(file_paths, queue, queue_list_or_treeview, log_func=None):
    """
    Универсальный контроллер для добавления файлов в очередь.
    queue — список dict с ключами path, start, end_segment_1, end_segment_2, end.
    queue_list_or_treeview — Treeview: добавляем строки через .insert().
    Возвращает (added_count, skipped_count), изменяет queue и виджет.
    """
    if not file_paths:
        return 0, 0

    existing_paths = [q["path"] for q in queue] if queue else []
    valid_files, invalid_files, duplicate_files = validate_and_filter_files(file_paths, existing_files=existing_paths)

    added_count = 0
    for file_path in valid_files:
        path_norm = normalize_queue_path(file_path) or file_path
        item = make_queue_item(path_norm)
        queue.append(item)
        num = len(queue)
        name = os.path.basename(file_path)
        status_text = t("status_not_processed")
        values = (num, name, item["start"], item["end_segment_1"], item["end_segment_2"], item["end"], status_text)
        queue_list_or_treeview.insert("", "end", values=values)
        added_count += 1

    skipped_count = len(invalid_files) + len(duplicate_files)
    if log_func:
        if added_count > 0:
            log_func(t("added_to_queue", count=added_count))
        if duplicate_files:
            log_func(t("skipped_duplicates", count=len(duplicate_files)))
        if invalid_files:
            log_func(t("skipped_invalid", count=len(invalid_files)))
        if skipped_count > 0 and added_count == 0:
            log_func(t("failed_to_add"))
    return added_count, skipped_count

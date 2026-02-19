"""
Модуль для добавления файлов в очередь обработки.
Поддерживает добавление одного файла, группы файлов и каталогов (рекурсивно).
Централизованная логика валидации и обработки всех способов добавления файлов.
"""
import os
from tkinter import filedialog, messagebox
from config import VALID_EXTS, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, DEFAULT_START_TIMESTAMP

# Импорт менеджера языков
try:
    from lang_manager import t
except ImportError:
    from i18n_fallback import t


def get_file_dialog_filetypes():
    """Единый список типов файлов для диалогов выбора (один/несколько файлов)."""
    exts_str = ";".join(f"*{e}" for e in VALID_EXTS)
    audio_exts = ";".join(f"*{e}" for e in AUDIO_EXTENSIONS)
    video_exts = ";".join(f"*{e}" for e in VIDEO_EXTENSIONS)
    return [
        (t("all_supported"), exts_str),
        (t("audio_files"), audio_exts or exts_str),
        (t("video_files"), video_exts or exts_str),
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
            print(f"Ошибка при сканировании каталога {directory}: {e}")
    return valid_files


def add_single_file():
    """
    Диалог выбора одного файла.
    
    Returns:
        Путь к выбранному файлу или None
    """
    file_path = filedialog.askopenfilename(title=t("select_file"), filetypes=get_file_dialog_filetypes())
    
    if file_path and is_valid_file(file_path):
        return file_path
    elif file_path:
        messagebox.showwarning(
            t("unsupported_format"),
            t("unsupported_format_msg", filename=os.path.basename(file_path), formats=', '.join(VALID_EXTS))
        )
    
    return None


def add_multiple_files():
    """
    Диалог выбора нескольких файлов.
    
    Returns:
        Список путей к выбранным файлам
    """
    file_paths = filedialog.askopenfilenames(title=t("select_files"), filetypes=get_file_dialog_filetypes())
    
    if not file_paths:
        return []
    
    valid_files, invalid_files, _ = validate_and_filter_files(file_paths)
    
    if invalid_files:
        invalid_names = [os.path.basename(f) for f in invalid_files[:5]]
        files_str = ', '.join(invalid_names) + ("..." if len(invalid_files) > 5 else "")
        messagebox.showwarning(
            t("unsupported_formats"),
            t("unsupported_formats_msg", files=files_str)
        )
    
    return valid_files


def add_directory(recursive=True):
    """
    Диалог выбора каталога с добавлением всех валидных файлов из него.
    
    Args:
        recursive: Если True, обрабатывает вложенные каталоги рекурсивно
    
    Returns:
        Список путей к валидным файлам из каталога
    """
    directory = filedialog.askdirectory(
        title=t("select_directory")
    )
    
    if not directory:
        return []
    
    if not os.path.isdir(directory):
        messagebox.showerror(t("error_not_directory"), t("error_not_directory_msg"))
        return []
    
    valid_files = get_valid_files_from_directory(directory, recursive=recursive)
    
    if not valid_files:
        messagebox.showinfo(
            t("files_not_found"),
            t("files_not_found_msg", dirname=os.path.basename(directory), formats=', '.join(VALID_EXTS))
        )
    else:
        messagebox.showinfo(
            t("files_added"),
            t("files_added_msg", count=len(valid_files))
        )
    
    return valid_files


def _default_queue_item(path):
    """Формирует элемент очереди (dict) с временами по умолчанию."""
    from utils import get_audio_duration_seconds, format_timestamp
    duration = get_audio_duration_seconds(path) or 0.0
    end_ts = format_timestamp(duration) if duration > 0 else DEFAULT_START_TIMESTAMP
    return {
        "path": path,
        "start": DEFAULT_START_TIMESTAMP,
        "end_segment_1": "",
        "end_segment_2": "",
        "end": end_ts,
    }


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
        item = _default_queue_item(file_path)
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

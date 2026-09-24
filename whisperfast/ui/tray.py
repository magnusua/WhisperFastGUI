"""System tray helpers for FTW."""
import os
import threading

from whisperfast.i18n import t

try:
    import pystray
    from pystray import MenuItem as TrayMenuItem
    from PIL import Image
    TRAY_OK = True
except ImportError:
    TRAY_OK = False
    pystray = None
    TrayMenuItem = None
    Image = None


def capture_session_running() -> bool:
    """True while a meeting capture is in progress (including pause)."""
    try:
        from whisperfast.core.capture import get_capture_session

        return bool(get_capture_session().running)
    except Exception:
        return False


def setup_tray(app):
    """Запуск иконки в системном трее (если доступны pystray и Pillow). Не создаёт трей в режиме «Панель»."""
    if app.tray_mode.get() == "panel":
        return
    if not TRAY_OK:
        app.log(t("warning_tray_unavailable"))
        return
    if app._tray_icon:
        return
    width, height = 64, 64
    img = None
    if os.path.exists(app._icon_path):
        try:
            img = Image.open(app._icon_path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            if img.size != (width, height):
                img = img.resize((width, height), Image.Resampling.LANCZOS)
        except Exception:
            img = None
    if img is None:
        # Резервна іконка, якщо favicon.ico відсутній — простий сірий квадрат
        img = Image.new("RGBA", (width, height), (80, 80, 80, 255))

    def show_window(icon, item):
        app.root.after(0, lambda: tray_show_window(app))

    def quit_app(icon, item):
        app.root.after(0, lambda: tray_quit(app))

    def capture_label(item):
        del item
        from whisperfast.core.capture import get_capture_session

        return t("capture_stop") if get_capture_session().running else t("capture_start")

    def pause_label(item):
        del item
        from whisperfast.core.capture import get_capture_session

        session = get_capture_session()
        if session.paused:
            return t("capture_resume")
        return t("capture_pause")

    def recording_visible(item):
        del item
        return capture_session_running()

    menu = pystray.Menu(
        TrayMenuItem(t("tray_show_window"), show_window, default=True),
        TrayMenuItem(capture_label, lambda icon, item: app.root.after(0, lambda: _tray_capture(app))),
        TrayMenuItem(
            pause_label,
            lambda icon, item: app.root.after(0, lambda: _tray_pause(app)),
            visible=recording_visible,
        ),
        TrayMenuItem(
            t("capture_clip"),
            lambda icon, item: app.root.after(0, lambda: _tray_clip(app)),
            visible=recording_visible,
        ),
        TrayMenuItem(t("capture_settings"), lambda icon, item: app.root.after(0, lambda: _tray_settings(app))),
        TrayMenuItem(t("archive_button"), lambda icon, item: app.root.after(0, lambda: _tray_archive(app))),
        TrayMenuItem(t("exit"), quit_app),
    )
    app._tray_icon = pystray.Icon("ftw", img, t("app_title"), menu)
    threading.Thread(target=app._tray_icon.run, daemon=True).start()


def apply_tray_mode(app):
    """Применяет выбранный режим: Панель (без трея), Трей (только трей), Панель + Трей."""
    mode = app.tray_mode.get()
    if mode == "panel":
        if app._tray_icon:
            try:
                app._tray_icon.stop()
            except Exception:
                pass
            app._tray_icon = None
        app.root.deiconify()
    else:
        # Відкладений запуск трею: на Windows іконка часто не з'являється, якщо створювати її до готовності панелі задач
        def delayed_tray():
            setup_tray(app)
            if mode == "tray" and app._tray_icon:
                app.root.withdraw()
            else:
                app.root.deiconify()
        app.root.after(500, delayed_tray)


def tray_show_window(app):
    """Показать окно из трея (вызывается в main thread)."""
    app.root.deiconify()
    app.root.lift()
    app.root.focus_force()


def tray_quit(app):
    """Закрытие по пункту «Выход» в трее (вызывается в main thread)."""
    if app._on_close_request:
        app._on_close_request()


def _tray_capture(app):
    from whisperfast.ui import capture_ui

    capture_ui.toggle_capture(app)


def _tray_pause(app):
    from whisperfast.ui import capture_ui

    capture_ui.toggle_pause(app)


def _tray_clip(app):
    from whisperfast.ui import capture_ui

    capture_ui.save_clip(app)


def _tray_settings(app):
    from whisperfast.ui.capture_settings import show_capture_settings_dialog

    show_capture_settings_dialog(app)


def _tray_archive(app):
    from whisperfast.ui.archive import show_archive_window

    tray_show_window(app)
    show_archive_window(app)

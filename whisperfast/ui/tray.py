"""System tray helpers for WhisperGUI."""
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

    menu = pystray.Menu(
        TrayMenuItem(t("tray_show_window"), show_window, default=True),
        TrayMenuItem(t("exit"), quit_app),
    )
    app._tray_icon = pystray.Icon("whisper_fast_gui", img, t("app_title"), menu)
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


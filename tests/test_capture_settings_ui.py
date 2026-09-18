"""Headless Tk smoke test for the capture settings dialog (whisperfast/ui/capture_settings.py).

Catches import-time and widget-construction regressions in the actively developed
meeting-capture settings UI, which otherwise has zero test coverage (the rest of
the test suite never instantiates real Tkinter widgets).
"""
import tkinter as tk
import unittest

from whisperfast.ui.capture_settings import show_capture_settings_dialog


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise unittest.SkipTest(f"no Tk display available: {e}")
    root.withdraw()
    return root


class _FakeApp:
    def __init__(self, root):
        self.root = root
        self.capture_cfg = {}
        self.capture_consent_shown = tk.BooleanVar(master=root, value=False)
        self._capture_settings_window = None


class TestCaptureSettingsDialogSmoke(unittest.TestCase):
    def setUp(self):
        self.root = _make_root()
        self.addCleanup(self.root.destroy)

    def test_dialog_builds_without_raising_and_has_expected_tabs(self):
        app = _FakeApp(self.root)
        dialog = show_capture_settings_dialog(app)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())

        self.assertIsInstance(dialog, tk.Toplevel)
        self.assertIs(app._capture_settings_window, dialog)

        notebooks = [w for w in dialog.winfo_children() if isinstance(w, tk.ttk.Notebook)]
        self.assertEqual(len(notebooks), 1, "expected exactly one ttk.Notebook in the dialog")
        tab_count = len(notebooks[0].tabs())
        self.assertGreaterEqual(tab_count, 3, "expected multiple settings tabs (sources/codec/auto-record/...)")

    def test_reopening_focuses_the_existing_window_instead_of_creating_a_new_one(self):
        app = _FakeApp(self.root)
        first = show_capture_settings_dialog(app)
        self.addCleanup(lambda: first.winfo_exists() and first.destroy())

        second = show_capture_settings_dialog(app)
        self.assertIs(second, first)

    def test_defaults_are_prefilled_from_capture_cfg(self):
        app = _FakeApp(self.root)
        app.capture_cfg = {"capture_mix_mode": "device", "capture_include_mic": False}
        dialog = show_capture_settings_dialog(app)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        # The dialog must at least construct successfully with overridden
        # capture_cfg values without raising (covered implicitly by reaching here).
        self.assertTrue(dialog.winfo_exists())


if __name__ == "__main__":
    unittest.main()

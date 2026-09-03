"""pythonw.exe must not be used for pip install."""
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperfast.setup.installer import _pip_python
from whisperfast.setup.python_selector import _to_python_exe


class TestToPythonExe(unittest.TestCase):
    def test_pythonw_maps_to_sibling_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            pythonw = os.path.join(tmp, "pythonw.exe")
            python = os.path.join(tmp, "python.exe")
            for path in (pythonw, python):
                with open(path, "wb") as f:
                    f.write(b"")
            self.assertEqual(
                os.path.normcase(_to_python_exe(pythonw)),
                os.path.normcase(python),
            )

    def test_python_exe_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = os.path.join(tmp, "python.exe")
            with open(python, "wb") as f:
                f.write(b"")
            self.assertEqual(_to_python_exe(python), python)

    def test_pip_python_rewrites_pythonw(self):
        with tempfile.TemporaryDirectory() as tmp:
            pythonw = os.path.join(tmp, "pythonw.exe")
            python = os.path.join(tmp, "python.exe")
            for path in (pythonw, python):
                with open(path, "wb") as f:
                    f.write(b"")
            with patch("whisperfast.setup.installer.sys.executable", pythonw):
                self.assertEqual(
                    os.path.normcase(_pip_python()),
                    os.path.normcase(python),
                )


class TestInstallRunHint(unittest.TestCase):
    def test_hint_points_to_vbs_not_bat(self):
        from whisperfast.i18n.lang_manager import t

        for lang in ("EN", "UK", "RU"):
            with patch("whisperfast.i18n.lang_manager._current_language", lang):
                text = t("install_run_hint")
            self.assertIn("run_whisper.vbs", text)
            self.assertNotIn("run_whisper.bat", text)


class TestPipRetryAndTorchFallback(unittest.TestCase):
    def test_group_failure_retries_each_spec(self):
        from whisperfast.setup import installer as inst

        calls = []

        def fake_run(cmd, log_func, timeout=600, summarize=True):
            calls.append(list(cmd))
            pkgs = [p for p in cmd[5:] if not str(p).startswith("-")]
            if len(pkgs) > 1:
                return 1
            return 0 if pkgs == ["pygame"] else 1

        logs = []
        with patch.object(inst, "_run_install_cmd", side_effect=fake_run):
            with patch.object(inst, "_pip_python", return_value="python"):
                code = inst._run_pip_specs(["pygame", "pydub"], logs.append, summarize=True)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 3)
        self.assertTrue(any(len([p for p in c[5:] if not str(p).startswith("-")]) > 1 for c in calls))

    def test_cuda_torch_failure_retries_cpu_index(self):
        from whisperfast.setup import installer as inst

        cmds = []

        def fake_run(cmd, log_func, timeout=600, summarize=True):
            cmds.append(list(cmd))
            if "--index-url" in cmd:
                return 1
            return 0

        logs = []
        with patch.object(inst, "_run_install_cmd", side_effect=fake_run):
            with patch.object(inst, "_pip_python", return_value="python"):
                code = inst._run_torch_install(logs.append, use_cuda=True, summarize=True)
        self.assertEqual(code, 0)
        self.assertTrue(any("--index-url" in c for c in cmds))
        self.assertTrue(any("--index-url" not in c and "torch" in c for c in cmds))


class TestCudaCliOverride(unittest.TestCase):
    def test_parse_cuda_cpu_auto(self):
        from whisperfast.setup.installer import parse_installer_argv

        self.assertTrue(parse_installer_argv(["--cuda"]))
        self.assertFalse(parse_installer_argv(["--cpu"]))
        self.assertIsNone(parse_installer_argv([]))
        self.assertTrue(parse_installer_argv(["--cpu", "--cuda"]))

    def test_user_yes_overrides_false_detect(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "refresh_gpu_settings", return_value=(False, None)):
            with patch.object(inst, "save_app_settings") as save:
                use_cuda, include_nvidia, _ = inst._resolve_cuda_choice(True)
        self.assertTrue(use_cuda)
        self.assertTrue(include_nvidia)
        save.assert_called()
        self.assertTrue(save.call_args[0][0]["has_nvidia"])

    def test_user_no_skips_even_if_detected(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "refresh_gpu_settings", return_value=(True, "RTX")):
            with patch.object(inst, "save_app_settings") as save:
                use_cuda, include_nvidia, _ = inst._resolve_cuda_choice(False)
        self.assertFalse(use_cuda)
        self.assertFalse(include_nvidia)
        save.assert_called_with({"has_nvidia": False})


if __name__ == "__main__":
    unittest.main()

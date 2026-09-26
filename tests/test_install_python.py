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

    def test_old_cuda_wheel_is_uninstalled_before_cu128(self):
        from whisperfast.setup import installer as inst

        cmds = []

        def fake_run(cmd, log_func, timeout=600, summarize=True):
            cmds.append(list(cmd))
            return 0

        with patch.object(inst.importlib.metadata, "version", return_value="2.5.1+cu121"):
            with patch.object(inst, "_run_install_cmd", side_effect=fake_run):
                with patch.object(inst, "_pip_python", return_value="python"):
                    code = inst._run_torch_install(lambda _line: None, use_cuda=True, summarize=True)
        self.assertEqual(code, 0)
        self.assertEqual(cmds[0][3], "uninstall")
        self.assertIn("--index-url", cmds[1])
        self.assertIn("cu128", " ".join(cmds[1]))

    def test_progress_bar_flag_is_install_only(self):
        from whisperfast.setup import installer as inst

        install = inst._pip_cmd_quiet_progress(
            ["python", "-m", "pip", "install", "--upgrade", "torch"]
        )
        uninstall = inst._pip_cmd_quiet_progress(
            ["python", "-m", "pip", "uninstall", "-y", "torch"]
        )
        self.assertEqual(install[-2:], ["--progress-bar", "off"])
        self.assertNotIn("--progress-bar", uninstall)

    def test_cu128_wheel_is_not_removed(self):
        from whisperfast.setup import installer as inst

        cmds = []

        def fake_run(cmd, log_func, timeout=600, summarize=True):
            cmds.append(list(cmd))
            return 0

        with patch.object(inst.importlib.metadata, "version", return_value="2.8.0+cu128"):
            with patch.object(inst, "_run_install_cmd", side_effect=fake_run):
                with patch.object(inst, "_pip_python", return_value="python"):
                    inst._run_torch_install(lambda _line: None, use_cuda=True, summarize=True)
        self.assertNotIn("uninstall", cmds[0])

    def test_pypi_cuda13_wheel_is_not_downgraded_to_cu128(self):
        from whisperfast.setup import installer as inst

        cmds = []

        def fake_run(cmd, log_func, timeout=600, summarize=True):
            cmds.append(list(cmd))
            return 0

        with patch.object(inst.importlib.metadata, "version", return_value="2.14.0"):
            with patch("whisperfast.setup.gpu_info.installed_torch_cuda", return_value=(13, 0)):
                with patch.object(inst, "_run_install_cmd", side_effect=fake_run):
                    with patch.object(inst, "_pip_python", return_value="python"):
                        code = inst._run_torch_install(
                            lambda _line: None, use_cuda=True, force=True, summarize=True
                        )
        self.assertEqual(code, 0)
        self.assertEqual(len(cmds), 1)
        self.assertNotIn("uninstall", cmds[0])
        self.assertNotIn("cu128", " ".join(cmds[0]))
        self.assertIn("--force-reinstall", cmds[0])

    def test_cuda_level_from_pypi_requires(self):
        from whisperfast.setup.installer import _cuda_level_from_requires

        reqs = [
            "cuda-toolkit==13.0.3",
            "nvidia-cudnn-cu13==9.24.0.43",
            'nvidia-cuda-nvrtc-cu12==12.4.127; platform_system == "Linux"',
        ]
        self.assertEqual(_cuda_level_from_requires(reqs), (13, 0))
        self.assertEqual(
            _cuda_level_from_requires(["nvidia-cublas-cu128==12.8.0"]),
            (12, 8),
        )


class TestCudaCliOverride(unittest.TestCase):
    def test_parse_cuda_cpu_auto(self):
        from whisperfast.setup.installer import parse_installer_argv

        self.assertTrue(parse_installer_argv(["--cuda"]))
        self.assertFalse(parse_installer_argv(["--cpu"]))
        self.assertIsNone(parse_installer_argv([]))
        self.assertTrue(parse_installer_argv(["--cpu", "--cuda"]))

    def test_user_yes_overrides_false_detect(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "nvidia_from_settings", return_value=(False, "")):
            with patch.object(inst, "refresh_gpu_settings", return_value=(False, None)):
                with patch.object(inst, "save_app_settings") as save:
                    use_cuda, include_nvidia, _ = inst._resolve_cuda_choice(True)
        self.assertTrue(use_cuda)
        self.assertTrue(include_nvidia)
        save.assert_called()
        self.assertTrue(save.call_args[0][0]["has_nvidia"])

    def test_user_no_skips_even_if_detected(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "nvidia_from_settings", return_value=(False, "")):
            with patch.object(inst, "refresh_gpu_settings", return_value=(True, "RTX")):
                with patch.object(inst, "save_app_settings") as save:
                    use_cuda, include_nvidia, _ = inst._resolve_cuda_choice(False)
        self.assertFalse(use_cuda)
        self.assertFalse(include_nvidia)
        save.assert_called_with({"has_nvidia": False})

    def test_saved_nvidia_skips_probe_and_installs_cuda_stack(self):
        from whisperfast.setup import installer as inst

        with patch.object(
            inst, "nvidia_from_settings", return_value=(True, "NVIDIA GeForce RTX 4090")
        ):
            with patch.object(inst, "refresh_gpu_settings") as refresh:
                with patch.object(inst, "save_app_settings") as save:
                    use_cuda, include_nvidia, name = inst._resolve_cuda_choice(None)
        refresh.assert_not_called()
        self.assertTrue(use_cuda)
        self.assertTrue(include_nvidia)
        self.assertEqual(name, "NVIDIA GeForce RTX 4090")
        save.assert_called_with(
            {"has_nvidia": True, "gpu_model": "NVIDIA GeForce RTX 4090"}
        )

    def test_auto_detect_also_installs_nvidia_libs(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "nvidia_from_settings", return_value=(False, "")):
            with patch.object(
                inst, "refresh_gpu_settings", return_value=(True, "NVIDIA GeForce RTX 4090")
            ):
                use_cuda, include_nvidia, name = inst._resolve_cuda_choice(None)
        self.assertTrue(use_cuda)
        self.assertTrue(include_nvidia)
        self.assertEqual(name, "NVIDIA GeForce RTX 4090")


class TestAudioopShim(unittest.TestCase):
    def test_multimedia_starts_with_audioop_lts_on_313(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "needs_pyaudioop", return_value=True):
            with patch.object(inst, "audioop_available", return_value=False):
                specs = inst._multimedia_required_specs()
        self.assertEqual(specs[0], "audioop-lts")

    def test_run_pip_specs_rewrites_pyaudioop(self):
        from whisperfast.setup import installer as inst

        cmds = []

        def fake_run(cmd, log_func, timeout=600, summarize=True):
            cmds.append(list(cmd))
            return 0

        with patch.object(inst, "_run_install_cmd", side_effect=fake_run):
            with patch.object(inst, "_pip_python", return_value="python"):
                inst._run_pip_specs(
                    ["pygame", "pyaudioop"], lambda *_: None, retry_each=False
                )
        specs = [p for p in cmds[0][5:] if not str(p).startswith("-")]
        self.assertIn("audioop-lts", specs)
        self.assertNotIn("pyaudioop", specs)

    def test_multimedia_omits_shim_below_313(self):
        from whisperfast.setup import installer as inst

        with patch.object(inst, "needs_pyaudioop", return_value=False):
            specs = inst._multimedia_required_specs()
        self.assertNotIn("audioop-lts", specs)
        self.assertNotIn("pyaudioop", specs)

    def test_ensure_audioop_shim_skips_when_available(self):
        from whisperfast.setup import installer as inst

        logs = []
        with patch.object(inst, "needs_pyaudioop", return_value=True):
            with patch.object(inst, "audioop_available", return_value=True):
                with patch.object(inst, "_run_pip_specs") as pip:
                    ok = inst.ensure_audioop_shim(logs.append)
        self.assertTrue(ok)
        pip.assert_not_called()

    def test_ensure_audioop_shim_tries_audioop_lts_first(self):
        from whisperfast.setup import installer as inst

        logs = []
        available = [False, True]

        def fake_available():
            return available.pop(0)

        with patch.object(inst, "needs_pyaudioop", return_value=True):
            with patch.object(inst, "audioop_available", side_effect=fake_available):
                with patch.object(inst, "_run_pip_specs", return_value=0) as pip:
                    ok = inst.ensure_audioop_shim(logs.append)
        self.assertTrue(ok)
        pip.assert_called_once()
        self.assertEqual(pip.call_args[0][0], ["audioop-lts"])


class TestPypiPythonFilter(unittest.TestCase):
    def test_skips_release_that_needs_a_newer_python(self):
        from whisperfast.setup.installer import latest_compatible_pypi_version

        payload = {
            "info": {"version": "2.5.3", "requires_python": ">=3.12"},
            "releases": {
                "2.4.6": [{"requires_python": ">=3.11", "yanked": False}],
                "2.5.3": [{"requires_python": ">=3.12", "yanked": False}],
            },
        }
        self.assertEqual(
            latest_compatible_pypi_version(payload, version_info=(3, 11, 9)),
            "2.4.6",
        )
        self.assertEqual(
            latest_compatible_pypi_version(payload, version_info=(3, 12, 10)),
            "2.5.3",
        )

    def test_installed_newer_than_index_is_not_an_update(self):
        from whisperfast.setup.installer import _torch_needs_update, _version_is_newer

        self.assertFalse(_version_is_newer("2.4.6", "2.4.6"))
        self.assertTrue(_version_is_newer("2.5.3", "2.4.6"))
        # Same CUDA family: a higher local version is not an update.
        self.assertFalse(_torch_needs_update("2.14.0+cu121", "2.5.1+cu121"))
        self.assertTrue(_torch_needs_update("2.4.0", "2.5.1+cu121"))
        # CPU wheel → CUDA index for any NVIDIA, even when the CPU number is higher.
        self.assertTrue(_torch_needs_update("2.14.0+cpu", "2.11.0+cu128"))
        self.assertTrue(
            _torch_needs_update(
                "2.14.0+cpu",
                "2.11.0+cu128",
                gpu_name="NVIDIA GeForce RTX 3060",
            )
        )
        rtx50 = "NVIDIA GeForce RTX 5070 Laptop GPU"
        self.assertTrue(_torch_needs_update("2.14.0+cpu", "2.14.0+cu128", gpu_name=rtx50))
        self.assertTrue(_torch_needs_update("2.14.0+cu121", "2.7.0+cu128", gpu_name=rtx50))
        self.assertFalse(_torch_needs_update("2.8.0+cu128", "2.7.0+cu128", gpu_name=rtx50))
        self.assertFalse(_torch_needs_update("2.14.0+cu121", "2.5.1+cu121", gpu_name=rtx50))
        self.assertFalse(
            _torch_needs_update("2.14.0", "2.11.0+cu128", gpu_name=rtx50, cuda_level=(13, 0))
        )
        self.assertTrue(
            _torch_needs_update("2.14.0", "2.15.0+cu128", gpu_name=rtx50, cuda_level=(13, 0))
        )
        self.assertTrue(
            _torch_needs_update("2.14.0", "2.11.0+cu128", gpu_name=rtx50, cuda_level=None)
        )
        self.assertTrue(
            _torch_needs_update("2.14.0+cpu", "2.11.0+cu128", gpu_name=rtx50, cuda_level=(13, 0))
        )
        # Older CUDA tag on the machine vs cu128 index (non-Blackwell).
        self.assertTrue(
            _torch_needs_update(
                "2.5.1+cu121",
                "2.11.0+cu128",
                gpu_name="NVIDIA GeForce RTX 3060",
            )
        )


class TestCudaWake(unittest.TestCase):
    def test_wakes_a_sleeping_gpu_before_giving_up(self):
        from whisperfast.core import model_manager as mm

        seen = []
        answers = iter([False, False, True])

        def available():
            return next(answers)

        with patch.object(mm.torch.cuda, "is_available", side_effect=available):
            with patch("whisperfast.setup.gpu_info.poke_nvidia_gpu", return_value=True) as poke:
                with patch.object(mm.time, "sleep"):
                    ok = mm.cuda_available(log_func=seen.append, attempts=4, pause=0)
        self.assertTrue(ok)
        self.assertGreaterEqual(poke.call_count, 1)
        self.assertTrue(seen)

    def test_no_nvidia_does_not_wait(self):
        from whisperfast.core import model_manager as mm

        with patch.object(mm.torch.cuda, "is_available", return_value=False):
            with patch("whisperfast.setup.gpu_info.poke_nvidia_gpu", return_value=False) as poke:
                with patch.object(mm.time, "sleep") as sleep:
                    ok = mm.cuda_available(log_func=None, attempts=6, pause=1)
        self.assertFalse(ok)
        self.assertEqual(poke.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_gpu_mode_does_not_fall_back_to_cpu(self):
        from whisperfast.core.model_manager import GpuRequiredError
        from whisperfast.core import model_manager as mm

        with patch.object(mm.torch.cuda, "is_available", return_value=False):
            with patch("whisperfast.setup.gpu_info.prepare_nvidia_gpu"):
                with patch("whisperfast.setup.gpu_info.release_nvidia_display_client"):
                    with patch("whisperfast.setup.gpu_info.poke_nvidia_gpu", return_value=False):
                        with patch.object(mm.time, "sleep"):
                            with self.assertRaises(GpuRequiredError):
                                mm.WhisperModelSingleton.get(
                                    lambda _line: None, "GPU", keep_gpu_awake=True
                                )


if __name__ == "__main__":
    unittest.main()

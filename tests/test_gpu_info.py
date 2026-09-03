"""Saved NVIDIA gpu_model is trusted without a hardware probe."""
import unittest
from unittest.mock import patch

from whisperfast.setup.gpu_info import (
    gpu_model_looks_nvidia,
    install_gpu_status_line,
    nvidia_for_install,
    nvidia_from_settings,
    refresh_gpu_settings,
)


class TestGpuModelLooksNvidia(unittest.TestCase):
    def test_nvidia_prefix(self):
        self.assertTrue(gpu_model_looks_nvidia("NVIDIA GeForce RTX 4090"))
        self.assertTrue(gpu_model_looks_nvidia("nvidia tesla t4"))

    def test_rejects_empty_and_other_vendors(self):
        self.assertFalse(gpu_model_looks_nvidia(""))
        self.assertFalse(gpu_model_looks_nvidia("AMD Radeon RX 7900"))
        self.assertFalse(gpu_model_looks_nvidia("Intel UHD Graphics"))


class TestNvidiaFromSettings(unittest.TestCase):
    def test_trusts_gpu_model_even_if_has_nvidia_false(self):
        with patch(
            "whisperfast.setup.gpu_info.load_app_settings",
            return_value={"gpu_model": "NVIDIA GeForce RTX 4090", "has_nvidia": False},
        ):
            ok, name = nvidia_from_settings()
        self.assertTrue(ok)
        self.assertEqual(name, "NVIDIA GeForce RTX 4090")

    def test_empty_gpu_model_is_not_trusted(self):
        with patch(
            "whisperfast.setup.gpu_info.load_app_settings",
            return_value={"gpu_model": "", "has_nvidia": True},
        ):
            ok, name = nvidia_from_settings()
        self.assertFalse(ok)
        self.assertEqual(name, "")


class TestInstallGpuStatusLine(unittest.TestCase):
    def test_saved_skips_detect(self):
        with patch(
            "whisperfast.setup.gpu_info.nvidia_from_settings",
            return_value=(True, "NVIDIA GeForce RTX 4090"),
        ):
            with patch("whisperfast.setup.gpu_info.detect_nvidia_gpu") as detect:
                line = install_gpu_status_line()
        detect.assert_not_called()
        self.assertEqual(line, "SAVED:NVIDIA GeForce RTX 4090")

    def test_probe_when_settings_empty(self):
        with patch(
            "whisperfast.setup.gpu_info.nvidia_from_settings",
            return_value=(False, ""),
        ):
            with patch(
                "whisperfast.setup.gpu_info.detect_nvidia_gpu",
                return_value=(False, None),
            ):
                self.assertEqual(install_gpu_status_line(), "NOTFOUND")


class TestNvidiaForInstall(unittest.TestCase):
    def test_saved_does_not_probe(self):
        with patch(
            "whisperfast.setup.gpu_info.nvidia_from_settings",
            return_value=(True, "NVIDIA GeForce RTX 4090"),
        ):
            with patch("whisperfast.setup.gpu_info.save_app_settings") as save:
                with patch("whisperfast.setup.gpu_info.refresh_gpu_settings") as refresh:
                    has, name = nvidia_for_install()
        refresh.assert_not_called()
        self.assertTrue(has)
        self.assertEqual(name, "NVIDIA GeForce RTX 4090")
        save.assert_called_with(
            {"has_nvidia": True, "gpu_model": "NVIDIA GeForce RTX 4090"}
        )


class TestRefreshKeepsSavedNvidia(unittest.TestCase):
    def test_failed_probe_does_not_clear_saved_model(self):
        with patch(
            "whisperfast.setup.gpu_info.nvidia_from_settings",
            return_value=(True, "NVIDIA GeForce RTX 4090"),
        ):
            with patch(
                "whisperfast.setup.gpu_info.detect_nvidia_gpu",
                return_value=(False, None),
            ):
                with patch("whisperfast.setup.gpu_info.save_app_settings") as save:
                    has, name = refresh_gpu_settings()
        self.assertTrue(has)
        self.assertEqual(name, "NVIDIA GeForce RTX 4090")
        save.assert_called_with(
            {"has_nvidia": True, "gpu_model": "NVIDIA GeForce RTX 4090"}
        )


if __name__ == "__main__":
    unittest.main()

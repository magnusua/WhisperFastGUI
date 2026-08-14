"""Version comparison, GitHub Release asset picking, checksum-gated updates."""
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from whisperfast.i18n import t
from whisperfast.updates.app_updates import (
    _download_verified_release_zip,
    _format_release_date,
    _normalize_tag_version,
    _pick_release_assets,
    _version_is_newer,
    check_app_update,
    verify_detached_gpg_signature,
)
from whisperfast.updates.checksums import sha256_file


def _asset(name, url="https://example.invalid/" + "x", digest=""):
    item = {"name": name, "browser_download_url": url}
    if digest:
        item["digest"] = digest
    return item


class TestVersionIsNewer(unittest.TestCase):
    def test_unknown_local_version_is_never_older(self):
        self.assertFalse(_version_is_newer("1.2.11", "unknown"))
        self.assertFalse(_version_is_newer("unknown", "1.2.11"))
        self.assertFalse(_version_is_newer("1.2.11", "UNKNOWN"))
        self.assertFalse(_version_is_newer("", "1.2.10"))
        self.assertFalse(_version_is_newer("1.2.10", ""))
        self.assertFalse(_version_is_newer(None, "1.2.10"))
        self.assertFalse(_version_is_newer("1.2.10", "1.2.10"))

    def test_numeric_order_not_lexicographic(self):
        self.assertTrue(_version_is_newer("1.2.10", "1.2.9"))
        self.assertFalse(_version_is_newer("1.2.9", "1.2.10"))
        self.assertTrue(_version_is_newer("1.10.0", "1.9.0"))
        self.assertTrue(_version_is_newer("2.0.0", "1.9.9"))
        self.assertFalse(_version_is_newer("1.2.10", "1.2.11"))

    def test_patch_and_pre_not_required(self):
        self.assertTrue(_version_is_newer("1.3.0", "1.2.11"))


class TestReleaseHelpers(unittest.TestCase):
    def test_normalize_tag_version(self):
        self.assertEqual(_normalize_tag_version("v1.2.11"), "1.2.11")
        self.assertEqual(_normalize_tag_version("V2.0.0"), "2.0.0")
        self.assertEqual(_normalize_tag_version("1.2.11"), "1.2.11")
        self.assertEqual(_normalize_tag_version("  v1.0.0  "), "1.0.0")

    def test_format_release_date(self):
        self.assertEqual(_format_release_date("2026-08-14T12:00:00Z"), "14.08.2026")
        self.assertEqual(_format_release_date(""), "")

    def test_pick_prefers_whisperfastgui_zip_and_checksums(self):
        release = {
            "assets": [
                _asset("other.zip"),
                _asset("WhisperFastGUI-1.2.12-src.zip"),
                _asset("SHA256SUMS"),
                _asset("SHA256SUMS.asc"),
            ]
        }
        picked = _pick_release_assets(release)
        self.assertEqual(picked["zip"]["name"], "WhisperFastGUI-1.2.12-src.zip")
        self.assertEqual(picked["checksums"]["name"], "SHA256SUMS")
        self.assertEqual(picked["signature"]["name"], "SHA256SUMS.asc")

    def test_pick_missing_checksums(self):
        release = {"assets": [_asset("WhisperFastGUI-1.2.12-src.zip")]}
        picked = _pick_release_assets(release)
        self.assertIsNotNone(picked["zip"])
        self.assertIsNone(picked["checksums"])
        self.assertIsNone(picked["signature"])


class TestCheckAppUpdate(unittest.TestCase):
    def test_unknown_local_does_not_offer(self):
        with patch("whisperfast.updates.app_updates.get_local_app_version", return_value="unknown"):
            info = check_app_update()
        self.assertFalse(info["needs_update"])

    def test_no_release_does_not_offer(self):
        with patch("whisperfast.updates.app_updates.get_local_app_version", return_value="1.2.11"):
            with patch("whisperfast.updates.app_updates.fetch_latest_github_release", return_value=None):
                info = check_app_update()
        self.assertFalse(info["needs_update"])

    def test_newer_without_checksums_does_not_offer(self):
        release = {
            "tag_name": "v9.9.9",
            "published_at": "2026-08-15T00:00:00Z",
            "assets": [_asset("WhisperFastGUI-9.9.9-src.zip")],
        }
        logs = []
        with patch("whisperfast.updates.app_updates.get_local_app_version", return_value="1.2.11"):
            with patch(
                "whisperfast.updates.app_updates.fetch_latest_github_release",
                return_value=release,
            ):
                info = check_app_update(logs.append)
        self.assertFalse(info["needs_update"])
        self.assertEqual(info["remote"], "9.9.9")
        self.assertIn(t("app_update_checksum_missing"), logs)

    def test_newer_with_checksums_offers_update(self):
        release = {
            "tag_name": "v9.9.9",
            "published_at": "2026-08-15T00:00:00Z",
            "assets": [
                _asset("WhisperFastGUI-9.9.9-src.zip"),
                _asset("SHA256SUMS"),
            ],
        }
        with patch("whisperfast.updates.app_updates.get_local_app_version", return_value="1.2.11"):
            with patch(
                "whisperfast.updates.app_updates.fetch_latest_github_release",
                return_value=release,
            ):
                info = check_app_update()
        self.assertTrue(info["needs_update"])
        self.assertEqual(info["remote"], "9.9.9")
        self.assertEqual(info["remote_date"], "15.08.2026")


class TestDownloadVerifiedReleaseZip(unittest.TestCase):
    def test_refuses_without_checksum_asset(self):
        release = {
            "tag_name": "v9.9.9",
            "assets": [_asset("WhisperFastGUI-9.9.9-src.zip", "https://example.invalid/a.zip")],
        }
        logs = []
        with patch(
            "whisperfast.updates.app_updates.fetch_latest_github_release",
            return_value=release,
        ):
            result = _download_verified_release_zip(logs.append)
        self.assertIsNone(result)
        self.assertIn(t("app_update_checksum_missing"), logs)

    def test_refuses_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_name = "WhisperFastGUI-9.9.9-src.zip"
            src_zip = os.path.join(tmp, zip_name)
            with zipfile.ZipFile(src_zip, "w") as zf:
                zf.writestr("WhisperFastGUI-9.9.9/main.py", "print(1)\n")
            release = {
                "tag_name": "v9.9.9",
                "assets": [
                    _asset(zip_name, "https://example.invalid/a.zip"),
                    _asset("SHA256SUMS", "https://example.invalid/SHA256SUMS"),
                ],
            }

            def fake_download(url, dest, timeout=180):
                if "SHA256SUMS" in url:
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(("0" * 64) + f"  {zip_name}\n")
                else:
                    shutil.copy2(src_zip, dest)

            logs = []
            with patch("whisperfast.updates.app_updates.BASE_DIR", tmp):
                with patch(
                    "whisperfast.updates.app_updates.fetch_latest_github_release",
                    return_value=release,
                ):
                    with patch(
                        "whisperfast.updates.app_updates._signing_key_configured",
                        return_value=False,
                    ):
                        with patch(
                            "whisperfast.updates.app_updates._http_download",
                            side_effect=fake_download,
                        ):
                            result = _download_verified_release_zip(logs.append)
            self.assertIsNone(result)
            self.assertIn(t("app_update_checksum_mismatch"), logs)

    def test_accepts_matching_checksum_and_extracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_name = "WhisperFastGUI-9.9.9-src.zip"
            src_zip = os.path.join(tmp, zip_name)
            with zipfile.ZipFile(src_zip, "w") as zf:
                zf.writestr("WhisperFastGUI-9.9.9/main.py", "print(1)\n")
            digest = sha256_file(src_zip)
            release = {
                "tag_name": "v9.9.9",
                "assets": [
                    _asset(zip_name, "https://example.invalid/a.zip", digest=f"sha256:{digest}"),
                    _asset("SHA256SUMS", "https://example.invalid/SHA256SUMS"),
                ],
            }

            def fake_download(url, dest, timeout=180):
                if "SHA256SUMS" in url:
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(f"{digest}  {zip_name}\n")
                else:
                    shutil.copy2(src_zip, dest)

            logs = []
            with patch("whisperfast.updates.app_updates.BASE_DIR", tmp):
                with patch(
                    "whisperfast.updates.app_updates.fetch_latest_github_release",
                    return_value=release,
                ):
                    with patch(
                        "whisperfast.updates.app_updates._signing_key_configured",
                        return_value=False,
                    ):
                        with patch(
                            "whisperfast.updates.app_updates._http_download",
                            side_effect=fake_download,
                        ):
                            result = _download_verified_release_zip(logs.append)
            self.assertIsNotNone(result)
            self.assertTrue(os.path.isfile(os.path.join(result, "main.py")))
            self.assertIn(t("app_update_checksum_ok"), logs)

    def test_signing_key_requires_detached_signature(self):
        release = {
            "tag_name": "v9.9.9",
            "assets": [
                _asset("WhisperFastGUI-9.9.9-src.zip", "https://example.invalid/a.zip"),
                _asset("SHA256SUMS", "https://example.invalid/SHA256SUMS"),
            ],
        }
        logs = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("whisperfast.updates.app_updates.BASE_DIR", tmp):
                with patch(
                    "whisperfast.updates.app_updates.fetch_latest_github_release",
                    return_value=release,
                ):
                    with patch(
                        "whisperfast.updates.app_updates._signing_key_configured",
                        return_value=True,
                    ):
                        with patch(
                            "whisperfast.updates.app_updates._http_download",
                            side_effect=lambda *a, **k: None,
                        ):
                            result = _download_verified_release_zip(logs.append)
        self.assertIsNone(result)
        self.assertIn(t("app_update_signature_missing"), logs)


class TestGpgVerifyHelper(unittest.TestCase):
    def test_missing_gpg_fails_closed(self):
        with patch("whisperfast.updates.app_updates.shutil.which", return_value=None):
            ok, err = verify_detached_gpg_signature("data", "sig", "key")
        self.assertFalse(ok)
        self.assertIn("gpg", err.lower())


if __name__ == "__main__":
    unittest.main()

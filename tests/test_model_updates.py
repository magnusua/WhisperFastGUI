"""whisperfast.updates.model_updates: Whisper model revision check/update (no real Hub calls)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.updates import model_updates as mu


def _fake_cache_info(repo_id, commit_hash):
    ref = SimpleNamespace(commit_hash=commit_hash)
    repo = SimpleNamespace(repo_id=repo_id, refs={"main": ref})
    return SimpleNamespace(repos=[repo])


class TestGetModelRepoId(unittest.TestCase):
    def test_known_and_unknown_model(self):
        with patch.object(mu, "_MODELS", {"tiny": "Systran/faster-whisper-tiny"}):
            self.assertEqual(mu.get_model_repo_id("tiny"), "Systran/faster-whisper-tiny")
            self.assertIsNone(mu.get_model_repo_id("does-not-exist"))

    def test_no_models_table(self):
        with patch.object(mu, "_MODELS", {}):
            self.assertIsNone(mu.get_model_repo_id("tiny"))


class TestGetLocalModelRevision(unittest.TestCase):
    def test_no_scan_cache_dir_available(self):
        with patch.object(mu, "scan_cache_dir", None):
            self.assertIsNone(mu.get_local_model_revision("Systran/faster-whisper-tiny"))

    def test_finds_matching_repo(self):
        info = _fake_cache_info("Systran/faster-whisper-tiny", "abc123def456")
        with patch.object(mu, "scan_cache_dir", return_value=info):
            rev = mu.get_local_model_revision("Systran/faster-whisper-tiny", cache_root="/tmp/cache")
        self.assertEqual(rev, "abc123def456")

    def test_no_matching_repo_returns_none(self):
        info = _fake_cache_info("Systran/faster-whisper-tiny", "abc123def456")
        with patch.object(mu, "scan_cache_dir", return_value=info):
            self.assertIsNone(mu.get_local_model_revision("Systran/faster-whisper-base"))

    def test_scan_error_returns_none(self):
        with patch.object(mu, "scan_cache_dir", side_effect=OSError("boom")):
            self.assertIsNone(mu.get_local_model_revision("Systran/faster-whisper-tiny"))


class TestGetRemoteModelRevision(unittest.TestCase):
    def test_no_hf_api_available(self):
        with patch.object(mu, "HfApi", None):
            self.assertIsNone(mu.get_remote_model_revision("Systran/faster-whisper-tiny"))

    def test_returns_sha(self):
        fake_api = SimpleNamespace(model_info=lambda repo_id: SimpleNamespace(sha="deadbeef"))
        with patch.object(mu, "HfApi", return_value=fake_api):
            self.assertEqual(mu.get_remote_model_revision("Systran/faster-whisper-tiny"), "deadbeef")

    def test_hub_error_returns_none(self):
        def _raise(repo_id):
            raise RuntimeError("network down")

        fake_api = SimpleNamespace(model_info=_raise)
        with patch.object(mu, "HfApi", return_value=fake_api):
            self.assertIsNone(mu.get_remote_model_revision("Systran/faster-whisper-tiny"))


class TestModelNeedsUpdate(unittest.TestCase):
    def test_no_repo_id_never_needs_update(self):
        with patch.object(mu, "_MODELS", {}):
            self.assertFalse(mu.model_needs_update("tiny"))

    def test_not_downloaded_never_needs_update(self):
        with patch.object(mu, "_MODELS", {"tiny": "repo/tiny"}), \
                patch.object(mu, "get_local_model_revision", return_value=None):
            self.assertFalse(mu.model_needs_update("tiny"))

    def test_remote_unreachable_never_needs_update(self):
        with patch.object(mu, "_MODELS", {"tiny": "repo/tiny"}), \
                patch.object(mu, "get_local_model_revision", return_value="rev1"), \
                patch.object(mu, "get_remote_model_revision", return_value=None):
            self.assertFalse(mu.model_needs_update("tiny"))

    def test_matching_revisions_do_not_need_update(self):
        with patch.object(mu, "_MODELS", {"tiny": "repo/tiny"}), \
                patch.object(mu, "get_local_model_revision", return_value="rev1"), \
                patch.object(mu, "get_remote_model_revision", return_value="rev1"):
            self.assertFalse(mu.model_needs_update("tiny"))

    def test_mismatched_revisions_need_update(self):
        with patch.object(mu, "_MODELS", {"tiny": "repo/tiny"}), \
                patch.object(mu, "get_local_model_revision", return_value="rev1"), \
                patch.object(mu, "get_remote_model_revision", return_value="rev2"):
            self.assertTrue(mu.model_needs_update("tiny"))


class TestCheckWhisperModelUpdates(unittest.TestCase):
    def test_skips_unknown_names_and_ones_without_local_copy(self):
        logs = []
        with patch.object(mu, "WHISPER_MODELS", ["tiny", "base"]), \
                patch.object(mu, "get_model_repo_id", side_effect=lambda n: f"repo/{n}"), \
                patch.object(
                    mu, "get_local_model_revision",
                    side_effect=lambda repo_id, cache_root=None: "rev1" if "tiny" in repo_id else None,
                ), \
                patch.object(mu, "get_remote_model_revision", return_value="rev1"):
            updates = mu.check_whisper_model_updates(["tiny", "base", "not-a-model"], log_func=logs.append)
        self.assertEqual(updates, [])  # tiny matches revisions, base has no local copy
        self.assertTrue(any("tiny" in msg for msg in logs))

    def test_reports_mismatched_revision(self):
        with patch.object(mu, "WHISPER_MODELS", ["tiny"]), \
                patch.object(mu, "get_model_repo_id", return_value="repo/tiny"), \
                patch.object(mu, "get_local_model_revision", return_value="aaaaaaaa1111"), \
                patch.object(mu, "get_remote_model_revision", return_value="bbbbbbbb2222"):
            updates = mu.check_whisper_model_updates(["tiny"])
        self.assertEqual(len(updates), 1)
        name, local_short, remote_short = updates[0]
        self.assertEqual(name, "tiny")
        self.assertEqual(local_short, "aaaaaaaa")
        self.assertEqual(remote_short, "bbbbbbbb")

    def test_remote_check_failure_is_logged_and_skipped(self):
        logs = []
        with patch.object(mu, "WHISPER_MODELS", ["tiny"]), \
                patch.object(mu, "get_model_repo_id", return_value="repo/tiny"), \
                patch.object(mu, "get_local_model_revision", return_value="aaaaaaaa"), \
                patch.object(mu, "get_remote_model_revision", return_value=None):
            updates = mu.check_whisper_model_updates(["tiny"], log_func=logs.append)
        self.assertEqual(updates, [])
        self.assertTrue(logs)


class TestUpdateWhisperModel(unittest.TestCase):
    def test_no_hub_library_available(self):
        logs = []
        with patch.object(mu, "download_model", None), patch.object(mu, "snapshot_download", None):
            ok = mu.update_whisper_model("tiny", log_func=logs.append)
        self.assertFalse(ok)

    def test_unknown_model_name(self):
        logs = []
        with patch.object(mu, "download_model", lambda *a, **k: None), \
                patch.object(mu, "get_model_repo_id", return_value=None):
            ok = mu.update_whisper_model("not-a-model", log_func=logs.append)
        self.assertFalse(ok)

    def test_force_uses_snapshot_download_with_force_flag(self):
        calls = []
        fake_snapshot = lambda *a, **k: calls.append((a, k))
        with patch.object(mu, "get_model_repo_id", return_value="repo/tiny"), \
                patch.object(mu, "snapshot_download", fake_snapshot), \
                patch.object(mu, "download_model", lambda *a, **k: None):
            ok = mu.update_whisper_model("tiny", log_func=lambda *_: None, force=True)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        _, kwargs = calls[0]
        self.assertTrue(kwargs.get("force_download"))

    def test_non_force_prefers_download_model(self):
        calls = []
        with patch.object(mu, "get_model_repo_id", return_value="repo/tiny"), \
                patch.object(mu, "download_model", lambda *a, **k: calls.append((a, k))), \
                patch.object(mu, "snapshot_download", lambda *a, **k: self.fail("should not be called")):
            ok = mu.update_whisper_model("tiny", log_func=lambda *_: None, force=False)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)

    def test_download_error_returns_false_and_logs(self):
        logs = []

        def _raise(*_a, **_k):
            raise RuntimeError("disk full")

        with patch.object(mu, "get_model_repo_id", return_value="repo/tiny"), \
                patch.object(mu, "download_model", _raise):
            ok = mu.update_whisper_model("tiny", log_func=logs.append, force=False)
        self.assertFalse(ok)
        self.assertTrue(any("disk full" in msg for msg in logs))


class TestApplyWhisperModelUpdates(unittest.TestCase):
    def test_updates_each_model_in_order(self):
        calls = []
        with patch.object(mu, "update_whisper_model", side_effect=lambda name, log_func=print, force=False: calls.append(name) or True):
            mu.apply_whisper_model_updates(["tiny", "base"], log_func=lambda *_: None, force=True)
        self.assertEqual(calls, ["tiny", "base"])


if __name__ == "__main__":
    unittest.main()

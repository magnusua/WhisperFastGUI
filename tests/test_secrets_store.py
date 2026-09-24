"""API-key protection: DPAPI on Windows, Keychain references on macOS."""
import unittest
from unittest.mock import patch

from whisperfast import secrets_store
from whisperfast.secrets_store import (
    DPAPI_PREFIX,
    KEYCHAIN_PREFIX,
    protect_settings,
    protect_string,
    unprotect_settings,
    unprotect_string,
)


class SecretsStoreTestCase(unittest.TestCase):
    def setUp(self):
        secrets_store._unreadable_accounts.clear()

    def test_plaintext_roundtrip_when_protection_unavailable(self):
        with patch.object(secrets_store, "_crypt_protect", return_value=None), \
             patch.object(secrets_store.sys, "platform", "linux"):
            stored = protect_string("sk-test", label="cursor_api_key")
        self.assertEqual(stored, "sk-test")
        self.assertEqual(unprotect_string(stored), "sk-test")

    def test_already_protected_value_is_not_wrapped_again(self):
        blob = DPAPI_PREFIX + "AAAA"
        self.assertEqual(protect_string(blob, label="cursor_api_key"), blob)
        ref = KEYCHAIN_PREFIX + "cursor_api_key"
        self.assertEqual(protect_string(ref, label="cursor_api_key"), ref)

    def test_macos_stores_keychain_reference(self):
        saved = {}

        def _set(account, secret):
            saved[account] = secret
            return True

        def _get(account):
            return saved.get(account, "")

        with patch.object(secrets_store.sys, "platform", "darwin"), \
             patch.object(secrets_store, "_keychain_set", side_effect=_set), \
             patch.object(secrets_store, "_keychain_get", side_effect=_get), \
             patch.object(secrets_store, "_keychain_delete"):
            stored = protect_settings({"cursor_api_key": "sk-live", "language": "UK"})
            self.assertEqual(stored["cursor_api_key"], KEYCHAIN_PREFIX + "cursor_api_key")
            self.assertEqual(stored["language"], "UK")
            self.assertEqual(saved["cursor_api_key"], "sk-live")
            loaded = unprotect_settings(stored)
            self.assertEqual(loaded["cursor_api_key"], "sk-live")

    def test_macos_keeps_reference_when_keychain_read_fails(self):
        with patch.object(secrets_store.sys, "platform", "darwin"), \
             patch.object(secrets_store, "_keychain_get", return_value=None), \
             patch.object(secrets_store, "_keychain_delete") as delete:
            loaded = unprotect_string(KEYCHAIN_PREFIX + "cursor_api_key")
            self.assertEqual(loaded, "")
            saved = protect_settings({"cursor_api_key": ""})
            self.assertEqual(saved["cursor_api_key"], KEYCHAIN_PREFIX + "cursor_api_key")
            delete.assert_not_called()

    def test_macos_clears_keychain_item_when_secret_removed(self):
        with patch.object(secrets_store.sys, "platform", "darwin"), \
             patch.object(secrets_store, "_keychain_delete") as delete:
            saved = protect_settings({"gemini_api_key": ""})
        self.assertEqual(saved["gemini_api_key"], "")
        delete.assert_called_once_with("gemini_api_key")

    def test_windows_dpapi_prefix(self):
        with patch.object(secrets_store.sys, "platform", "win32"), \
             patch.object(secrets_store, "_crypt_protect", return_value="YmxvYg=="), \
             patch.object(secrets_store, "_crypt_unprotect", return_value="sk-test"):
            stored = protect_string("sk-test", label="cursor_api_key")
            self.assertEqual(stored, DPAPI_PREFIX + "YmxvYg==")
            self.assertEqual(unprotect_string(stored), "sk-test")


if __name__ == "__main__":
    unittest.main()

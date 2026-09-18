"""Provider registry is a single source of truth."""
import os
import unittest
from unittest.mock import patch

from whisperfast.core.google_oauth import GEMINI_SCOPE, google_auth_url
from whisperfast.i18n import t
from whisperfast.postprocess.providers import (
    PROVIDER_CURSOR,
    PROVIDER_ORDER,
    PROVIDERS,
    get_provider,
    normalize_provider_id,
    provider_choices,
)
from whisperfast.postprocess.providers.gemini import (
    GeminiProvider,
    call_gemini_generate,
    clear_gemini_session,
    store_gemini_session,
)


class TestProviderRegistry(unittest.TestCase):
    def test_order_and_choices_follow_providers(self):
        self.assertEqual(PROVIDER_ORDER, list(PROVIDERS))
        choices = provider_choices()
        self.assertEqual([pid for pid, _key in choices], PROVIDER_ORDER)
        for pid, label_key in choices:
            self.assertEqual(label_key, PROVIDERS[pid].label_key)

    def test_get_provider_dispatches_by_id(self):
        for pid in PROVIDER_ORDER:
            self.assertIs(get_provider(pid), PROVIDERS[pid])
            self.assertIs(get_provider(pid.upper()), PROVIDERS[pid])  # case-insensitive
            self.assertIs(get_provider(f"  {pid}  "), PROVIDERS[pid])  # trims whitespace

    def test_get_provider_falls_back_to_cursor_for_unknown_id(self):
        self.assertIs(get_provider("not-a-real-provider"), PROVIDERS[PROVIDER_CURSOR])
        self.assertIs(get_provider(""), PROVIDERS[PROVIDER_CURSOR])
        self.assertIs(get_provider(None), PROVIDERS[PROVIDER_CURSOR])

    def test_normalize_provider_id_matches_get_provider_choice(self):
        for pid in PROVIDER_ORDER:
            self.assertEqual(normalize_provider_id(pid.upper()), pid)
        self.assertEqual(normalize_provider_id("bogus"), PROVIDER_CURSOR)

    def test_every_provider_label_resolves_to_a_real_translation(self):
        # t() returns the key itself when a translation is missing, so this
        # catches a provider registered with a label_key that was never added
        # to lang.json (the registry-consistency check above wouldn't).
        for pid, label_key in provider_choices():
            self.assertNotEqual(t(label_key), label_key, f"missing translation for {pid}'s {label_key}")

    def test_every_provider_exposes_a_callable_process(self):
        for pid in PROVIDER_ORDER:
            provider = PROVIDERS[pid]
            self.assertTrue(callable(getattr(provider, "process", None)), f"{pid} has no process()")


class TestGeminiOAuth(unittest.TestCase):
    def test_auth_url_gemini_scopes(self):
        url = google_auth_url(
            "cid.apps.googleusercontent.com",
            "http://127.0.0.1:9/",
            code_challenge="abc_challenge",
            scope=GEMINI_SCOPE,
        )
        self.assertIn("code_challenge=abc_challenge", url)
        self.assertIn("generative-language.retriever", url)
        self.assertIn("cloud-platform", url)
        self.assertNotIn("calendar.readonly", url)

    def test_has_credentials_key_or_refresh(self):
        provider = GeminiProvider()
        old = os.environ.pop("FTW_GOOGLE_OAUTH_CLIENT_ID", None)
        try:
            self.assertFalse(provider.has_api_credentials({}))
            self.assertTrue(provider.has_api_credentials({"gemini_api_key": "k"}))
            self.assertFalse(
                provider.has_api_credentials({"gemini_oauth_refresh_token": "refresh"})
            )
            self.assertTrue(
                provider.has_api_credentials(
                    {
                        "gemini_oauth_refresh_token": "refresh",
                        "google_oauth_client_id": "cid.apps.googleusercontent.com",
                    }
                )
            )
        finally:
            if old is not None:
                os.environ["FTW_GOOGLE_OAUTH_CLIENT_ID"] = old

    def test_generate_bearer_header(self):
        captured = {}

        def fake_http(url, payload, headers=None, timeout_s=180.0):
            captured["url"] = url
            captured["headers"] = headers or {}
            return {"candidates": [{"content": {"parts": [{"text": "pong"}]}}]}

        with patch("whisperfast.postprocess.providers.gemini.http_json_request", fake_http):
            text = call_gemini_generate(
                "",
                "gemini-2.0-flash",
                "hi",
                access_token="tok",
                project_id="my-proj",
            )
        self.assertEqual(text, "pong")
        self.assertNotIn("key=", captured["url"])
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer tok")
        self.assertEqual(captured["headers"].get("x-goog-user-project"), "my-proj")

    def test_generate_api_key_query(self):
        captured = {}

        def fake_http(url, payload, headers=None, timeout_s=180.0):
            captured["url"] = url
            captured["headers"] = headers
            return {"candidates": [{"content": {"parts": [{"text": "pong"}]}}]}

        with patch("whisperfast.postprocess.providers.gemini.http_json_request", fake_http):
            call_gemini_generate("secret-key", "gemini-2.0-flash", "hi")
        self.assertIn("key=secret-key", captured["url"])
        self.assertFalse(captured["headers"])

    def test_store_and_clear_gemini_session(self):
        settings = {}
        store_gemini_session(settings, "refresh-token", "user@example.com")
        self.assertTrue(settings.get("gemini_oauth_refresh_token"))
        self.assertEqual(settings.get("gemini_oauth_email"), "user@example.com")
        clear_gemini_session(settings)
        self.assertEqual(settings.get("gemini_oauth_refresh_token"), "")
        self.assertEqual(settings.get("gemini_oauth_email"), "")


if __name__ == "__main__":
    unittest.main()

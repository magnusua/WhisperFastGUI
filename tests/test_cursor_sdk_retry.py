"""Retry Cursor SDK calls on transient Network request failed."""
from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from whisperfast.postprocess.cursor_postprocess import (
    CURSOR_SDK_BRIDGE_RETRY_DELAY_S,
    CURSOR_SDK_NETWORK_ATTEMPTS,
    CURSOR_SDK_NETWORK_RETRY_DELAY_S,
    _is_network_request_failed,
    _run_sdk_one,
    _sdk_retry_plan,
)


class ConnectError(Exception):
    """Mirrors Cursor ConnectError type name in retry matching."""


class TestNetworkRequestFailedClassifier(unittest.TestCase):
    def test_matches_gui_message(self):
        self.assertTrue(_is_network_request_failed(RuntimeError("internal: Network request failed")))

    def test_matches_connect_error_type(self):
        self.assertTrue(_is_network_request_failed(ConnectError("Network request failed")))

    def test_ignores_other_errors(self):
        self.assertFalse(_is_network_request_failed(RuntimeError("WinError 10061")))
        self.assertFalse(_is_network_request_failed(RuntimeError("permission denied")))


class TestSdkRetryPlan(unittest.TestCase):
    def test_network_retries_until_fifth_attempt(self):
        err = RuntimeError("internal: Network request failed")
        self.assertEqual(
            _sdk_retry_plan(err, 1),
            ("network", CURSOR_SDK_NETWORK_RETRY_DELAY_S),
        )
        self.assertEqual(
            _sdk_retry_plan(err, 4),
            ("network", CURSOR_SDK_NETWORK_RETRY_DELAY_S),
        )
        self.assertIsNone(_sdk_retry_plan(err, 5))
        self.assertIsNone(_sdk_retry_plan(err, CURSOR_SDK_NETWORK_ATTEMPTS))

    def test_connecterror_network_uses_network_plan_not_bridge(self):
        err = ConnectError("internal: Network request failed")
        self.assertEqual(
            _sdk_retry_plan(err, 1),
            ("network", CURSOR_SDK_NETWORK_RETRY_DELAY_S),
        )

    def test_bridge_retries_until_fifth_attempt(self):
        err = OSError("WinError 10061: connection refused")
        self.assertEqual(_sdk_retry_plan(err, 1), ("bridge", CURSOR_SDK_BRIDGE_RETRY_DELAY_S))
        self.assertEqual(_sdk_retry_plan(err, 4), ("bridge", CURSOR_SDK_BRIDGE_RETRY_DELAY_S))
        self.assertIsNone(_sdk_retry_plan(err, 5))

    def test_other_errors_retry_until_auth(self):
        self.assertEqual(
            _sdk_retry_plan(RuntimeError("agent crashed"), 1),
            ("network", CURSOR_SDK_NETWORK_RETRY_DELAY_S),
        )
        self.assertIsNone(_sdk_retry_plan(RuntimeError("invalid api key"), 1))
        self.assertIsNone(_sdk_retry_plan(RuntimeError("401 Unauthorized"), 2))


class _FakeClient:
    launched = 0

    def __init__(self):
        type(self).launched += 1

    def close(self):
        pass

    @classmethod
    def launch_bridge(cls, **_kwargs):
        return cls()


class _FakeAgent:
    side_effects = []

    @staticmethod
    def prompt(*_args, **_kwargs):
        item = _FakeAgent.side_effects.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _install_fake_cursor_sdk():
    sdk = ModuleType("cursor_sdk")
    sdk.Agent = _FakeAgent
    sdk.AgentOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    sdk.Client = _FakeClient
    sdk.LocalAgentOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    client_mod = ModuleType("cursor_sdk._client")
    client_mod.close_default_client = lambda: None
    sys.modules["cursor_sdk"] = sdk
    sys.modules["cursor_sdk._client"] = client_mod
    return sdk, client_mod


class TestRunSdkOneNetworkRetry(unittest.TestCase):
    def setUp(self):
        _FakeClient.launched = 0
        _FakeAgent.side_effects = []
        self._prev_sdk = sys.modules.get("cursor_sdk")
        self._prev_client = sys.modules.get("cursor_sdk._client")
        _install_fake_cursor_sdk()

    def tearDown(self):
        if self._prev_sdk is None:
            sys.modules.pop("cursor_sdk", None)
        else:
            sys.modules["cursor_sdk"] = self._prev_sdk
        if self._prev_client is None:
            sys.modules.pop("cursor_sdk._client", None)
        else:
            sys.modules["cursor_sdk._client"] = self._prev_client

    def test_succeeds_on_third_network_attempt(self):
        logs = []
        _FakeAgent.side_effects = [
            RuntimeError("internal: Network request failed"),
            RuntimeError("internal: Network request failed"),
            SimpleNamespace(status="finished", id="ok"),
        ]
        with patch("whisperfast.postprocess.cursor_postprocess._prepare_cursor_sdk"):
            with patch("whisperfast.postprocess.cursor_postprocess.time.sleep") as sleep:
                _run_sdk_one(
                    "in.txt",
                    "out.md",
                    "clean up",
                    "key",
                    log_func=logs.append,
                    prompt_num=1,
                )
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(
            [c.args[0] for c in sleep.call_args_list],
            [CURSOR_SDK_NETWORK_RETRY_DELAY_S, CURSOR_SDK_NETWORK_RETRY_DELAY_S],
        )
        self.assertEqual(len(logs), 2)
        self.assertIn("2/5", logs[0])
        self.assertIn("3/5", logs[1])
        self.assertIn("Network request failed", logs[0])
        self.assertEqual(_FakeClient.launched, 3)

    def test_raises_after_five_network_failures(self):
        _FakeAgent.side_effects = [RuntimeError("internal: Network request failed") for _ in range(5)]
        with patch("whisperfast.postprocess.cursor_postprocess._prepare_cursor_sdk"):
            with patch("whisperfast.postprocess.cursor_postprocess.time.sleep") as sleep:
                with self.assertRaises(RuntimeError) as ctx:
                    _run_sdk_one("in.txt", "out.md", "clean up", "key", prompt_num=1)
        self.assertIn("Network request failed", str(ctx.exception))
        self.assertEqual(sleep.call_count, 4)

    def test_does_not_retry_unrelated_error(self):
        _FakeAgent.side_effects = [RuntimeError("invalid api key")]
        with patch("whisperfast.postprocess.cursor_postprocess._prepare_cursor_sdk"):
            with patch("whisperfast.postprocess.cursor_postprocess.time.sleep") as sleep:
                with self.assertRaises(RuntimeError) as ctx:
                    _run_sdk_one("in.txt", "out.md", "clean up", "key")
        self.assertEqual(str(ctx.exception), "invalid api key")
        sleep.assert_not_called()
        self.assertEqual(_FakeClient.launched, 1)


if __name__ == "__main__":
    unittest.main()

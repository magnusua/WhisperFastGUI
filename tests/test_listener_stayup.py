"""The Telegram listener reconnects after a long outage and holds Windows awake."""
import ctypes
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.i18n import t
from whisperfast.platform_util import (
    ENDSESSION_CLOSEAPP,
    ENDSESSION_CRITICAL,
    ENDSESSION_LOGOFF,
    shutdown_should_proceed,
)
from whisperfast.telegram.reconnect import (
    is_network_error,
    next_reconnect_wait,
    sleep_until_stop,
)


class TestReconnectWait(unittest.TestCase):
    def test_pause_grows_then_caps_at_half_a_minute(self):
        self.assertEqual(next_reconnect_wait(0, was_online=False), 5)
        self.assertEqual(next_reconnect_wait(5, was_online=False), 10)
        self.assertEqual(next_reconnect_wait(10, was_online=False), 20)
        self.assertEqual(next_reconnect_wait(20, was_online=False), 30)
        self.assertEqual(next_reconnect_wait(30, was_online=False), 30)

    def test_a_live_session_resets_the_pause(self):
        self.assertEqual(next_reconnect_wait(30, was_online=True), 0)

    def test_stop_ends_the_wait_without_sleeping_the_whole_interval(self):
        self.assertFalse(sleep_until_stop(lambda: True, 3600))
        self.assertTrue(sleep_until_stop(lambda: False, 0))

    def test_network_errors_cover_telethon_and_bot_api(self):
        self.assertTrue(is_network_error(ConnectionError("Connection to Telegram failed 5 time(s)")))
        self.assertTrue(is_network_error(TimeoutError("timed out")))

        from whisperfast.telegram.client import TelegramApiError

        self.assertTrue(is_network_error(TelegramApiError("connection failed")))
        self.assertFalse(is_network_error(TelegramApiError("unauthorized")))


class TestAccountReconnect(unittest.TestCase):
    def test_connection_loss_retries_until_telegram_is_back(self):
        from whisperfast.telegram.account import run_account

        state = {"connects": 0, "stop": False}
        lines = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.args = args

            def is_connected(self):
                return False

            async def connect(self):
                state["connects"] += 1
                if state["connects"] < 3:
                    raise ConnectionError("Connection to Telegram failed 5 time(s)")

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_me(self):
                return SimpleNamespace(id=7, first_name="Mykola", username="m")

            def on(self, _event):
                return lambda fn: fn

            async def send_message(self, *_args, **_kwargs):
                return None

            async def run_until_disconnected(self):
                state["stop"] = True

        async def no_wait(_stop, _seconds, step=0.4):
            return True

        settings = {
            "telegram_api_id": "1",
            "telegram_api_hash": "hash",
            "telegram_phone": "+10000000000",
            "telegram_self_chat_names": "",
        }
        with patch("telethon.TelegramClient", FakeClient):
            with patch(
                "whisperfast.telegram.reconnect.async_sleep_until_stop",
                no_wait,
            ):
                code = run_account(
                    settings,
                    log=lines.append,
                    stop=lambda: state["stop"],
                    submit=lambda *_a, **_k: None,
                    gui_running=lambda: True,
                )
        self.assertEqual(code, 0)
        self.assertEqual(state["connects"], 3)
        self.assertIn(t("telegram_listener_reconnecting"), lines)
        self.assertIn("Connection to Telegram failed 5 time(s)", lines)
        self.assertEqual(lines.count(t("telegram_listener_reconnecting")), 1)

    def test_missing_sign_in_does_not_retry(self):
        from whisperfast.telegram.account import run_account

        connects = {"n": 0}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def is_connected(self):
                return False

            async def connect(self):
                connects["n"] += 1

            async def disconnect(self):
                return None

            async def is_user_authorized(self):
                return False

        settings = {
            "telegram_api_id": "1",
            "telegram_api_hash": "hash",
            "telegram_phone": "+10000000000",
        }
        with patch("telethon.TelegramClient", FakeClient):
            code = run_account(settings, log=lambda *_a, **_k: None, stop=lambda: False)
        self.assertEqual(code, 1)
        self.assertEqual(connects["n"], 1)


class TestBotReconnect(unittest.TestCase):
    def test_bot_poll_retries_a_dropped_connection(self):
        from whisperfast.telegram.client import TelegramApiError
        from whisperfast.telegram.worker import run_bot

        state = {"n": 0, "stop": False}
        lines = []

        class Client:
            def get_updates(self, **_kwargs):
                state["n"] += 1
                if state["n"] == 1:
                    raise TelegramApiError("connection failed")
                state["stop"] = True
                return []

        with patch(
            "whisperfast.telegram.worker.load_app_settings",
            return_value={"telegram_allowed_chat_ids": []},
        ):
            with patch(
                "whisperfast.telegram.learn.load_learn_chats",
                return_value={"always": []},
            ):
                with patch(
                    "whisperfast.telegram.learn.take_ready",
                    return_value=[],
                ):
                    with patch(
                        "whisperfast.telegram.reconnect.sleep_until_stop",
                        return_value=True,
                    ):
                        code = run_bot(
                            {"telegram_bot_token": "123:abc"},
                            client=Client(),
                            ensure_api=lambda *_a, **_k: None,
                            log=lines.append,
                            stop=lambda: state["stop"],
                            max_cycles=5,
                        )
        self.assertEqual(code, 0)
        self.assertGreaterEqual(state["n"], 2)
        self.assertIn(t("telegram_listener_reconnecting"), lines)


class TestShutdownPolicy(unittest.TestCase):
    def test_manual_shutdown_is_vetoed_when_no_update_is_waiting(self):
        self.assertFalse(shutdown_should_proceed(0, update_pending=False))

    def test_update_servicing_forced_and_logoff_proceed(self):
        self.assertTrue(shutdown_should_proceed(ENDSESSION_CLOSEAPP, update_pending=False))
        self.assertTrue(shutdown_should_proceed(ENDSESSION_CRITICAL, update_pending=False))
        self.assertTrue(shutdown_should_proceed(ENDSESSION_LOGOFF, update_pending=False))
        self.assertTrue(
            shutdown_should_proceed(ENDSESSION_CLOSEAPP | ENDSESSION_LOGOFF, update_pending=False)
        )

    def test_automatic_update_reboot_proceeds_even_without_a_special_flag(self):
        self.assertTrue(shutdown_should_proceed(0, update_pending=True))

    def test_reboot_required_key_counts_as_an_update_reboot(self):
        from whisperfast.platform_util import windows_update_reboot_pending

        class Opened:
            def Close(self):
                return None

        def open_key(_root, path):
            if path.endswith("RebootRequired"):
                return Opened()
            raise FileNotFoundError(path)

        with patch("winreg.OpenKey", open_key):
            self.assertTrue(windows_update_reboot_pending())

    def test_absent_reboot_keys_are_not_an_update(self):
        from whisperfast.platform_util import windows_update_reboot_pending

        def open_key(_root, _path):
            raise FileNotFoundError()

        with patch("winreg.OpenKey", open_key):
            self.assertFalse(windows_update_reboot_pending())


def _shutdown_block_registered(hwnd) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShutdownBlockReasonQuery.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.ShutdownBlockReasonQuery.restype = wintypes.BOOL
    size = wintypes.DWORD(256)
    buf = ctypes.create_unicode_buffer(256)
    return bool(user32.ShutdownBlockReasonQuery(hwnd, buf, ctypes.byref(size)))


class TestPowerGuard(unittest.TestCase):
    def test_guard_starts_and_stops_on_windows(self):
        import sys

        from whisperfast.platform_util import ListenerPowerGuard

        if sys.platform != "win32":
            self.skipTest("windows only")
        guard = ListenerPowerGuard(t("telegram_listener_shutdown_block"))
        try:
            self.assertTrue(guard.start(), guard._error)
            self.assertTrue(guard.active())
            self.assertTrue(guard._request)
            self.assertTrue(guard._hwnd)
            self.assertFalse(_shutdown_block_registered(guard._hwnd))
        finally:
            guard.stop()
        self.assertFalse(guard.active())

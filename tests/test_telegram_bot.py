"""Telegram bot: media filter, allowlist, one-at-a-time queue. No network, no Whisper weights."""
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whisperfast.cli import maybe_run_cli
from whisperfast.secrets_store import SECRET_SETTING_KEYS
from whisperfast.settings import load_app_settings, normalize_chat_ids
from whisperfast.telegram.client import TelegramApiError, TelegramClient, unwrap_result
from whisperfast.core.ipc_cmd import append_command, take_queued_commands
from whisperfast.core.queue_manager import QueueController
from whisperfast.telegram.gui_bridge import apply_telegram_command, maybe_deliver_telegram
from whisperfast.telegram.pipeline import PipelineError, run_pipeline
from whisperfast.telegram.worker import (
    IncomingMedia,
    accept_updates,
    decide_update,
    ensure_local_api,
    media_from_message,
    process_pending,
)


def _message(chat_id, message_id=1, **fields):
    return {"message_id": message_id, "chat": {"id": chat_id}, **fields}


def _update(message, update_id=1):
    return {"update_id": update_id, "message": message}


class TestMediaFilter(unittest.TestCase):
    def test_voice_video_and_media_document(self):
        voice = media_from_message(_message(5, voice={"file_id": "v1"}))
        self.assertEqual(voice.kind, "voice")
        self.assertEqual(voice.file_id, "v1")
        self.assertTrue(voice.filename.endswith(".ogg"))

        video = media_from_message(
            _message(5, message_id=2, video={"file_id": "vid", "file_name": "clip.mp4"})
        )
        self.assertEqual(video.kind, "video")
        self.assertEqual(video.filename, "clip.mp4")

        doc = media_from_message(
            _message(
                5,
                message_id=3,
                document={"file_id": "d1", "file_name": "talk.mkv", "mime_type": "video/x-matroska"},
            )
        )
        self.assertEqual(doc.kind, "document")
        self.assertEqual(doc.filename, "talk.mkv")

        audio_mime = media_from_message(
            _message(5, document={"file_id": "d2", "file_name": "note", "mime_type": "audio/ogg"})
        )
        self.assertIsNotNone(audio_mime)
        self.assertTrue(audio_mime.filename.endswith(".ogg"))

    def test_pdf_and_text_are_not_media(self):
        pdf = media_from_message(
            _message(5, document={"file_id": "p", "file_name": "notes.pdf", "mime_type": "application/pdf"})
        )
        self.assertIsNone(pdf)
        self.assertIsNone(media_from_message(_message(5, text="hello")))
        self.assertIsNone(media_from_message(_message(5, photo=[{"file_id": "ph"}])))


class TestAllowlist(unittest.TestCase):
    def test_empty_allowlist_answers_start_and_ignores_media(self):
        start = decide_update(_update(_message(42, text="/start")), [])
        self.assertIsNone(start.job)
        self.assertEqual(start.reply_chat_id, 42)
        self.assertIn("42", start.reply_text)

        mentioned = decide_update(_update(_message(7, text="/start@MyBot")), [])
        self.assertEqual(mentioned.reply_chat_id, 7)

        media = decide_update(_update(_message(42, voice={"file_id": "v"})), [])
        self.assertIsNone(media.job)
        self.assertIsNone(media.reply_text)
        self.assertIn("42", media.log)

    def test_foreign_chat_is_logged_and_not_queued(self):
        decision = decide_update(
            _update(_message(99, video={"file_id": "v", "file_name": "a.mp4"})),
            [42],
        )
        self.assertIsNone(decision.job)
        self.assertIsNone(decision.reply_text)
        self.assertIn("99", decision.log)

        plain = decide_update(_update(_message(99, text="hi")), [42])
        self.assertIn("99", plain.log)

    def test_allowed_chat_is_enqueued(self):
        decision = decide_update(
            _update(_message(-100, message_id=8, audio={"file_id": "a", "file_name": "call.mp3"})),
            [-100],
        )
        self.assertEqual(decision.job.chat_id, -100)
        self.assertEqual(decision.job.file_id, "a")
        self.assertIsNone(decision.log)

    def test_accept_updates_replies_in_the_same_chat(self):
        sent = []

        class Client:
            def send_message(self, chat_id, text, reply_to=None):
                sent.append((chat_id, text, reply_to))

        pending = []
        logs = []
        offset = accept_updates(
            [
                _update(_message(42, message_id=3, voice={"file_id": "v"}), update_id=10),
                _update(_message(7, text="nope"), update_id=11),
            ],
            [42],
            Client(),
            pending,
            log=logs.append,
        )
        self.assertEqual(offset, 12)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].chat_id, 42)
        self.assertEqual(sent[0][0], 42)
        self.assertEqual(sent[0][2], 3)
        self.assertTrue(any("7" in line for line in logs))

    def test_normalize_chat_ids_and_settings_round_trip(self):
        self.assertEqual(normalize_chat_ids(["-100", 5, 5, True, "x"]), [-100, 5])
        from whisperfast.settings import parse_chat_id_text

        self.assertEqual(parse_chat_id_text("15, -100\n15  8"), [15, -100, 8])
        self.assertIn("telegram_bot_token", SECRET_SETTING_KEYS)
        self.assertIn("telegram_api_hash", SECRET_SETTING_KEYS)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"telegram_allowed_chat_ids": ["15", 15], "telegram_api_id": 12345}, handle)
            with patch("whisperfast.settings.settings_path", return_value=path):
                data = load_app_settings()
            self.assertEqual(data["telegram_allowed_chat_ids"], [15])
            self.assertEqual(data["telegram_api_id"], "12345")
            self.assertEqual(data["telegram_api_base"], "http://127.0.0.1:8081")
            self.assertEqual(data["telegram_mode"], "bot")
            with patch("whisperfast.settings.settings_path", return_value=path):
                from whisperfast.settings import save_app_settings

                save_app_settings({"telegram_mode": "nope", "telegram_phone": "+100"})
                again = load_app_settings()
            self.assertEqual(again["telegram_mode"], "bot")
            self.assertEqual(again["telegram_phone"], "+100")


class TestQueue(unittest.TestCase):
    def test_jobs_are_handed_to_the_gui_one_at_a_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            src_a = os.path.join(tmp, "a.ogg")
            src_b = os.path.join(tmp, "b.mp4")
            open(src_a, "wb").close()
            open(src_b, "wb").close()
            active = {"n": 0, "max": 0}
            submitted = []
            messages = []

            class Client:
                def get_file_path(self, file_id):
                    return src_a if file_id == "a" else src_b

                def send_message(self, chat_id, text, reply_to=None):
                    messages.append((chat_id, text, reply_to))

            def submit(path, chat_id, message_id):
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
                submitted.append((os.path.basename(path), chat_id, message_id))
                active["n"] -= 1

            jobs = [
                IncomingMedia(1, 10, "a", "a.ogg", "voice"),
                IncomingMedia(2, 11, "b", "b.mp4", "video"),
            ]
            process_pending(
                jobs,
                client=Client(),
                settings={},
                submit=submit,
                gui_running=lambda: True,
                work_dir=tmp,
            )
            self.assertEqual(active["max"], 1)
            self.assertEqual(submitted, [("a.ogg", 1, 10), ("b.mp4", 2, 11)])
            self.assertEqual([item[0] for item in messages], [1, 2])
            self.assertEqual(messages[0][2], 10)

    def test_missing_gui_is_reported_in_the_same_chat(self):
        messages = []

        class Client:
            def get_file_path(self, file_id):
                raise AssertionError("should not download when the GUI is down")

            def send_message(self, chat_id, text, reply_to=None):
                messages.append((chat_id, text, reply_to))

        process_pending(
            [IncomingMedia(9, 4, "a", "a.ogg", "voice")],
            client=Client(),
            settings={},
            submit=lambda *args: None,
            gui_running=lambda: False,
        )
        self.assertEqual(messages[0][0], 9)
        self.assertEqual(messages[0][2], 4)
        self.assertTrue(messages[0][1])


class TestGuiHandoff(unittest.TestCase):
    def test_command_queue_keeps_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("whisperfast.core.ipc_cmd.cmd_queue_dir", return_value=tmp):
                append_command("telegram_file", path="a.ogg", chat_id=1, message_id=2)
                append_command("telegram_file", path="b.mp4", chat_id=3, message_id=4)
                queued = take_queued_commands()
                self.assertEqual(take_queued_commands(), [])
        self.assertEqual([item["path"] for item in queued], ["a.ogg", "b.mp4"])
        self.assertEqual(queued[1]["chat_id"], 3)

    def test_apply_adds_file_and_starts_the_gui_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "clip.mp3")
            open(media, "wb").close()
            started = []

            class Ctrl:
                def __init__(self):
                    self.queue = []
                    self.watch_pending_continue = False
                    self._is_processing = lambda: False
                    self._start_processing = lambda **kwargs: started.append(kwargs)

                def add_files(self, paths):
                    for path in paths:
                        self.queue.append({"path": os.path.abspath(path), "processed": False})
                    return len(paths), 0

                def save_to_file(self):
                    return None

            app = SimpleNamespace(queue_ctrl=Ctrl())
            apply_telegram_command(
                app,
                {"action": "telegram_file", "path": media, "chat_id": 15, "message_id": 8},
            )
            item = app.queue_ctrl.queue[0]
            self.assertEqual(item["telegram_chat_id"], 15)
            self.assertEqual(item["telegram_message_id"], 8)
            self.assertEqual(started, [{"mode": "only_new", "from_watch": True}])

    def test_queue_file_keeps_telegram_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "clip.mp3")
            queue_path = os.path.join(tmp, "request_queue.json")
            open(media, "wb").close()
            ctrl = QueueController(request_queue_file=queue_path)
            ctrl.queue.append(
                {
                    "path": media,
                    "note": "",
                    "start": "00:00:00",
                    "end_segment_1": "",
                    "end_segment_2": "",
                    "end": "00:00:01",
                    "processed": False,
                    "telegram_chat_id": 5,
                    "telegram_message_id": 9,
                }
            )
            ctrl.save_to_file()
            restored = QueueController(request_queue_file=queue_path)
            restored.load_from_file()
        self.assertEqual(restored.queue[0]["telegram_chat_id"], 5)
        self.assertEqual(restored.queue[0]["telegram_message_id"], 9)

    def test_deliver_waits_for_ai_then_sends_to_the_same_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "clip.mp3")
            txt = os.path.join(tmp, "clip.txt")
            md = os.path.join(tmp, "clip_note.md")
            open(media, "wb").close()
            with open(txt, "w", encoding="utf-8") as handle:
                handle.write("hello")
            with open(md, "w", encoding="utf-8") as handle:
                handle.write("edited")

            class Store:
                def get_file(self, file_id):
                    return {
                        "id": file_id,
                        "status": "done",
                        "source": media,
                        "outputs": [
                            {"role": "txt", "path": txt},
                            {"role": "srt", "path": os.path.join(tmp, "clip.srt")},
                            {"role": "ai", "path": md},
                        ],
                    }

            app = SimpleNamespace(
                queue_ctrl=SimpleNamespace(
                    queue=[
                        {
                            "path": media,
                            "telegram_chat_id": 42,
                            "telegram_message_id": 3,
                        }
                    ]
                ),
                log_panel=SimpleNamespace(_store=Store()),
                ai_jobs=SimpleNamespace(
                    _jobs={"j": {"status": "running", "txt_path": txt, "log_file_id": "f1"}}
                ),
            )
            sent = []
            maybe_deliver_telegram(app, file_id="f1", sender=lambda meta, files, error: sent.append(1))
            self.assertEqual(sent, [])
            app.ai_jobs._jobs["j"]["status"] = "done"
            maybe_deliver_telegram(
                app,
                file_id="f1",
                sender=lambda meta, files, error: sent.append((meta, [os.path.basename(f["path"]) for f in files], error)),
            )
            self.assertEqual(sent[0][0]["chat_id"], 42)
            self.assertEqual(sent[0][0]["message_id"], 3)
            self.assertEqual(sent[0][1], ["clip.txt", "clip_note.md"])
            self.assertNotIn("clip.srt", sent[0][1])

    def test_video_audio_is_sent_and_a_later_clip_returns_to_the_chat(self):
        from whisperfast.telegram.gui_bridge import select_telegram_files
        from whisperfast.telegram.origin import lookup, remember

        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "clip.mp4")
            audio = os.path.join(tmp, "clip.mp3")
            txt = os.path.join(tmp, "clip.txt")
            full_mp3 = os.path.join(tmp, "clip_audio.mp3")
            clip_mp3 = os.path.join(tmp, "clip_00-01-00_00-02-00_audio.mp3")
            ai = os.path.join(tmp, "clip_note.md")
            for path in (video, audio, txt, full_mp3, clip_mp3, ai):
                open(path, "wb").close()
            outputs = [
                {"role": "txt", "path": txt},
                {"role": "mp3", "path": full_mp3},
                {"role": "mp3", "path": clip_mp3},
                {"role": "ai", "path": ai},
                {"role": "srt", "path": os.path.join(tmp, "clip.srt")},
            ]
            video_files = select_telegram_files(video, outputs)
            self.assertEqual(
                [os.path.basename(item["path"]) for item in video_files],
                ["clip.txt", "clip_audio.mp3", "clip_00-01-00_00-02-00_audio.mp3", "clip_note.md"],
            )
            from whisperfast.i18n import t

            self.assertEqual(video_files[2]["caption"], t("telegram_caption_clip"))
            audio_names = [os.path.basename(item["path"]) for item in select_telegram_files(audio, outputs)]
            self.assertIn("clip.txt", audio_names)
            self.assertNotIn("clip_audio.mp3", audio_names)
            self.assertIn("clip_00-01-00_00-02-00_audio.mp3", audio_names)

            origin = os.path.join(tmp, "origin.json")
            with patch("whisperfast.telegram.origin._FILE", origin):
                remember(video, 7, 8)
                self.assertEqual(lookup(video), {"chat_id": 7, "message_id": 8})


class TestPipeline(unittest.TestCase):
    def test_transcribe_then_ai_without_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "clip.mp3")
            open(media, "wb").close()
            seen = {}

            class Model:
                def transcribe(self, path, **kwargs):
                    seen["kwargs"] = kwargs
                    seg = SimpleNamespace(start=0.0, end=1.2, text=" hello ", speaker="")
                    return iter([seg]), None

            def ai_runner(txt_path, **kwargs):
                seen["delay"] = kwargs.get("delay_s")
                seen["prompts"] = kwargs.get("prompts")
                md = os.path.splitext(txt_path)[0] + "_redactor.md"
                with open(md, "w", encoding="utf-8") as handle:
                    handle.write("edited")
                return [md]

            class Provider:
                def has_api_credentials(self, credentials):
                    return True

            with patch("whisperfast.telegram.pipeline.get_provider", return_value=Provider()):
                with patch(
                    "whisperfast.postprocess.cursor_postprocess.ensure_redactor_file",
                    return_value="",
                ):
                    with patch(
                        "whisperfast.postprocess.cursor_postprocess.parse_redactor_prompts",
                        return_value=[(1, "redactor", "body")],
                    ):
                        result = run_pipeline(
                            media,
                            {"ai_provider": "cursor", "ai_default_prompt_nums": [1], "lang_mode": "None"},
                            model_getter=lambda log, device, name: Model(),
                            ai_runner=ai_runner,
                        )
            self.assertTrue(result.txt_path.endswith("clip.txt"))
            with open(result.txt_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "hello")
            self.assertEqual(len(result.md_paths), 1)
            self.assertEqual(seen["delay"], 0)
            self.assertEqual(seen["kwargs"]["vad_filter"], True)
            self.assertIsNone(seen["kwargs"]["language"])

    def test_missing_api_key_skips_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "clip.mp3")
            open(media, "wb").close()
            called = {"ai": False}

            class Model:
                def transcribe(self, path, **kwargs):
                    seg = SimpleNamespace(start=0.0, end=1.0, text="hi", speaker="")
                    return iter([seg]), None

            class Provider:
                def has_api_credentials(self, credentials):
                    return False

            def ai_runner(*args, **kwargs):
                called["ai"] = True
                return []

            with patch("whisperfast.telegram.pipeline.get_provider", return_value=Provider()):
                with self.assertRaises(PipelineError) as ctx:
                    run_pipeline(
                        media,
                        {"ai_provider": "cursor"},
                        model_getter=lambda log, device, name: Model(),
                        ai_runner=ai_runner,
                    )
            self.assertFalse(called["ai"])
            self.assertTrue(ctx.exception.txt_path.endswith(".txt"))


class TestClient(unittest.TestCase):
    def test_get_file_returns_local_path_and_send_message_posts_chat(self):
        calls = []

        def transport(url, params, timeout):
            calls.append((url, params))
            if url.endswith("/getFile"):
                return {"ok": True, "result": {"file_path": r"D:\bot-api\voice.ogg"}}
            if url.endswith("/sendMessage"):
                return {"ok": True, "result": {"message_id": 1}}
            return {"ok": False, "description": "nope"}

        client = TelegramClient("secret-token", api_base="http://127.0.0.1:8081", transport=transport)
        self.assertEqual(client.get_file_path("abc"), r"D:\bot-api\voice.ogg")
        client.send_message(15, "hi", reply_to=4)
        self.assertIn("/botsecret-token/getFile", calls[0][0])
        self.assertEqual(calls[1][1]["chat_id"], 15)
        self.assertEqual(calls[1][1]["reply_to_message_id"], 4)
        with self.assertRaises(TelegramApiError):
            unwrap_result({"ok": False, "description": "bad"})


class TestLocalServerAndCli(unittest.TestCase):
    def test_starts_local_server_when_port_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "telegram-bot-api.exe")
            open(exe, "wb").close()
            started = {}

            def popen(cmd, **kwargs):
                started["cmd"] = cmd
                return SimpleNamespace()

            settings = {
                "telegram_api_base": "http://127.0.0.1:8081",
                "telegram_bot_api_exe": exe,
                "telegram_api_id": "100",
                "telegram_api_hash": "hash",
                "telegram_work_dir": tmp,
            }
            with patch("whisperfast.telegram.worker.port_is_open", side_effect=[False, True]):
                with patch("whisperfast.telegram.worker.time.sleep"):
                    ensure_local_api(settings, log=lambda _msg: None, popen=popen, wait_s=5)
            cmd = started["cmd"]
            self.assertIn("--local", cmd)
            self.assertIn("--api-id=100", cmd)
            self.assertIn("--api-hash=hash", cmd)
            self.assertIn("--http-port=8081", cmd)

    def test_missing_exe_exits(self):
        settings = {
            "telegram_api_base": "http://127.0.0.1:8081",
            "telegram_bot_api_exe": "",
        }
        with patch("whisperfast.telegram.worker.port_is_open", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                ensure_local_api(settings, log=lambda _msg: None)
        self.assertIn("telegram-bot-api", str(ctx.exception))

    def test_cli_telegram_does_not_fall_through(self):
        with patch("whisperfast.telegram.worker.run_from_settings", return_value=0) as run:
            handled = maybe_run_cli(["main.py", "--telegram"])
        self.assertTrue(handled)
        run.assert_called_once()


class TestTelegramSettingsDialog(unittest.TestCase):
    def test_dialog_opens_with_saved_values(self):
        import tkinter as tk
        from types import SimpleNamespace

        from whisperfast.ui.dialogs import show_telegram_settings_dialog

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"no Tk display available: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        saved = []
        app = SimpleNamespace(
            root=root,
            telegram_bot_token=tk.StringVar(master=root, value="secret-token"),
            telegram_api_id=tk.StringVar(master=root, value="100"),
            telegram_api_hash=tk.StringVar(master=root, value="hash"),
            telegram_bot_api_exe=tk.StringVar(master=root, value=""),
            telegram_api_base=tk.StringVar(master=root, value="http://127.0.0.1:8081"),
            telegram_allowed_chat_ids_text=tk.StringVar(master=root, value="42, -100"),
            telegram_work_dir=tk.StringVar(master=root, value=""),
            telegram_mode=tk.StringVar(master=root, value="bot"),
            telegram_phone=tk.StringVar(master=root, value=""),
            _persist_settings=lambda: saved.append(True),
        )
        show_telegram_settings_dialog(app)
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(tops), 1)
        self.assertEqual(tops[0].title(), "Telegram")
        tops[0].destroy()


class TestAccountMode(unittest.TestCase):
    def test_private_chats_only(self):
        from whisperfast.telegram.account import accept_private_chat, media_filename, normalize_mode

        self.assertTrue(
            accept_private_chat(5, is_private=True, sender_is_bot=False, outgoing=False, self_id=1, allowlist=[])
        )
        self.assertTrue(
            accept_private_chat(1, is_private=True, sender_is_bot=False, outgoing=True, self_id=1, allowlist=[])
        )
        self.assertFalse(
            accept_private_chat(9, is_private=True, sender_is_bot=False, outgoing=True, self_id=1, allowlist=[])
        )
        self.assertTrue(
            accept_private_chat(
                9,
                is_private=True,
                sender_is_bot=False,
                outgoing=True,
                self_id=1,
                allowlist=[],
                self_names=["олексій", "@maria"],
                chat_names=["Олексій Коваль", "Олексій"],
            )
        )
        self.assertTrue(
            accept_private_chat(
                9,
                is_private=True,
                sender_is_bot=False,
                outgoing=True,
                self_id=1,
                allowlist=[],
                self_names=["Maria"],
                chat_names=["maria"],
            )
        )
        self.assertFalse(
            accept_private_chat(
                9,
                is_private=True,
                sender_is_bot=False,
                outgoing=True,
                self_id=1,
                allowlist=[],
                self_names=["Інший"],
                chat_names=["Олексій"],
            )
        )
        self.assertFalse(
            accept_private_chat(5, is_private=False, sender_is_bot=False, outgoing=False, self_id=1, allowlist=[])
        )
        self.assertTrue(
            accept_private_chat(
                5,
                is_private=False,
                is_group=True,
                sender_is_bot=False,
                outgoing=False,
                self_id=1,
                allowlist=[],
                self_names=["NPG Archive TG"],
                chat_names=["npg archive tg"],
            )
        )
        self.assertFalse(
            accept_private_chat(
                5,
                is_private=False,
                is_group=True,
                sender_is_bot=False,
                outgoing=False,
                self_id=1,
                allowlist=[],
                self_names=["Інша група"],
                chat_names=["NPG Archive TG"],
            )
        )
        self.assertFalse(
            accept_private_chat(5, is_private=True, sender_is_bot=True, outgoing=False, self_id=1, allowlist=[])
        )
        self.assertFalse(
            accept_private_chat(5, is_private=True, sender_is_bot=False, outgoing=False, self_id=1, allowlist=[9])
        )
        self.assertTrue(
            accept_private_chat(9, is_private=True, sender_is_bot=False, outgoing=False, self_id=1, allowlist=[9])
        )
        self.assertEqual(normalize_mode("Account"), "account")
        self.assertEqual(normalize_mode("group"), "bot")
        self.assertTrue(media_filename("clip.mp4", "video/mp4").endswith("clip.mp4"))
        self.assertTrue(media_filename("", "audio/ogg").endswith(".ogg"))
        self.assertIsNone(media_filename("note.txt", "text/plain"))

    def test_outbox_round_trip(self):
        from whisperfast.telegram.outbox import enqueue_outgoing, take_outgoing

        with tempfile.TemporaryDirectory() as tmp:
            with patch("whisperfast.telegram.outbox.outbox_dir", return_value=tmp):
                enqueue_outgoing(7, 8, text="hi", files=[{"path": "a.txt", "caption": "c"}])
                items = take_outgoing()
                self.assertEqual(take_outgoing(), [])
        self.assertEqual(items[0]["chat_id"], 7)
        self.assertEqual(items[0]["reply_to"], 8)
        self.assertEqual(items[0]["text"], "hi")
        self.assertEqual(items[0]["files"][0]["caption"], "c")

    def test_run_from_settings_uses_the_account_listener(self):
        from whisperfast.telegram.worker import run_from_settings

        with patch("whisperfast.telegram.worker.load_app_settings", return_value={"telegram_mode": "account"}):
            with patch("whisperfast.telegram.account.run_account", return_value=0) as account:
                with patch("whisperfast.telegram.worker.run_bot") as bot:
                    self.assertEqual(run_from_settings(), 0)
        account.assert_called_once()
        bot.assert_not_called()

    def test_deliver_in_account_mode_uses_the_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "clip.mp3")
            txt = os.path.join(tmp, "clip.txt")
            open(media, "wb").close()
            with open(txt, "w", encoding="utf-8") as handle:
                handle.write("hello")

            class Store:
                def get_file(self, file_id):
                    return {
                        "id": file_id,
                        "status": "done",
                        "source": media,
                        "outputs": [{"role": "txt", "path": txt}],
                    }

            app = SimpleNamespace(
                queue_ctrl=SimpleNamespace(
                    queue=[{"path": media, "telegram_chat_id": 42, "telegram_message_id": 3}]
                ),
                log_panel=SimpleNamespace(_store=Store()),
                ai_jobs=SimpleNamespace(_jobs={}),
            )

            class Immediate:
                def __init__(self, target=None, daemon=None):
                    self._target = target

                def start(self):
                    self._target()

            with patch("whisperfast.telegram.gui_bridge.threading.Thread", Immediate):
                with patch(
                    "whisperfast.settings.load_app_settings",
                    return_value={"telegram_mode": "account"},
                ):
                    with patch("whisperfast.telegram.outbox.outbox_dir", return_value=tmp):
                        maybe_deliver_telegram(app, file_id="f1")
                        from whisperfast.telegram.outbox import take_outgoing

                        items = take_outgoing()
        self.assertEqual(items[0]["chat_id"], 42)
        self.assertEqual(items[0]["reply_to"], 3)
        self.assertEqual(os.path.basename(items[0]["files"][0]["path"]), "clip.txt")


class TestListenerKind(unittest.TestCase):
    def test_bot_needs_a_token_and_account_needs_a_session(self):
        from whisperfast.telegram.service import listener_kind

        self.assertIsNone(listener_kind({"telegram_mode": "bot"}))
        self.assertEqual(listener_kind({"telegram_mode": "bot", "telegram_bot_token": "123:abc"}), "bot")
        self.assertIsNone(
            listener_kind(
                {
                    "telegram_mode": "account",
                    "telegram_api_id": "1",
                    "telegram_api_hash": "h",
                    "telegram_phone": "+380501111111",
                }
            )
        )


class TestInWindowListener(unittest.TestCase):
    def test_start_uses_this_window_and_stop_ends_it(self):
        import whisperfast.telegram.service as service

        seen = {}
        started = threading.Event()

        def fake_run(**kwargs):
            seen.update(kwargs)
            started.set()
            while not kwargs["stop"]():
                time.sleep(0.02)
            return 0

        with patch("whisperfast.telegram.worker.run_from_settings", fake_run):
            service.stop()
            self.assertTrue(service.start())
            self.assertFalse(service.start())
            self.assertTrue(started.wait(2))
            self.assertTrue(seen["gui_running"]())
            service.stop()
            deadline = time.time() + 2
            while service.is_running() and time.time() < deadline:
                time.sleep(0.02)
        self.assertFalse(service.is_running())


if __name__ == "__main__":
    unittest.main()

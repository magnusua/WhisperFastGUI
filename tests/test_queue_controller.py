"""QueueController persist and mutations without Tkinter."""
import os
import tempfile
import unittest
from unittest.mock import patch

from whisperfast.core.queue_manager import QueueController
from whisperfast.utils import make_queue_item


class FakeTreeview:
    def __init__(self):
        self.rows = []
        self._n = 0

    def insert(self, parent, index, values=()):
        self._n += 1
        iid = str(self._n)
        self.rows.append((iid, values))
        return iid

    def delete(self, *iids):
        drop = set(iids)
        self.rows = [row for row in self.rows if row[0] not in drop]

    def get_children(self):
        return [row[0] for row in self.rows]


def _touch(path, data=b"x"):
    with open(path, "wb") as f:
        f.write(data)


class TestQueueController(unittest.TestCase):
    def setUp(self):
        self._duration = patch("whisperfast.utils.get_audio_duration_seconds", return_value=12.0)
        self._duration.start()
        self.addCleanup(self._duration.stop)

    def _controller(self, tmp, bind=True):
        qfile = os.path.join(tmp, "request_queue.json")
        ctrl = QueueController(request_queue_file=qfile, log_func=lambda *a, **k: None)
        if bind:
            ctrl.bind_treeview(FakeTreeview())
        return ctrl

    def test_add_files_requires_treeview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp3")
            _touch(path)
            ctrl = self._controller(tmp, bind=False)
            added, skipped = ctrl.add_files([path])
            self.assertEqual((added, skipped), (0, 0))
            self.assertEqual(ctrl.queue, [])

    def test_add_files_and_skip_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp3")
            _touch(path)
            ctrl = self._controller(tmp)
            added, skipped = ctrl.add_files([path])
            self.assertEqual(added, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(ctrl.queue), 1)
            added2, skipped2 = ctrl.add_files([path])
            self.assertEqual(added2, 0)
            self.assertEqual(skipped2, 1)
            self.assertEqual(len(ctrl.queue), 1)

    def test_persist_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.wav")
            _touch(path)
            ctrl = self._controller(tmp)
            ctrl.add_files([path])
            ctrl.queue[0]["processed"] = True
            ctrl.save_to_file()

            other = self._controller(tmp)
            other.load_from_file()
            self.assertEqual(len(other.queue), 1)
            self.assertEqual(os.path.normcase(other.queue[0]["path"]), os.path.normcase(path))
            self.assertTrue(other.queue[0]["processed"])

    def test_mark_done_and_remove_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.mp3")
            b = os.path.join(tmp, "b.mp3")
            _touch(a)
            _touch(b)
            ctrl = self._controller(tmp)
            ctrl.add_files([a, b])
            ctrl.mark_done(0)
            self.assertTrue(ctrl.queue[0]["processed"])
            self.assertFalse(ctrl.queue[1]["processed"])
            ctrl.mark_done_by_path(b)
            self.assertTrue(ctrl.queue[1]["processed"])
            ctrl.remove_paths([a])
            self.assertEqual(len(ctrl.queue), 1)
            self.assertEqual(os.path.normcase(ctrl.queue[0]["path"]), os.path.normcase(b))

    def test_relocate_and_mark_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.mp3")
            dest = os.path.join(tmp, "moved.mp3")
            _touch(src)
            ctrl = self._controller(tmp)
            ctrl.queue.append(make_queue_item(src))
            ctrl.relocate_and_mark_done(src, dest)
            self.assertEqual(os.path.normcase(ctrl.queue[0]["path"]), os.path.normcase(dest))
            self.assertTrue(ctrl.queue[0]["processed"])

    def test_continue_after_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = self._controller(tmp, bind=False)
            calls = []
            ctrl._start_processing = lambda mode, target_idx=None, from_watch=False: calls.append(
                (mode, from_watch)
            )
            ctrl.queue.append(make_queue_item(os.path.join(tmp, "x.mp3")))

            ctrl.watch_pending_continue = True
            self.assertFalse(ctrl.continue_after_processing(cancel_requested=True))
            self.assertFalse(ctrl.watch_pending_continue)
            self.assertEqual(calls, [])

            ctrl.watch_pending_continue = False
            self.assertFalse(ctrl.continue_after_processing(from_watch=False))
            self.assertEqual(calls, [])

            ctrl.watch_pending_continue = True
            self.assertTrue(ctrl.continue_after_processing(from_watch=False))
            self.assertEqual(calls, [("only_new", False)])
            self.assertFalse(ctrl.watch_pending_continue)

            calls.clear()
            ctrl.queue[0]["processed"] = False
            self.assertTrue(ctrl.continue_after_processing(from_watch=True))
            self.assertEqual(calls, [("only_new", False)])

            calls.clear()
            ctrl.queue[0]["processed"] = True
            self.assertFalse(ctrl.continue_after_processing(from_watch=True))
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

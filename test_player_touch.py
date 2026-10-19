"""Exercise the real timeline callbacks without Qt, audio hardware or mpv.

As with the startup/power tests, compile selected GUI source methods and supply
small widget doubles. This keeps the regressions runnable on development PCs.
"""
import ast
import bisect
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).with_name("player.py")


def source_class(name, namespace, methods=None, base=None):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name)
    if methods is not None:
        node.body = [item for item in node.body if isinstance(item, ast.FunctionDef) and item.name in methods]
    node.bases = [ast.Name(id=base, ctx=ast.Load())] if base else []
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(SOURCE), "exec"), namespace)
    return namespace[name]


class SliderDouble:
    def __init__(self):
        self.down = False
        self.current = 100
        self.position = 100
        self.changes = []
        self.focus = None
        self.released = Mock()

    def isSliderDown(self):
        return self.down

    def setSliderDown(self, down):
        released = self.down and not down
        self.down = down
        if released:
            self.released()

    def setSliderPosition(self, value):
        self.position = self.current = value
        self.changes.append(value)

    def setValue(self, value):
        self.current = value
        self.changes.append(value)

    def value(self):
        return self.current

    def sliderPosition(self):
        return self.position

    def setFocus(self, reason):
        self.focus = reason


class TouchSliderTests(unittest.TestCase):
    def setUp(self):
        self.qt = SimpleNamespace(LeftButton=1, MouseFocusReason=2, Horizontal=3)
        slider_class = source_class("SeekSlider", {"QSlider": SliderDouble, "Qt": self.qt},
                                    base="QSlider")
        self.slider = slider_class()
        self.slider.value_at = Mock(side_effect=lambda point: point)

    def event(self, position):
        return SimpleNamespace(button=lambda: 1, pos=lambda: position, accept=Mock())

    def test_tapping_anywhere_sets_that_position_and_commits_on_release(self):
        press, release = self.event(720), self.event(720)
        self.slider.mousePressEvent(press)
        self.assertEqual(self.slider.value(), 720)
        self.assertTrue(self.slider.isSliderDown())
        self.slider.released.assert_not_called()
        self.slider.mouseReleaseEvent(release)
        self.assertFalse(self.slider.isSliderDown())
        self.slider.released.assert_called_once()
        press.accept.assert_called_once()
        release.accept.assert_called_once()

    def test_drag_updates_position_and_commits_only_final_release(self):
        self.slider.mousePressEvent(self.event(200))
        self.slider.mouseMoveEvent(self.event(550))
        self.slider.mouseMoveEvent(self.event(800))
        self.slider.released.assert_not_called()
        self.slider.mouseReleaseEvent(self.event(900))
        self.assertEqual(self.slider.changes, [200, 550, 800, 900])
        self.assertEqual(self.slider.focus, self.qt.MouseFocusReason)
        self.slider.released.assert_called_once()


class PlayerSeekTests(unittest.TestCase):
    def setUp(self):
        self.process_types = SimpleNamespace(Running=2)
        player_class = source_class("Player", {
            "QProcess": self.process_types,
            "bisect": bisect,
            "clock": lambda value: f"{int(value) // 60}:{int(value) % 60:02d}",
        }, methods={"preview_seek", "seek_action", "seek_to", "submit_seek", "tick",
                    "control_result", "control_failure"})
        self.player = player_class()
        self.player.generation = 7
        self.player.process = SimpleNamespace(state=lambda: self.process_types.Running)
        self.player.control = Mock()
        self.player.seek = SliderDouble()
        self.player.volume = Mock()
        self.player.volume.isSliderDown.return_value = False
        self.player.duration = 200
        self.player.seek_revision = 0
        self.player.pending_seek = None
        self.player.requested_seek = None
        self.player.pending_volume = None
        self.player.from_cd = False
        self.player.cd_offsets = []
        self.player.names = [("Song", "Artist")]
        self.player.position = 0
        self.player.main = SimpleNamespace(message=Mock())
        for field in ("elapsed", "total", "pause", "volume_text", "title", "artist", "changed"):
            setattr(self.player, field, Mock())

    def state(self, revision=0, elapsed=20):
        return {"time-pos": elapsed, "duration": 200, "pause": False,
                "playlist-pos": 0, "volume": 40, "_seek_revision": revision}

    def test_keyboard_uses_new_position_before_qt_updates_value(self):
        self.player.seek.position = 750
        self.player.seek_action(1)
        self.player.control.submit.assert_called_once_with(7, "seek", (1, 150.0))
        self.player.elapsed.setText.assert_called_once_with("2:30")

    def test_drag_preview_and_inflight_state_cannot_move_the_timeline(self):
        self.player.seek.down = True
        self.player.preview_seek(750)
        self.player.seek_action(7)
        self.player.control_result(7, "state", self.state())
        self.player.control.submit.assert_not_called()
        self.assertEqual(self.player.seek.changes, [])
        self.player.elapsed.setText.assert_called_once_with("2:30")
        self.player.total.setText.assert_not_called()

    def test_old_poll_cannot_snap_back_before_or_after_seek_acknowledgement(self):
        self.player.seek.current = 750
        self.player.seek_to()
        self.player.tick()
        self.player.control_result(7, "state", self.state())
        self.assertEqual(self.player.seek.changes, [])
        self.assertEqual(self.player.control.submit.call_count, 1)
        self.player.control_result(7, "seek", 1)
        self.player.control.submit.assert_called_with(7, "state", 1)
        self.player.control_result(7, "state", self.state())
        self.assertEqual(self.player.seek.changes, [])
        self.player.control_result(7, "state", self.state(revision=1, elapsed=151))
        self.assertEqual(self.player.seek.changes, [755])
        self.player.elapsed.setText.assert_called_with("2:31")

    def test_rapid_requests_coalesce_to_latest_target_until_acknowledged(self):
        self.player.seek_to(100)
        self.player.seek_to(500)
        self.player.seek_to(900)
        self.player.control.submit.assert_called_once_with(7, "seek", (1, 20.0))
        self.player.control_result(7, "seek", 1)
        self.player.control.submit.assert_called_with(7, "seek", (3, 180.0))
        self.assertEqual(self.player.control.submit.call_count, 2)
        self.player.control_result(7, "seek", 3)
        self.player.control.submit.assert_called_with(7, "state", 3)

    def test_cd_seek_uses_full_disc_time_without_adding_track_offset(self):
        self.player.from_cd = True
        self.player.cd_offsets = [0, 180, 400]
        self.player.duration = 2400
        self.player.seek_to(500)
        self.player.control.submit.assert_called_once_with(7, "seek", (1, 1200.0))

    def test_failure_reports_problem_and_resumes_state_polling(self):
        self.player.seek_to(500)
        self.player.control_failure(7, "seek", "Seek not available")
        self.assertIsNone(self.player.pending_seek)
        self.player.main.message.setText.assert_called_once_with(
            "Playback control could not apply the change: Seek not available")
        self.player.control.submit.assert_called_with(7, "state", 1)

    def test_previous_session_reply_cannot_acknowledge_current_seek(self):
        self.player.seek_to(500)
        self.player.control_result(6, "seek", 1)
        self.assertEqual(self.player.pending_seek, 1)
        self.assertEqual(self.player.control.submit.call_count, 1)


class SeekWorkerTests(unittest.TestCase):
    def setUp(self):
        self.request = Mock(return_value=[{"error": "success", "data": None}])
        worker_class = source_class("ControlWorker", {
            "QThread": object, "queue": queue, "threading": threading,
            "request_batch": self.request, "ControlCancelled": type("ControlCancelled", (Exception,), {}),
        }, methods={"__init__", "session", "submit", "run"}, base="QThread")
        self.worker = worker_class("/test/mpv.sock")
        self.worker.completed = Mock()
        self.worker.failed = Mock()
        self.worker.session(7)

    def run_worker(self):
        self.worker.tasks.put(None)
        self.worker.run()
        self.worker.failed.emit.assert_not_called()

    def test_seek_is_exact_absolute_and_acknowledges_request_revision(self):
        self.worker.submit(7, "seek", (4, 123.5))
        self.run_worker()
        self.assertEqual(self.request.call_args.args, ("/test/mpv.sock", [["seek", 123.5, "absolute+exact"]]))
        self.worker.completed.emit.assert_called_once_with(7, "seek", 4)

    def test_state_poll_keeps_revision_from_submission(self):
        self.request.return_value = [{"error": "success", "data": value}
                                     for value in (12, 200, False, 0, 0, 40)]
        self.worker.submit(7, "state", 3)
        self.run_worker()
        result = self.worker.completed.emit.call_args.args[2]
        self.assertEqual(result["_seek_revision"], 3)
        self.assertEqual(result["time-pos"], 12)


if __name__ == "__main__":
    unittest.main()

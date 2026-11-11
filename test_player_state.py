"""Playback snapshots stay consistent for the optional Now playing screen."""
import bisect
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_player_touch import source_class


class PlayerStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.process_types = SimpleNamespace(NotRunning=0, Starting=1, Running=2,
                                             FailedToStart=3, NormalExit=0)
        self.box = SimpleNamespace(warning=Mock(), information=Mock())
        self.storage = Mock()
        self.playlist = Mock(return_value=str(Path(self.directory.name) / "queue.m3u8"))
        self.failed_exit = Mock(return_value=False)
        cls = source_class("Player", {
            "QProcess": self.process_types, "QMessageBox": self.box,
            "Path": Path, "os": os, "bisect": bisect,
            "require_storage": self.storage, "music_playlist": self.playlist,
            "report_playback_exit": self.failed_exit,
            "clock": lambda value: f"{int(value) // 60}:{int(value) % 60:02d}",
        }, methods={"play", "started", "stop", "toggle", "skip", "command", "clear_playlist",
                    "finished", "process_error", "control_result"})
        self.player = cls()
        self.player.demo = False
        self.player.config = {"audio_device": "auto", "device": "/dev/sr0"}
        self.player.process = Mock()
        self.player.process.state.return_value = self.process_types.NotRunning
        self.player.process.start.side_effect = lambda *_: setattr(
            self.player.process.state, "return_value", self.process_types.Starting)
        self.player.control = Mock()
        self.player.sources = []
        self.player.names = []
        self.player.position = -1
        self.player.playback_state = "stopped"
        self.player.from_cd = False
        self.player.cd_offsets = []
        self.player.generation = 0
        self.player.playlist_path = None
        self.player.ipc = str(Path(self.directory.name) / "unused.sock")
        self.player.intentional_stop = False
        self.player.diagnostics = ""
        for name in ("title", "artist", "pause", "elapsed", "total", "seek", "volume",
                     "volume_text", "read_error", "changed"):
            setattr(self.player, name, Mock())
        self.player.volume.value.return_value = 40
        self.player.seek.isSliderDown.return_value = False
        self.player.volume.isSliderDown.return_value = False
        self.snapshots = []
        self.player.changed.emit.side_effect = lambda: self.snapshots.append((
            self.player.playback_state, self.player.position, list(self.player.names),
            list(self.player.sources), self.player.from_cd))
        self.names = [("First song", "First artist"), ("Second song", "Second artist")]
        self.sources = ["/music/one.flac", "/music/two.flac"]

    def play(self, **options):
        self.player.play(self.sources, self.names, **options)

    def poll(self, **changes):
        values = {"time-pos": 30, "duration": 180, "pause": False, "volume": 40,
                  "playlist-pos": 0, "_seek_revision": self.player.seek_revision}
        values.update(changes)
        self.player.control_result(self.player.generation, "state", values)

    def test_selected_track_is_present_in_first_loading_snapshot(self):
        self.play(start_index=1)
        self.assertEqual(self.snapshots[-1], ("loading", 1, self.names, self.sources, False))
        self.player.title.setText.assert_called_with("Second song")
        self.assertIn("--playlist-start=1", self.player.process.start.call_args.args[1])
        self.player.process.state.return_value = self.process_types.Running
        self.player.started()
        self.assertEqual(self.snapshots[-1][0:2], ("playing", 1))

    def test_cd_time_offset_selects_matching_track_before_first_poll(self):
        self.player.play(["cdda://"], self.names, from_cd=True,
                         cd_offsets=[0, 180], start_seconds=180)
        self.assertEqual(self.snapshots[-1], ("loading", 1, self.names, ["cdda://"], True))
        self.player.title.setText.assert_called_with("Second song")
        self.storage.assert_not_called()
        self.playlist.assert_not_called()

    def test_pause_and_track_change_emit_current_snapshot_once(self):
        self.play()
        self.player.process.state.return_value = self.process_types.Running
        self.player.started()
        self.snapshots.clear()
        self.poll(pause=True, **{"playlist-pos": 1})
        self.assertEqual(len(self.snapshots), 1)
        self.assertEqual(self.snapshots[0][0:2], ("paused", 1))
        self.poll(pause=True, **{"playlist-pos": 1})
        self.assertEqual(len(self.snapshots), 1)
        self.poll(pause=False, **{"playlist-pos": 1})
        self.assertEqual(self.snapshots[-1][0:2], ("playing", 1))

    def test_finish_retains_track_for_replay_but_stop_clears_details(self):
        self.play(start_index=1)
        self.player.process.state.return_value = self.process_types.NotRunning
        self.player.finished(0, self.process_types.NormalExit)
        self.assertEqual(self.snapshots[-1], ("finished", 1, self.names, self.sources, False))
        self.player.stop()
        self.assertEqual(self.snapshots[-1], ("stopped", -1, [], [], False))
        self.player.title.setText.assert_called_with("Choose something to play")
        self.player.artist.setText.assert_called_with("Your music")

    def test_failure_is_reported_to_screen_and_can_be_retried(self):
        self.play()
        self.player.process.state.return_value = self.process_types.NotRunning
        self.player.process_error(self.process_types.FailedToStart)
        self.assertEqual(self.snapshots[-1][0], "error")
        self.player.toggle()
        self.assertEqual(self.snapshots[-1][0:2], ("loading", 0))
        self.assertEqual(self.player.process.start.call_count, 2)

    def test_old_poll_cannot_change_new_track_or_pause_state(self):
        self.play()
        old_generation = self.player.generation
        self.player.process.state.return_value = self.process_types.NotRunning
        self.play(start_index=1)
        self.player.process.state.return_value = self.process_types.Running
        self.player.started()
        self.snapshots.clear()
        self.player.control_result(old_generation, "state", {"pause": True, "playlist-pos": 0})
        self.assertEqual(self.snapshots, [])
        self.assertEqual(self.player.position, 1)
        self.assertEqual(self.player.playback_state, "playing")

    def test_demo_can_preview_track_changes_and_pause_without_hardware(self):
        self.player.demo = True
        self.play(start_index=1)
        self.assertEqual(self.snapshots[-1], ("playing", 1, self.names, self.sources, False))
        self.player.toggle()
        self.assertEqual(self.player.playback_state, "paused")
        self.player.skip(-1)
        self.assertEqual(self.snapshots[-1][0:2], ("paused", 0))
        self.player.toggle()
        self.assertEqual(self.player.playback_state, "playing")
        self.player.process.start.assert_not_called()
        self.storage.assert_not_called()
        self.playlist.assert_not_called()
        self.player.control.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

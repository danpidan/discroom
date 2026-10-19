"""Stored playback queues use temporary playlists, including large libraries."""
import ast
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_player_touch import source_class


SOURCE = Path(__file__).with_name("player.py")


class PlaylistTestBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SOURCE.parent)
        self.addCleanup(self.directory.cleanup)
        self.created = []

        def temporary_file(**options):
            descriptor, path = tempfile.mkstemp(dir=self.directory.name, **options)
            self.created.append((descriptor, Path(path)))
            return descriptor, path

        self.temporary_file = Mock(side_effect=temporary_file)
        self.namespace = {"Path": Path, "os": os,
                          "tempfile": SimpleNamespace(mkstemp=self.temporary_file)}
        function = next(node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                        if isinstance(node, ast.FunctionDef) and node.name == "music_playlist")
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), self.namespace)
        self.make_playlist = self.namespace["music_playlist"]


class PlaylistFileTests(PlaylistTestBase):
    def test_utf8_paths_are_absolute_and_preserve_spaces_hashes_and_order(self):
        sources = [str(Path(self.directory.name) / "Beyoncé #1.flac"), "relative music/次の曲.flac"]
        playlist = Path(self.make_playlist(sources))
        self.assertEqual(playlist.suffix, ".m3u8")
        self.assertEqual(playlist.read_text(encoding="utf-8").splitlines(),
                         ["#EXTM3U", *[str(Path(source).resolve()) for source in sources]])

    def test_line_breaks_cannot_inject_extra_playlist_entries(self):
        for name in ("first\nsecond.flac", "first\rsecond.flac"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "line break.*Rename"):
                self.make_playlist([name])
        self.temporary_file.assert_not_called()

    def test_fdopen_failure_closes_descriptor_and_removes_temporary_file(self):
        self.namespace["os"] = SimpleNamespace(fdopen=Mock(side_effect=OSError("Cannot open")), close=os.close)
        with self.assertRaisesRegex(OSError, "Cannot open"):
            self.make_playlist(["track.flac"])
        descriptor, path = self.created[0]
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertFalse(path.exists())

    def test_write_failure_closes_descriptor_and_removes_partial_playlist(self):
        class FailedWrite:
            def __init__(self, descriptor, *args, **options):
                self.file = os.fdopen(descriptor, *args, **options)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.file.close()

            def write(self, text):
                raise OSError("Drive full")

        self.namespace["os"] = SimpleNamespace(fdopen=FailedWrite, close=os.close)
        with self.assertRaisesRegex(OSError, "Drive full"):
            self.make_playlist(["track.flac"])
        descriptor, path = self.created[0]
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertFalse(path.exists())


class PlaybackQueueTests(PlaylistTestBase):
    def setUp(self):
        super().setUp()
        self.box = SimpleNamespace(warning=Mock(), information=Mock())
        self.process_types = SimpleNamespace(Running=2, NotRunning=0, FailedToStart=3, NormalExit=0)
        self.storage = Mock()
        self.playlist = Mock(wraps=self.make_playlist)
        player_class = source_class("Player", {
            "Path": Path, "os": os, "QProcess": self.process_types,
            "require_storage": self.storage, "music_playlist": self.playlist,
            "QMessageBox": self.box, "report_playback_exit": Mock(return_value=False),
        }, methods={"play", "clear_playlist", "process_error", "finished", "stop", "shutdown"})
        self.player = player_class()
        self.player.config = {"audio_device": "auto", "device": "/dev/sr0"}
        self.player.demo = False
        self.player.playlist_path = None
        self.player.process = Mock()
        self.player.process.state.return_value = self.process_types.NotRunning
        self.player.volume = Mock()
        self.player.volume.value.return_value = 40
        self.player.control = Mock()
        self.player.generation = 0
        self.player.ipc = str(Path(self.directory.name) / "unused.sock")
        self.player.diagnostics = ""
        self.player.intentional_stop = False
        self.player.stop = Mock(side_effect=self.player.clear_playlist)
        for field in ("title", "artist", "pause", "changed", "elapsed", "total", "seek", "read_error",
                      "timer", "volume_save_timer", "save_volume"):
            setattr(self.player, field, Mock())

    def play(self, sources=None, **options):
        sources = sources or ["music/track.flac"]
        self.player.play(sources, [("Song", "Artist")] * len(sources), **options)

    def test_large_queue_uses_one_playlist_option_and_preserves_selected_start(self):
        sources = [f"music/track {index}.flac" for index in range(20000)]
        self.play(sources, start_index=123)
        program, arguments = self.player.process.start.call_args.args
        self.assertEqual(program, "mpv")
        self.assertLess(len(arguments), 15)
        self.assertIn(f"--playlist={self.player.playlist_path}", arguments)
        self.assertIn("--playlist-start=123", arguments)
        self.assertIn("--loop-playlist=no", arguments)
        self.assertIn("--loop-file=no", arguments)
        self.assertEqual(len(Path(self.player.playlist_path).read_text(encoding="utf-8").splitlines()), 20001)
        self.assertEqual(self.player.sources, sources)

    def test_direct_cd_stays_a_direct_source_and_does_not_create_playlist(self):
        self.play(["cdda://"], from_cd=True, cd_offsets=[0, 180], start_seconds=180)
        arguments = self.player.process.start.call_args.args[1]
        self.assertEqual(arguments[-2:], ["--", "cdda://"])
        self.assertIn("--cdrom-device=/dev/sr0", arguments)
        self.assertIn("--start=180", arguments)
        self.playlist.assert_not_called()
        self.storage.assert_not_called()

    def test_playlist_survives_start_then_is_cleaned_after_playback_finishes(self):
        self.play()
        path = Path(self.player.playlist_path)
        self.assertTrue(path.exists())
        self.player.finished(0, self.process_types.NormalExit)
        self.assertFalse(path.exists())
        self.assertIsNone(self.player.playlist_path)

    def test_failed_process_start_cleans_playlist(self):
        self.play()
        path = Path(self.player.playlist_path)
        self.player.process_error(self.process_types.FailedToStart)
        self.assertFalse(path.exists())
        self.box.warning.assert_called_once()

    def test_stop_cleans_playlist_and_repeated_cleanup_is_harmless(self):
        self.play()
        path = Path(self.player.playlist_path)
        type(self.player).stop(self.player)
        self.player.clear_playlist()
        self.assertFalse(path.exists())
        self.assertIsNone(self.player.playlist_path)

    def test_new_playback_replaces_and_cleans_previous_playlist(self):
        self.play()
        first = Path(self.player.playlist_path)
        self.play(["music/next.flac"])
        self.assertFalse(first.exists())
        self.assertTrue(Path(self.player.playlist_path).exists())

    def test_shutdown_uses_stop_to_clean_playlist(self):
        self.play()
        path = Path(self.player.playlist_path)
        self.player.control.isRunning.return_value = False
        self.player.shutdown()
        self.assertFalse(path.exists())

    def test_invalid_path_reports_filename_problem_without_starting_mpv(self):
        self.play(["music/bad\nname.flac"])
        self.player.process.start.assert_not_called()
        self.assertIn("line break", self.box.warning.call_args.args[2])
        self.assertEqual(list(Path(self.directory.name).glob("*.m3u8")), [])


if __name__ == "__main__":
    unittest.main()

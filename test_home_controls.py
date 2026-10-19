"""Exercise actual home callbacks with controlled window and playback effects."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

import test_core
import core


class HomeControlTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("discroom.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        names = {"set_fullscreen", "toggle_fullscreen", "return_to_desktop", "shuffle_library"}
        window.body = [node for node in window.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in window.body}, names)
        window.bases = []
        window.decorator_list = []
        self.saved = []
        self.namespace = {
            "save_config": Mock(side_effect=lambda config: self.saved.append(config.copy())),
            "shuffled_tracks": Mock(wraps=core.shuffled_tracks),
        }
        exec(compile(ast.Module(body=[window], type_ignores=[]), str(source), "exec"), self.namespace)
        self.window = self.namespace["MainWindow"]()
        self.window.config = {"fullscreen": False, "favourites": ["a"]}
        self.window.demo = False
        self.window.library_busy = False
        self.window.albums = []
        self.window.player = Mock()
        self.window.keyboard = Mock()
        self.window.message = Mock()
        self.fullscreen = False
        self.events = []
        self.window.showFullScreen = Mock(side_effect=lambda: self.display("full screen", True))
        self.window.showNormal = Mock(side_effect=lambda: self.display("normal", False))
        self.window.showMinimized = Mock(side_effect=lambda: self.events.append("minimized"))
        self.window.isFullScreen = Mock(side_effect=lambda: self.fullscreen)

    def display(self, event, fullscreen):
        self.events.append(event)
        self.fullscreen = fullscreen

    def test_shuffle_plays_the_whole_library_with_matching_track_labels(self):
        first = core.Track("/music/one.flac", "First song", "Artist one", 1, 60)
        second = core.Track("/music/two.flac", "Second song", "Artist two", 1, 90)
        third = core.Track("/music/three.flac", "Third song", "Artist three", 2, 120)
        self.window.albums = [core.Album("a", "First album", "Artist one", tracks=[first]),
                              core.Album("b", "Second album", "Artist two", tracks=[second, third])]
        self.window.page = "Favourites"
        self.window.search = Mock()
        self.window.search.text.return_value = "First song"
        self.window.shuffle_library()
        self.namespace["shuffled_tracks"].assert_called_once_with(self.window.albums)
        paths, labels = self.window.player.play.call_args.args
        expected = {track.path: (track.title, track.artist) for track in (first, second, third)}
        self.assertEqual(len(paths), 3)
        self.assertEqual(dict(zip(paths, labels)), expected)
        self.assertIn("3 tracks", self.window.message.setText.call_args.args[0])

    def test_shuffle_empty_library_does_not_interrupt_playback(self):
        self.window.shuffle_library()
        self.window.player.play.assert_not_called()
        self.window.player.stop.assert_not_called()
        self.assertIn("No stored music", self.window.message.setText.call_args.args[0])

    def test_shuffle_waits_for_library_edits(self):
        self.window.library_busy = True
        self.window.shuffle_library()
        self.namespace["shuffled_tracks"].assert_not_called()
        self.window.player.play.assert_not_called()
        self.assertIn("edit", self.window.message.setText.call_args.args[0])

    def test_fullscreen_toggle_changes_window_and_persists_both_states(self):
        self.window.toggle_fullscreen()
        self.assertTrue(self.fullscreen)
        self.assertTrue(self.window.config["fullscreen"])
        self.window.toggle_fullscreen()
        self.assertFalse(self.fullscreen)
        self.assertFalse(self.window.config["fullscreen"])
        self.assertEqual(self.events, ["full screen", "normal"])
        self.assertEqual([config["fullscreen"] for config in self.saved], [True, False])

    def test_demo_fullscreen_changes_display_without_saving_configuration(self):
        self.window.demo = True
        self.window.set_fullscreen(True)
        self.window.set_fullscreen(False)
        self.assertEqual(self.events, ["full screen", "normal"])
        self.namespace["save_config"].assert_not_called()

    def test_preference_save_failure_leaves_fullscreen_working_and_explains_it(self):
        self.namespace["save_config"].side_effect = OSError("Drive is read-only")
        self.window.set_fullscreen(True)
        self.assertTrue(self.fullscreen)
        self.assertTrue(self.window.config["fullscreen"])
        self.assertIn("could not be saved", self.window.message.setText.call_args.args[0])

    def test_desktop_minimizes_in_order_preserving_preference_and_playback(self):
        self.fullscreen = True
        self.window.config["fullscreen"] = True
        self.window.return_to_desktop()
        self.assertEqual(self.events, ["normal", "minimized"])
        self.window.keyboard.hide.assert_called_once_with()
        self.assertTrue(self.window.config["fullscreen"])
        self.namespace["save_config"].assert_not_called()
        self.window.player.stop.assert_not_called()
        self.assertIn("F11", self.window.message.setText.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

"""Real, offscreen Qt checks for the 1280 x 720 touchscreen interface.

These tests use demo data and never access a CD, music drive, DAC or settings.
Linux CI installs the normal application dependencies. Platforms without Qt or
the application's Linux fcntl dependency skip this optional GUI test module.
"""
import os
import unittest
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import fcntl  # The application is Linux based; do not shim it in the tests.
    from PyQt5.QtCore import QEvent, QPoint, QPointF, QSize, Qt, QTimer
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QDialog, QListWidget, QPushButton
except ImportError as exc:
    GUI_AVAILABLE = False
    GUI_UNAVAILABLE = f"Real GUI tests require PyQt5 and Linux fcntl: {exc}"
else:
    GUI_AVAILABLE = True
    GUI_UNAVAILABLE = ""
    import discroom
    from core import Album, Track, default_config
    from player import SeekSlider


class GuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["discroom-gui-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(discroom.STYLE)

    def process_events(self):
        self.app.processEvents()
        QTest.qWait(10)
        self.app.processEvents()


@unittest.skipUnless(GUI_AVAILABLE, GUI_UNAVAILABLE)
class DiscRoomGuiTests(GuiTestCase):
    def setUp(self):
        # Demo mode already blocks these operations; guards make a regression
        # fail the test instead of accessing the developer's machine.
        for target in ("discroom.save_config", "player.save_config", "discroom.require_storage",
                       "player.require_storage", "discroom.read_disc", "discroom.drive_has_disc",
                       "discroom.request_power", "discroom.run_checked"):
            guard = patch(target, side_effect=AssertionError(f"GUI test attempted {target}"))
            guard.start()
            self.addCleanup(guard.stop)
        self.window = discroom.MainWindow(default_config(), demo=True)
        self.addCleanup(self.close_window)
        self.window.resize(1280, 720)
        self.window.show()
        self.process_events()

    def close_window(self):
        for dialog in self.window.findChildren(QDialog):
            dialog.reject()
        self.window.close()
        self.process_events()
        self.assertFalse(self.window.player.control.isRunning())

    def test_home_fits_1280_by_720_and_uses_large_buttons(self):
        self.assertEqual(self.window.size(), QSize(1280, 720))
        minimum = self.window.minimumSizeHint()
        self.assertLessEqual(minimum.width(), 1280)
        self.assertLessEqual(minimum.height(), 720)
        controls = self.window.centralWidget().findChildren(QPushButton)
        home_controls = [control for control in controls if not self.window.keyboard.isAncestorOf(control)]
        self.assertGreater(len(home_controls), 15)
        for control in home_controls:
            with self.subTest(button=control.text()):
                self.assertTrue(control.property("homeControl"))
                self.assertGreaterEqual(control.minimumHeight(), 72)
        for control in self.window.keyboard.findChildren(QPushButton):
            self.assertFalse(control.property("homeControl"))

    def test_keyboard_keeps_window_and_transport_inside_720_pixels(self):
        original_size = self.window.size()
        self.window.show_keyboard()
        self.process_events()
        self.assertTrue(self.window.keyboard.isVisible())
        self.assertEqual(self.window.size(), original_size)
        self.assertLessEqual(self.window.minimumSizeHint().height(), 720)
        central = self.window.centralWidget()
        for widget in (self.window.keyboard, self.window.player):
            bottom = widget.mapTo(central, widget.rect().bottomRight())
            self.assertLess(bottom.y(), central.height())

    def test_album_rows_show_track_duration_and_real_file_size(self):
        album = Album("gui-album", "Test album", "Test artist", "2026", "Test", tracks=[
            Track("/demo/one.flac", "First song", "First artist", 1, 125, 12_500_000),
            Track("/demo/two.flac", "Second song", "Second artist", 2, 179, 9_876_543),
        ])
        captured = {}
        watchdog = QTimer(self.window)
        watchdog.setSingleShot(True)

        def close_modal():
            for dialog in self.window.findChildren(QDialog):
                dialog.reject()

        def capture_modal():
            dialog = QApplication.activeModalWidget()
            try:
                if not isinstance(dialog, QDialog):
                    raise AssertionError("The album dialog did not become modal")
                tracks = dialog.findChild(QListWidget)
                captured["rows"] = [tracks.item(index).text() for index in range(tracks.count())]
                captured["tooltips"] = [tracks.item(index).toolTip() for index in range(tracks.count())]
            except BaseException as exc:
                # An exception escaping a Qt timer callback can abort Python.
                captured["error"] = exc
            finally:
                close_modal()

        watchdog.timeout.connect(close_modal)
        watchdog.start(2000)
        QTimer.singleShot(0, capture_modal)
        try:
            self.window.open_album(album)
        finally:
            watchdog.stop()
        if "error" in captured:
            raise captured["error"]
        self.assertEqual(len(captured["rows"]), 2)
        self.assertIn("First song", captured["rows"][0])
        self.assertIn("2:05", captured["rows"][0])
        self.assertIn("12.5 MB", captured["rows"][0])
        self.assertIn("2:59", captured["rows"][1])
        self.assertIn("9.9 MB", captured["rows"][1])
        self.assertIn("12,500,000 bytes", captured["tooltips"][0])

    def test_shuffle_queues_all_library_tracks_even_when_search_hides_them(self):
        self.window.search.setText("no matching album")
        expected = {track.path: (track.title, track.artist)
                    for album in self.window.albums for track in album.tracks}
        with patch.object(self.window.player, "play", Mock()) as play:
            self.window.shuffle_library()
        play.assert_called_once()
        sources, names = play.call_args.args
        self.assertCountEqual(sources, expected)
        self.assertEqual(len(sources), len(set(sources)))
        self.assertEqual(dict(zip(sources, names)), expected)
        self.assertIn(f"{len(expected)} tracks", self.window.message.text())

    def test_f11_toggles_fullscreen_with_search_focused(self):
        self.window.search.setFocus()
        QTest.keyClick(self.window.search, Qt.Key_F11)
        self.process_events()
        self.assertTrue(self.window.isFullScreen())
        self.assertEqual(self.window.fullscreen_button.text(), "Windowed")
        QTest.keyClick(self.window.search, Qt.Key_F11)
        self.process_events()
        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(self.window.fullscreen_button.text(), "Full screen")

    def test_desktop_action_minimizes_without_closing_the_app(self):
        self.window.return_to_desktop()
        self.process_events()
        self.assertTrue(self.window.isMinimized())
        self.assertTrue(self.window.player.control.isRunning())
        self.assertIn("Full screen", self.window.message.text())


@unittest.skipUnless(GUI_AVAILABLE, GUI_UNAVAILABLE)
class SeekSliderGuiTests(GuiTestCase):
    def setUp(self):
        self.slider = SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setSingleStep(10)
        self.slider.setPageStep(100)
        self.slider.resize(600, 48)
        self.slider.show()
        self.addCleanup(self.slider.close)
        self.process_events()

    def point(self, fraction):
        return QPoint(round((self.slider.width() - 1) * fraction), self.slider.height() // 2)

    def test_tap_uses_requested_position_and_releases_once(self):
        releases = []
        self.slider.sliderReleased.connect(lambda: releases.append(self.slider.value()))
        QTest.mouseClick(self.slider, Qt.LeftButton, pos=self.point(.8))
        self.assertEqual(len(releases), 1)
        self.assertAlmostEqual(self.slider.value(), 800, delta=30)
        self.assertFalse(self.slider.isSliderDown())
        QTest.mouseClick(self.slider, Qt.LeftButton, pos=self.point(0))
        self.assertEqual(self.slider.value(), 0)
        QTest.mouseClick(self.slider, Qt.LeftButton, pos=self.point(1))
        self.assertEqual(self.slider.value(), 1000)

    def test_drag_previews_movement_and_commits_at_release(self):
        releases = []
        self.slider.sliderReleased.connect(lambda: releases.append(self.slider.value()))
        QTest.mousePress(self.slider, Qt.LeftButton, pos=self.point(.2))
        # Send an actual Qt move event with the held button: offscreen platform
        # plugins do not consistently synthesize held-button QTest.mouseMove.
        move = QMouseEvent(QEvent.MouseMove, QPointF(self.point(.7)), Qt.NoButton,
                           Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(self.slider, move)
        self.assertTrue(self.slider.isSliderDown())
        self.assertAlmostEqual(self.slider.value(), 700, delta=30)
        self.assertEqual(releases, [])
        QTest.mouseRelease(self.slider, Qt.LeftButton, pos=self.point(.9))
        self.assertEqual(len(releases), 1)
        self.assertAlmostEqual(releases[0], 900, delta=30)
        self.assertFalse(self.slider.isSliderDown())

    def test_home_end_arrows_and_page_keys_keep_working(self):
        self.slider.setValue(500)
        QTest.keyClick(self.slider, Qt.Key_Home)
        self.assertEqual(self.slider.value(), 0)
        QTest.keyClick(self.slider, Qt.Key_End)
        self.assertEqual(self.slider.value(), 1000)
        QTest.keyClick(self.slider, Qt.Key_Left)
        self.assertEqual(self.slider.value(), 990)
        QTest.keyClick(self.slider, Qt.Key_PageDown)
        self.assertEqual(self.slider.value(), 890)


if __name__ == "__main__":
    unittest.main()

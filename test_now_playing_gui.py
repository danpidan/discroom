"""Real Qt regressions for optional artwork view and playback navigation."""
import unittest
from unittest.mock import Mock, patch

import test_gui as qt

if qt.GUI_AVAILABLE:
    from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QProcess
    from PyQt5.QtGui import QColor, QImage, QPainter
    from core import Disc, blank_metadata


@unittest.skipUnless(qt.GUI_AVAILABLE, qt.GUI_UNAVAILABLE)
class NowPlayingGuiTests(qt.GuiTestCase):
    def setUp(self):
        for target in ("discroom.save_config", "player.save_config", "discroom.require_storage",
                       "player.require_storage", "discroom.read_disc", "discroom.drive_has_disc",
                       "discroom.request_power", "discroom.run_checked", "discroom.fetch_cover"):
            guard = patch(target, side_effect=AssertionError(f"GUI test attempted {target}"))
            guard.start()
            self.addCleanup(guard.stop)
        self.window = qt.discroom.MainWindow(qt.default_config(), demo=True)
        self.addCleanup(self.close_window)
        self.window.timer.stop()
        self.window.storage_timer.stop()
        self.player = self.window.player
        self.screen = self.window.now_playing
        self.window.resize(1280, 720)
        self.window.show()
        self.process_events()

    def close_window(self):
        self.window.demo = True
        self.window.close()
        self.process_events()
        self.assertFalse(self.window.player.control.isRunning())

    @staticmethod
    def cover_bytes(colour, wide=False):
        image = QImage(300 if wide else 80, 100 if wide else 80, QImage.Format_RGB32)
        image.fill(QColor(colour))
        if wide:
            painter = QPainter(image)
            painter.fillRect(30, 0, 240, 100, QColor("white"))
            painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def albums(self):
        first = qt.Album("one", "First album", "First artist", "2025", "Classic Rock",
                         self.cover_bytes("red"), tracks=[
                             qt.Track("/music/first.flac", "First song", "First artist", 1, 120)])
        second = qt.Album("two", "Second album", "Second artist", "2026", "Jazz",
                          self.cover_bytes("blue"), tracks=[
                              qt.Track("/music/second.flac", "Second song", "Guest artist", 1, 180)])
        self.window.albums = [first, second]
        self.window.update_filters()
        self.window.render_albums()
        return first, second

    def play_albums(self, albums, start=0):
        tracks = [track for album in albums for track in album.tracks]
        self.player.play([track.path for track in tracks],
                         [(track.title, track.artist) for track in tracks], start_index=start)

    def click(self, widget):
        qt.QTest.mouseClick(widget, qt.Qt.LeftButton)
        self.process_events()

    def test_view_is_optional_and_return_preserves_playback_and_library_filters(self):
        albums = self.albums()
        self.window.navigate("Favourites")
        self.window.search.setText("first")
        self.window.filters["Genre"].setCurrentText("Classic Rock")
        self.play_albums(albums)
        library = self.window.screens.currentWidget()
        process = self.player.process
        generation = self.player.generation
        self.assertIsNot(library, self.screen)
        self.window.show_keyboard()
        self.click(self.window.now_playing_button)
        self.assertIs(self.window.screens.currentWidget(), self.screen)
        self.assertFalse(self.window.keyboard.isVisible())
        self.assertIs(self.window.player, self.player)
        self.assertIs(self.player.process, process)
        self.assertTrue(self.player.isVisible())
        self.assertTrue(self.window.now_playing_button.isChecked())
        self.click(self.screen.back_button)
        self.assertIs(self.window.screens.currentWidget(), library)
        self.assertEqual(self.window.page, "Favourites")
        self.assertEqual(self.window.search.text(), "first")
        self.assertEqual(self.window.filters["Genre"].currentText(), "Classic Rock")
        self.assertFalse(self.window.now_playing_button.isChecked())
        self.assertEqual(self.player.generation, generation)
        self.assertEqual(self.player.playback_state, "playing")

    def test_selected_track_and_cross_album_skips_show_art_despite_filters(self):
        first, second = self.albums()
        self.window.search.setText("not in this library")
        self.window.filters["Genre"].setCurrentText("Classic Rock")
        self.play_albums([first, second], start=1)
        self.assertEqual(self.screen.title.text(), "Second song")
        self.assertEqual(self.screen.artist.text(), "Guest artist")
        self.assertEqual(self.screen.album.text(), "Second album")
        self.assertEqual(self.screen.cover.pixmap.toImage().pixelColor(0, 0), QColor("blue"))
        self.player.skip(-1)
        self.assertEqual(self.screen.title.text(), "First song")
        self.assertEqual(self.screen.cover.pixmap.toImage().pixelColor(0, 0), QColor("red"))
        self.player.skip(1)
        self.assertEqual(self.screen.album.text(), "Second album")
        self.assertIn("Track 2 of 2", self.screen.info.text())

    def test_pause_finish_and_stop_update_screen_without_stale_artwork(self):
        self.play_albums(self.albums())
        self.assertEqual(self.screen.state.text(), "PLAYING")
        self.player.toggle()
        self.assertEqual(self.screen.state.text(), "PAUSED")
        self.player.toggle()
        self.assertEqual(self.screen.state.text(), "PLAYING")
        with patch.object(self.player, "read_error"):
            self.player.finished(0, QProcess.NormalExit)
        self.assertEqual(self.screen.state.text(), "PLAYBACK FINISHED")
        self.assertEqual(self.screen.title.text(), "First song")
        self.player.stop()
        self.assertEqual(self.screen.state.text(), "NOTHING PLAYING")
        self.assertEqual(self.screen.title.text(), "Choose something to play")
        self.assertEqual(self.screen.album.text(), "")
        self.assertTrue(self.screen.cover.pixmap.isNull())

    def test_rescan_updates_current_metadata_and_cover_by_file_path(self):
        first, second = self.albums()
        self.play_albums([first, second])
        edited = qt.Album("renamed", "Renamed album", "New album artist", cover=self.cover_bytes("green"),
                          tracks=[qt.Track(first.tracks[0].path, "Renamed song", "New singer", 1, 120)])
        self.window.albums = [edited, second]
        self.window.update_filters()
        self.assertEqual(self.screen.title.text(), "Renamed song")
        self.assertEqual(self.screen.artist.text(), "New singer")
        self.assertEqual(self.screen.album.text(), "Renamed album")
        self.assertEqual(self.screen.cover.pixmap.toImage().pixelColor(0, 0), QColor("green"))

    def test_missing_or_invalid_artwork_replaces_previous_cover_with_placeholder(self):
        first, second = self.albums()
        self.play_albums([first])
        self.assertFalse(self.screen.cover.pixmap.isNull())
        for cover in (b"", b"not an image"):
            with self.subTest(cover=cover):
                second.cover = cover
                self.play_albums([second])
                self.assertTrue(self.screen.cover.pixmap.isNull())
                self.assertIn("No cover available", self.screen.cover.accessibleName())
                self.assertEqual(self.screen.album.text(), "Second album")

    def test_full_artwork_view_keeps_both_edges_of_non_square_cover(self):
        cover = qt.discroom.Cover(qt.Album("wide", "Wide image", "Artist",
                                           cover=self.cover_bytes("green", wide=True)), full_cover=True)
        self.addCleanup(cover.close)
        cover.setFixedSize(300, 300)
        cover.show()
        self.process_events()
        rendered = cover.grab().toImage()
        # Cropping the 3:1 image into a square loses these green outer edges.
        self.assertEqual(rendered.pixelColor(10, 150), QColor("green"))
        self.assertEqual(rendered.pixelColor(290, 150), QColor("green"))
        self.assertEqual(rendered.pixelColor(150, 150), QColor("white"))

    def test_long_metadata_does_not_push_controls_off_1280_by_720_screen(self):
        album = qt.Album("long", "An album with a much longer title than the usual collection entry",
                         "The ensemble with a long artist name and several special guests", "2026", cover=self.cover_bytes("red"),
                         tracks=[qt.Track("/music/long.flac", "A rather long song title with several words and an extended live performance description",
                                          "The ensemble with a long artist name and several special guests", 1, 120)])
        self.window.albums = [album]
        self.window.update_filters()
        self.play_albums([album])
        self.click(self.window.now_playing_button)
        self.assertEqual(self.window.size(), qt.QSize(1280, 720))
        self.assertLessEqual(self.window.minimumSizeHint().width(), 1280)
        self.assertLessEqual(self.window.minimumSizeHint().height(), 720)
        central = self.window.centralWidget()
        for widget in (self.screen.cover, self.screen.title, self.screen.artist, self.screen.album,
                       self.screen.back_button, self.player):
            with self.subTest(widget=widget.objectName() or type(widget).__name__):
                top = widget.mapTo(central, widget.rect().topLeft())
                bottom = widget.mapTo(central, widget.rect().bottomRight())
                self.assertGreaterEqual(top.x(), 0)
                self.assertGreaterEqual(top.y(), 0)
                self.assertLess(bottom.x(), central.width())
                self.assertLess(bottom.y(), central.height())

    def test_cd_selected_track_uses_cd_art_and_metadata_for_all_chapters(self):
        self.window.disc = Disc([1, 2, 3], [9000] * 3, [0, 9000, 18000])
        self.window.metadata = blank_metadata(self.window.disc)
        self.window.metadata.update(album="The CD album", artist="The CD artist")
        self.window.metadata["tracks"][2] = {"title": "Third CD song", "artist": "Guest CD singer"}
        self.window.load_cd_cover()
        self.window.cd_cover = self.cover_bytes("blue")
        self.window.play_cd(2)
        self.assertEqual(self.player.sources, ["cdda://"])
        self.assertEqual(self.player.position, 2)
        self.assertEqual(self.screen.title.text(), "Third CD song")
        self.assertEqual(self.screen.album.text(), "The CD album")
        self.assertEqual(self.screen.cover.pixmap.toImage().pixelColor(0, 0), QColor("blue"))
        self.assertIn("Audio CD · Track 3 of 3", self.screen.info.text())

    def test_late_cd_cover_results_cannot_replace_another_release_or_disc(self):
        requests = []
        self.window.demo = False
        self.window.work = Mock(side_effect=lambda operation, done, failed=None: requests.append((done, failed)))
        first_disc = self.window.disc
        self.window.metadata["release_id"] = "11111111-1111-1111-1111-111111111111"
        self.window.load_cd_cover()
        first_done = requests[-1][0]
        self.window.metadata["release_id"] = "22222222-2222-2222-2222-222222222222"
        self.window.load_cd_cover()
        second_done = requests[-1][0]
        first_done(self.cover_bytes("red"))
        self.assertEqual(self.window.cd_cover, b"")
        self.window.play_cd(1)
        second_done(self.cover_bytes("blue"))
        self.assertEqual(self.screen.cover.pixmap.toImage().pixelColor(0, 0), QColor("blue"))
        playing_album = self.window.playing_cd_album
        self.window.disc = Disc([1, 2], [8000, 8000], [0, 8000])
        self.window.metadata = blank_metadata(self.window.disc)
        self.window.metadata.update(album="Different CD", release_id="33333333-3333-3333-3333-333333333333")
        self.window.load_cd_cover()
        third_done = requests[-1][0]
        second_done(self.cover_bytes("red"))
        third_done(self.cover_bytes("green"))
        self.assertIs(self.window.playing_cd_disc, first_disc)
        self.assertIs(self.window.playing_cd_album, playing_album)
        self.assertEqual(self.screen.cover.pixmap.toImage().pixelColor(0, 0), QColor("blue"))


if __name__ == "__main__":
    unittest.main()

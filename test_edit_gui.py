"""Offscreen Qt regressions for album and track editing on the touchscreen."""
import unittest
from unittest.mock import Mock, patch

import test_gui as qt


@unittest.skipUnless(qt.GUI_AVAILABLE, qt.GUI_UNAVAILABLE)
class EditGuiTests(qt.GuiTestCase):
    def setUp(self):
        self.window = qt.discroom.MainWindow(qt.default_config(), demo=True)
        self.addCleanup(self.close_window)
        self.window.show()
        self.process_events()
        self.album = qt.Album("edit-test", "FirstLight", "Original Artist", "2025", "HardRock", tracks=[
            qt.Track("/demo/first.flac", "FirstSong", "Original Artist", 1, 120, 18_000_000),
            qt.Track("/demo/second.flac", "SecondSong", "Guest Artist", 2, 180, 21_000_000),
        ])

    def close_window(self):
        for dialog in self.window.findChildren(qt.QDialog):
            dialog.reject()
        self.window.close()
        self.process_events()
        self.assertFalse(self.window.player.control.isRunning())

    def button(self, widget, text):
        return next(button for button in widget.findChildren(qt.QPushButton) if button.text() == text)

    def click(self, widget):
        qt.QTest.mouseClick(widget, qt.Qt.LeftButton)
        self.process_events()

    def reopen_and_type_space(self, dialog, keyboard, field, position):
        field.setFocus()
        self.process_events()
        self.assertTrue(keyboard.isVisible())
        self.click(self.button(dialog, "Hide keyboard"))
        self.assertFalse(keyboard.isVisible())
        self.assertIs(qt.QApplication.focusWidget(), field)
        self.click(field)
        self.assertTrue(keyboard.isVisible())
        field.setCursorPosition(position)
        self.click(self.button(dialog, "Space"))

    def drive_modal(self, launch, interact):
        seen = []
        errors = []
        watchdog = qt.QTimer(self.window)
        watchdog.setSingleShot(True)

        def dismiss():
            for dialog in self.window.findChildren(qt.QDialog):
                if dialog.isVisible():
                    dialog.reject()

        def interact_with_modal():
            dialog = qt.QApplication.activeModalWidget()
            try:
                self.assertIsInstance(dialog, qt.QDialog)
                seen.append(dialog)
                interact(dialog)
            except BaseException as exc:
                # Do not let a test failure escape from a Qt timer callback.
                errors.append(exc)
            finally:
                dismiss()

        watchdog.timeout.connect(dismiss)
        watchdog.start(2000)
        qt.QTimer.singleShot(0, interact_with_modal)
        try:
            launch()
        finally:
            watchdog.stop()
        if errors:
            raise errors[0]
        self.assertEqual(len(seen), 1)

    def submit_edit(self, launch, interact):
        with patch.object(self.window, "manage", Mock(return_value=True)) as manage, \
                patch.object(qt.discroom, "edit_tags") as edit_tags:
            self.drive_modal(launch, interact)
            manage.assert_called_once()
            manage.call_args.args[0]({})
            edit_tags.assert_called_once()
            return edit_tags.call_args.args[1]

    def test_album_keyboard_reopens_and_keeps_spaces_in_saved_metadata(self):
        parent = qt.QDialog(self.window)

        def edit(dialog):
            title = dialog.inputs["album"]
            self.reopen_and_type_space(dialog, dialog.keyboard, title, 5)
            self.assertEqual(title.text(), "First Light")

            genre = dialog.inputs["genre"]
            genre.setFocus()
            genre.setCursorPosition(4)
            qt.QTest.keyClick(genre, qt.Qt.Key_Space)
            self.assertEqual(genre.text(), "Hard Rock")

            dialog.table.editItem(dialog.table.item(0, 1))
            self.process_events()
            editor = dialog.table.viewport().findChild(qt.discroom.QLineEdit)
            self.assertIsNotNone(editor)
            self.reopen_and_type_space(dialog, dialog.keyboard, editor, 5)
            self.assertEqual(editor.text(), "First Song")
            self.click(self.button(dialog, "Save details"))

        edits = self.submit_edit(lambda: self.window.edit_album(self.album, parent), edit)
        tags = dict(edits)
        self.assertEqual(tags["/demo/first.flac"]["album"], "First Light")
        self.assertEqual(tags["/demo/first.flac"]["title"], "First Song")
        self.assertEqual(tags["/demo/first.flac"]["genre"], "Hard Rock")
        self.assertEqual(tags["/demo/second.flac"]["album"], "First Light")
        self.assertEqual(tags["/demo/second.flac"]["artist"], "Guest Artist")

    def test_track_keyboard_reopens_and_return_saves_multi_word_text(self):
        parent = qt.QDialog(self.window)

        def edit(dialog):
            title, artist = dialog.findChildren(qt.discroom.QLineEdit)
            keyboard = dialog.findChild(qt.discroom.Keyboard)
            self.reopen_and_type_space(dialog, keyboard, title, 5)
            self.assertEqual(title.text(), "First Song")

            self.click(self.button(dialog, "Hide keyboard"))
            self.assertFalse(keyboard.isVisible())
            self.click(artist)
            self.assertTrue(keyboard.isVisible())
            artist.setText("NewArtist")
            artist.setCursorPosition(3)
            qt.QTest.keyClick(artist, qt.Qt.Key_Space)
            self.assertEqual(artist.text(), "New Artist")
            qt.QTest.keyClick(artist, qt.Qt.Key_Return)
            self.process_events()
            self.assertEqual(dialog.result(), qt.QDialog.Accepted)

        edits = self.submit_edit(lambda: self.window.edit_track(self.album, 0, parent), edit)
        self.assertEqual(edits, [("/demo/first.flac", {"title": "First Song", "artist": "New Artist"})])

    def test_space_on_idle_title_opens_editor_without_replacing_the_title(self):
        parent = qt.QDialog(self.window)

        def edit(dialog):
            table = dialog.table
            # Recreate keyboard navigation to a selected cell with no editor.
            table.setEditTriggers(table.NoEditTriggers)
            table.setCurrentCell(0, 1)
            table.setEditTriggers(table.AllEditTriggers)
            table.setFocus()
            self.process_events()
            self.assertIsNone(table.viewport().findChild(qt.discroom.QLineEdit))

            qt.QTest.keyClick(table, qt.Qt.Key_Space)
            self.process_events()
            editor = table.viewport().findChild(qt.discroom.QLineEdit)
            self.assertIsNotNone(editor)
            self.assertEqual(editor.text(), "FirstSong")

            editor.setCursorPosition(5)
            qt.QTest.keyClick(editor, qt.Qt.Key_Space)
            self.assertEqual(editor.text(), "First Song")
            self.click(self.button(dialog, "Save details"))

        edits = self.submit_edit(lambda: self.window.edit_album(self.album, parent), edit)
        self.assertEqual(dict(edits)["/demo/first.flac"]["title"], "First Song")

    def test_validation_retry_preserves_original_artist_and_unmodified_tracks(self):
        disc = qt.discroom.Disc([1, 2], [9000, 13500], [0, 9000])
        metadata = {
            "album": "First Light", "artist": "Original Artist", "year": "2025", "genre": "Hard Rock",
            "tracks": [{"title": "First Song", "artist": "Original Artist"},
                       {"title": "Second Song", "artist": "Guest Artist"}],
        }
        dialog = qt.discroom.MetadataDialog(disc, metadata, [], self.window, editing=True)
        dialog.show()
        self.process_events()
        dialog.inputs["artist"].setText("New Artist")
        dialog.inputs["year"].setText("invalid")

        with patch.object(qt.discroom.QMessageBox, "warning") as warning:
            dialog.confirm()
            warning.assert_called_once()
            self.assertEqual(dialog.metadata, metadata)
            dialog.inputs["year"].setText("2026")
            dialog.table.item(1, 1).setText("")
            dialog.confirm()
            self.assertEqual(warning.call_count, 2)
            self.assertEqual(dialog.metadata, metadata)

        dialog.table.item(1, 1).setText("Second Song")
        dialog.confirm()
        self.assertEqual(dialog.result(), qt.QDialog.Accepted)
        self.assertEqual(dialog.metadata["artist"], "New Artist")
        self.assertEqual([track["artist"] for track in dialog.metadata["tracks"]], ["New Artist", "Guest Artist"])


if __name__ == "__main__":
    unittest.main()

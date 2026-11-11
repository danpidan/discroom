"""Check Genre grouping and selection in the real Qt library view."""
import unittest

import test_gui

if test_gui.GUI_AVAILABLE:
    import discroom
    from core import Album, default_config


@unittest.skipUnless(test_gui.GUI_AVAILABLE, test_gui.GUI_UNAVAILABLE)
class GenreGuiTests(test_gui.GuiTestCase):
    def setUp(self):
        self.window = discroom.MainWindow(default_config(), demo=True)
        self.addCleanup(self.window.close)
        self.genre = self.window.filters["Genre"]

    def set_albums(self, albums):
        self.window.albums = albums
        self.window.update_filters()
        self.window.render_albums()

    def album_titles(self):
        titles = []
        for index in range(self.window.grid.count()):
            tile = self.window.grid.itemAt(index).widget()
            cover = tile.findChild(discroom.Cover)
            if cover:
                titles.append(cover.album.title)
        return titles

    def test_genres_with_spaces_group_without_joining_words_or_changing_tags(self):
        albums = [Album("a", "One", "Artist A", "2025", "Classic Rock"),
                  Album("b", "Two", "Artist B", "2024", "  classic  ROCK\t"),
                  Album("c", "Three", "Artist A", "2024", "CLASSIC\tROCK"),
                  Album("d", "Four", "Artist A", "2025", "ClassicRock"),
                  Album("e", "Five", "Artist B", "2025", " \t "),
                  Album("f", "Six", "Artist A", "2025", "")]
        self.set_albums(albums)
        self.assertEqual([self.genre.itemText(i) for i in range(self.genre.count())],
                         ["All genres", "Classic Rock", "ClassicRock"])
        self.genre.setCurrentIndex(self.genre.findData("classic rock"))
        self.assertCountEqual(self.album_titles(), ["One", "Two", "Three"])
        self.window.filters["Artist"].setCurrentText("Artist A")
        self.assertCountEqual(self.album_titles(), ["One", "Three"])
        self.window.filters["Year"].setCurrentText("2025")
        self.assertEqual(self.album_titles(), ["One"])
        self.assertEqual(albums[1].genre, "  classic  ROCK\t")

    def test_rescan_keeps_selection_when_genre_spelling_changes_and_clears_removed_genre(self):
        self.set_albums([Album("a", "One", "Artist", genre="Classic Rock"),
                         Album("b", "Two", "Artist", genre="Jazz")])
        self.genre.setCurrentIndex(self.genre.findData("classic rock"))
        self.set_albums([Album("b", "Two", "Artist", genre="Jazz"),
                         Album("c", "Three", "Artist", genre="CLASSIC  ROCK")])
        self.assertEqual(self.genre.currentData(), "classic rock")
        self.assertEqual(self.genre.currentText(), "CLASSIC ROCK")
        self.assertEqual(self.album_titles(), ["Three"])
        self.set_albums([Album("b", "Two", "Artist", genre="Jazz")])
        self.assertEqual(self.genre.currentText(), "All genres")
        self.assertEqual(self.album_titles(), ["Two"])


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Importing this test helper supports the offline cloud runner as well as the Pi.
import test_core
import library_management as management


class Tags(dict):
    def __init__(self, path):
        self.path = Path(path)
        super().__init__(json.loads(self.path.read_text()))

    def save(self):
        self.path.write_text(json.dumps(self))


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.mount = Path(self.temporary.name)
        self.library = self.mount / "DiscRoom/Music"
        self.folder = self.library / "Artist/Album/Disc 1"
        self.folder.mkdir(parents=True)
        self.first, self.second = self.folder / "01.flac", self.folder / "02.flac"
        self.first.write_text(json.dumps({"title": "First", "album": "Album", "artist": "Artist"}))
        self.second.write_text(json.dumps({"title": "Second", "album": "Album", "artist": "Guest"}))
        self.cover = self.folder / "cover.jpg"
        self.cover.write_bytes(b"original cover")
        self.config = {"mount": str(self.mount), "library": str(self.library)}
        self.mounted = patch("core.os.path.ismount", lambda path: Path(path) == self.mount)
        self.mounted.start()

    def tearDown(self):
        self.mounted.stop()
        self.temporary.cleanup()

    def test_single_track_delete_and_restore_preserves_other_track_and_cover(self):
        original = self.first.read_bytes()
        management.move_to_trash(self.config, [str(self.first)], "First")
        self.assertFalse(self.first.exists())
        self.assertTrue(self.second.is_file())
        self.assertTrue(self.cover.is_file())
        entries = management.list_trash(self.config)
        self.assertEqual(entries[0]["count"], 1)
        management.restore_trash(self.config, entries[0]["entry"])
        self.assertEqual(self.first.read_bytes(), original)
        self.assertEqual(management.list_trash(self.config), [])

    def test_album_delete_removes_folder_and_restores_art_and_tracks(self):
        management.move_to_trash(self.config, [str(self.first), str(self.second)], "Album", include_cover=True)
        self.assertFalse(self.folder.exists())
        entries = management.list_trash(self.config)
        self.assertEqual(entries[0]["count"], 2)
        management.restore_trash(self.config, entries[0]["entry"])
        self.assertTrue(self.first.is_file())
        self.assertTrue(self.second.is_file())
        self.assertEqual(self.cover.read_bytes(), b"original cover")

    def test_restoring_over_new_music_is_rejected(self):
        management.move_to_trash(self.config, [str(self.first)], "First")
        self.first.write_text("new track")
        entry = management.list_trash(self.config)[0]
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            management.restore_trash(self.config, entry["entry"])
        self.assertEqual(self.first.read_text(), "new track")
        self.assertTrue(Path(entry["entry"]).exists())

    def test_permanent_delete_only_removes_selected_trash_entry(self):
        management.move_to_trash(self.config, [str(self.first)], "First")
        entry = management.list_trash(self.config)[0]
        management.delete_trash_forever(self.config, entry["entry"])
        self.assertFalse(Path(entry["entry"]).exists())
        self.assertTrue(self.second.is_file())
        self.assertTrue(self.cover.is_file())

    def test_outside_library_track_is_never_deleted(self):
        outside = self.mount / "personal.flac"
        outside.write_text("keep")
        with self.assertRaises(ValueError):
            management.move_to_trash(self.config, [str(outside)], "Outside")
        self.assertEqual(outside.read_text(), "keep")

    def test_symlink_track_is_never_deleted(self):
        link = self.folder / "link.flac"
        link.symlink_to(self.first)
        with self.assertRaisesRegex(RuntimeError, "Only music"):
            management.move_to_trash(self.config, [str(link)], "Link")
        self.assertTrue(self.first.is_file())

    def test_disconnected_storage_prevents_delete(self):
        with patch("core.os.path.ismount", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not mounted"):
                management.move_to_trash(self.config, [str(self.first)], "First")
        self.assertTrue(self.first.is_file())

    def test_failed_album_move_rolls_back_first_track(self):
        rename = management.os.rename
        def failure(source, target):
            if Path(source) == self.second:
                raise OSError("simulated failure")
            return rename(source, target)
        with patch("library_management.os.rename", side_effect=failure):
            with self.assertRaises(OSError):
                management.move_to_trash(self.config, [str(self.first), str(self.second)], "Album")
        self.assertTrue(self.first.is_file())
        self.assertTrue(self.second.is_file())
        self.assertEqual(management.list_trash(self.config), [])

    def test_metadata_edits_preserve_other_tags_and_cover(self):
        with patch("library_management.FLAC", Tags):
            management.edit_tags(self.config, [(str(self.first), {"title": "Corrected title"})])
        tags = json.loads(self.first.read_text())
        self.assertEqual(tags["title"], "Corrected title")
        self.assertEqual(tags["album"], "Album")
        self.assertEqual(json.loads(self.second.read_text())["title"], "Second")
        self.assertEqual(self.cover.read_bytes(), b"original cover")

    def test_metadata_commit_failure_restores_original_tracks(self):
        original = [self.first.read_bytes(), self.second.read_bytes()]
        replace = management.os.replace
        def failure(source, target):
            if Path(source).name == "1.edited.flac":
                raise OSError("simulated tag commit failure")
            return replace(source, target)
        with patch("library_management.FLAC", Tags), patch("library_management.os.replace", side_effect=failure):
            with self.assertRaises(OSError):
                management.edit_tags(self.config, [(str(self.first), {"title": "New first"}),
                                                   (str(self.second), {"title": "New second"})])
        self.assertEqual(self.first.read_bytes(), original[0])
        self.assertEqual(self.second.read_bytes(), original[1])

    def test_invalid_trash_manifest_cannot_restore_outside_library(self):
        management.move_to_trash(self.config, [str(self.first)], "First")
        entry = Path(management.list_trash(self.config)[0]["entry"])
        manifest = json.loads((entry / "manifest.json").read_text())
        manifest["files"][0]["original"] = "../../personal.flac"
        (entry / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            management.restore_trash(self.config, entry)
        self.assertFalse((self.mount / "personal.flac").exists())


if __name__ == "__main__":
    unittest.main()

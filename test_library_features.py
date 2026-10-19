"""Library queues and file sizes work without a CD drive or audio backend."""
from pathlib import Path
import random
import tempfile
import types
import unittest
from unittest.mock import patch

# Use the shared offline import shims before loading the production module.
import test_core
import core


class FakeAudio(dict):
    pictures = []
    info = types.SimpleNamespace(length=123.5)

    def __init__(self, path):
        super().__init__(album=["Album"], albumartist=["Artist"],
                         title=[Path(path).stem], tracknumber=[Path(path).stem[:2]])


class LibraryShuffleTests(unittest.TestCase):
    def track(self, name):
        return core.Track(f"/music/{name}.flac", name, "Artist", 1, 123)

    def test_queue_includes_every_album_track_once_and_keeps_metadata(self):
        tracks = [self.track(str(index)) for index in range(12)]
        albums = [core.Album("a", "First album", "Artist", tracks=tracks[:5]),
                  core.Album("b", "Second album", "Artist", tracks=tracks[5:])]
        shuffled = core.shuffled_tracks(albums, random.Random(7))
        self.assertEqual(sorted(track.path for track in shuffled), sorted(track.path for track in tracks))
        self.assertNotEqual(shuffled, tracks)
        self.assertTrue(all(any(track is original for original in tracks) for track in shuffled))
        self.assertEqual(albums[0].tracks, tracks[:5])
        self.assertEqual(albums[1].tracks, tracks[5:])

    def test_duplicate_file_is_queued_once_even_when_referenced_in_two_albums(self):
        track = self.track("Shared")
        albums = [core.Album("a", "First", "Artist", tracks=[track]),
                  core.Album("b", "Second", "Artist", tracks=[track, self.track("Other")])]
        shuffled = core.shuffled_tracks(albums)
        self.assertEqual(len(shuffled), 2)
        self.assertEqual({item.title for item in shuffled}, {"Shared", "Other"})

    def test_empty_library_and_single_track_are_valid_queues(self):
        self.assertEqual(core.shuffled_tracks([]), [])
        track = self.track("Only")
        self.assertEqual(core.shuffled_tracks([core.Album("a", "One", "Artist", tracks=[track])]), [track])


class LibraryFileSizeTests(unittest.TestCase):
    def test_scan_records_real_sizes_while_retaining_track_order(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory) / "Artist" / "Album"
            album.mkdir(parents=True)
            (album / "02 Second.flac").write_bytes(b"x" * 2345)
            (album / "01 First.flac").write_bytes(b"y" * 1234)
            with patch("core.FLAC", FakeAudio):
                albums, skipped = core.scan_library(directory)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(albums), 1)
        self.assertEqual([(track.number, track.size_bytes) for track in albums[0].tracks],
                         [(1, 1234), (2, 2345)])

    def test_track_removed_during_scan_is_skipped_without_leaving_an_empty_album(self):
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / "01 Removed.flac"
            track.write_bytes(b"flac")

            def removed_after_metadata_read(path):
                result = FakeAudio(path)
                Path(path).unlink()
                return result

            with patch("core.FLAC", side_effect=removed_after_metadata_read):
                albums, skipped = core.scan_library(directory)
        self.assertEqual(albums, [])
        self.assertEqual(skipped, 1)

    def test_legacy_track_constructor_and_decimal_storage_labels(self):
        track = core.Track("/music/old.flac", "Old", "Artist", 1, 60)
        self.assertEqual(track.size_bytes, 0)
        for size, expected in [(0, "0 B"), (999, "999 B"), (1500, "1.5 KB"),
                               (23_400_000, "23.4 MB"), (1_000_000_000, "1.0 GB"),
                               (1_000_000_000_000, "1.0 TB")]:
            with self.subTest(size=size):
                self.assertEqual(core.format_file_size(size), expected)

    def test_unreadable_duration_does_not_leave_an_empty_album_or_hide_valid_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "01 Broken.flac").write_bytes(b"broken")
            (Path(directory) / "02 Valid.flac").write_bytes(b"valid")

            def metadata(path):
                result = FakeAudio(path)
                if Path(path).name.startswith("01"):
                    result["album"] = ["Broken album"]
                    result.info = None
                return result

            with patch("core.FLAC", side_effect=metadata):
                albums, skipped = core.scan_library(directory)
        self.assertEqual(skipped, 1)
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0].title, "Album")
        self.assertEqual([track.title for track in albums[0].tracks], ["02 Valid"])


if __name__ == "__main__":
    unittest.main()

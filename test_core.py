"""Storage/drive orchestration tests. Physical drive and encoder are replaced."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

# The cloud test runner has no GUI/network package installation permission.
# These shims only allow importing core: every network/FLAC operation used in
# these tests is replaced explicitly below. On the Pi, use installed packages.
if importlib.util.find_spec("requests") is None:
    sys.modules["requests"] = types.SimpleNamespace(RequestException=RuntimeError)
if importlib.util.find_spec("mutagen") is None:
    package = types.ModuleType("mutagen")
    package.__path__ = []
    module = types.ModuleType("mutagen.flac")
    module.FLAC = lambda path: None
    sys.modules["mutagen"] = package
    sys.modules["mutagen.flac"] = module

import core


TOC = """
Table of contents (audio tracks only):
track       length               begin        copy pre ch
 1.     12006 [02:40.06]        0 [00:00.00]    no  no  2
 2.     11565 [02:34.15]    12006 [02:40.06]    no  no  2
 3.     22377 [04:58.27]    23571 [05:14.21]    no  no  2
"""


class Metadata(dict):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)

    def save(self):
        self.path.write_text(json.dumps(self))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mount = Path(self.temp.name)
        self.config = {"mount": str(self.mount), "library": str(self.mount / "DiscRoom/Music"), "device": "/dev/test-cd"}
        self.disc = core.Disc([1, 2], [750, 750], [0, 750])
        self.metadata = core.blank_metadata(self.disc)
        self.metadata.update(artist="An Artist", album="An Album", year="2026", genre="Jazz")
        self.metadata["tracks"] = [{"title": "First track", "artist": "An Artist"},
                                   {"title": "Second track", "artist": "Guest Artist"}]
        self.cancel = threading.Event()
        self.events = []
        self.mount_patch = patch("core.os.path.ismount", lambda path: Path(path) == self.mount)
        self.mount_patch.start()

    def tearDown(self):
        self.mount_patch.stop()
        self.temp.cleanup()

    @property
    def destination(self):
        return Path(self.config["library"]) / "An Artist/An Album/Disc 1"

    def fake_command(self, args, cancel=None, timeout=900):
        self.events.append(args[0])
        if args[0] == "cdparanoia":
            Path(args[-1]).write_bytes(b"test WAV")
        elif args[0] == "flac":
            Path(args[args.index("-o") + 1]).write_bytes(b"test FLAC")
        elif args[0] == "eject":
            self.assertTrue(self.destination.is_dir())
            self.assertIn("sync", self.events)
        return "Done"

    def rip(self, command=None, progress=None, cover=None):
        with patch("core.read_disc", return_value=self.disc), \
             patch("core.run_checked", side_effect=command or self.fake_command), \
             patch("core.FLAC", Metadata), \
             patch("core.fetch_cover", side_effect=cover or (lambda release: b"")), \
             patch("core.sync_filesystem", side_effect=lambda path: self.events.append("sync")):
            return core.rip_album(self.config, self.disc, self.metadata,
                                  progress or (lambda value, message: None), self.cancel)

    def test_success_tags_two_tracks_and_flushes_before_eject(self):
        self.assertIn("CD ejected", self.rip())
        tracks = sorted(self.destination.glob("*.flac"))
        self.assertEqual(len(tracks), 2)
        first = json.loads(tracks[0].read_text())
        second = json.loads(tracks[1].read_text())
        self.assertEqual(first["album"], "An Album")
        self.assertEqual(first["title"], "First track")
        self.assertEqual(second["artist"], "Guest Artist")
        self.assertEqual(second["tracknumber"], "2")
        self.assertLess(self.events.index("sync"), self.events.index("eject"))
        self.assertFalse(list((Path(self.config["library"]) / ".copying").iterdir()))

    def test_disconnect_before_start_creates_no_folder(self):
        with patch("core.os.path.ismount", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not mounted"):
                self.rip()
        self.assertFalse(Path(self.config["library"]).exists())
        self.assertFalse(self.events)

    def test_duplicate_preserves_existing_files(self):
        self.destination.mkdir(parents=True)
        existing = self.destination / "original.flac"
        existing.write_text("keep this")
        with self.assertRaisesRegex(RuntimeError, "already exist"):
            self.rip()
        self.assertEqual(existing.read_text(), "keep this")
        self.assertFalse(self.events)

    def test_second_track_failure_never_publishes_partial_album(self):
        def command(args, cancel=None, timeout=900):
            if args[0] == "cdparanoia" and args[-2] == "2":
                raise RuntimeError("Unreadable second track")
            return self.fake_command(args, cancel, timeout)
        with self.assertRaisesRegex(RuntimeError, "Unreadable"):
            self.rip(command=command)
        self.assertFalse(self.destination.exists())
        self.assertNotIn("eject", self.events)
        self.assertFalse(list((Path(self.config["library"]) / ".copying").iterdir()))

    def test_cancel_during_art_lookup_prevents_publication(self):
        def cover(release):
            self.cancel.set()
            return b""
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.rip(cover=cover)
        self.assertFalse(self.destination.exists())
        self.assertNotIn("eject", self.events)

    def test_disconnect_between_tracks_stops_copy(self):
        mounted = {"value": True}
        def progress(value, message):
            if message.startswith("Saving track 1"):
                mounted["value"] = False
        with patch("core.os.path.ismount", side_effect=lambda path: mounted["value"]):
            with self.assertRaisesRegex(RuntimeError, "not mounted"):
                self.rip(progress=progress)
        self.assertFalse(self.destination.exists())
        self.assertNotIn("eject", self.events)

    def test_eject_failure_reports_saved_album(self):
        def command(args, cancel=None, timeout=900):
            if args[0] == "eject":
                raise RuntimeError("Drive busy")
            return self.fake_command(args, cancel, timeout)
        message = self.rip(command=command)
        self.assertIn("saved successfully", message)
        self.assertIn("Eject manually", message)
        self.assertTrue(self.destination.exists())

    def test_changed_disc_is_rejected(self):
        with patch("core.read_disc", return_value=core.Disc([1], [750], [0])), \
             patch("core.fetch_cover", return_value=b""):
            with self.assertRaisesRegex(RuntimeError, "CD changed"):
                core.rip_album(self.config, self.disc, self.metadata, lambda value, text: None, self.cancel)
        self.assertFalse(self.destination.exists())


class SafetyTests(unittest.TestCase):
    def test_atomic_publication_never_replaces_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.mkdir()
            (source / "new").write_text("new")
            target.mkdir()
            (target / "existing").write_text("keep")
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                core.publish_directory(source, target)
            self.assertEqual((target / "existing").read_text(), "keep")
            self.assertTrue((source / "new").is_file())

    def test_atomic_publication_moves_completed_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.mkdir()
            (source / "track").write_text("complete")
            core.publish_directory(source, target)
            self.assertFalse(source.exists())
            self.assertEqual((target / "track").read_text(), "complete")
            core.sync_filesystem(target)

    def test_music_folder_outside_drive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"mount": directory, "library": str(Path(directory).parent / "outside")}
            with patch("core.os.path.ismount", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "inside"):
                    core.require_storage(config, create=True)

    def test_filename_cannot_escape_album_folder(self):
        self.assertNotIn("/", core.safe_name("../../other/album"))
        self.assertNotIn("\\", core.safe_name("Artist\\Album"))
        self.assertEqual(core.safe_name(".."), "Unknown")

    def test_toc_matches_readable_cd_frames(self):
        disc = core.parse_toc(TOC)
        self.assertEqual(disc.numbers, [1, 2, 3])
        self.assertEqual(disc.offsets, [0, 12006, 23571])
        self.assertEqual(disc.lengths, [12006, 11565, 22377])
        self.assertEqual(len(disc.disc_id), 28)

    def test_data_disc_is_not_treated_as_audio(self):
        with self.assertRaisesRegex(RuntimeError, "No audio tracks"):
            core.parse_toc("No audio tracks on this disc")


if __name__ == "__main__":
    unittest.main()

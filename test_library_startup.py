"""Exercise the actual GUI storage callbacks without importing Qt or hardware.

Only the two callback methods are compiled from discroom.py. Widget setters
and worker completion are controlled here, including delayed mount and scan
timing; the implementation under test is the application's own source.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class LibraryStartupTests(unittest.TestCase):
    def setUp(self):
        self.mounted = False
        self.scanned_albums = ["stored album"]
        self.pending_workers = []
        source = Path(__file__).with_name("discroom.py")
        tree = ast.parse(source.read_text())
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        window.body = [node for node in window.body if isinstance(node, ast.FunctionDef)
                       and node.name in ("storage_status", "refresh_library")]
        window.bases = []
        window.decorator_list = []
        namespace = {
            "require_storage": self.require_storage,
            "shutil": SimpleNamespace(disk_usage=lambda path: SimpleNamespace(free=1024**3)),
            "scan_library": lambda path: (self.scanned_albums, 0),
        }
        exec(compile(ast.Module(body=[window], type_ignores=[]), str(source), "exec"), namespace)
        self.namespace = namespace
        self.window = namespace["MainWindow"]()
        self.window.config = {}
        self.window.demo = False
        self.window.albums = []
        self.window.scan_busy = False
        self.window.library_busy = False
        self.window.copying = False
        self.window.storage_available = False
        self.window.library_refresh_pending = False
        self.window.storage = Mock()
        self.window.message = Mock()
        self.window.render_albums = Mock()
        self.window.update_filters = Mock()
        self.window.error = Mock()
        self.window.work = Mock(side_effect=lambda *callbacks: self.pending_workers.append(callbacks))

    def require_storage(self, config, create=False):
        if not self.mounted:
            raise RuntimeError("Music drive is not mounted.")
        return Path("/test-music-drive/DiscRoom/Music")

    def finish_scan(self):
        scan, result, _ = self.pending_workers.pop(0)
        result(scan(None))

    def test_drive_available_at_startup_loads_once(self):
        self.mounted = True
        self.window.storage_status()
        self.finish_scan()
        self.assertEqual(self.window.albums, self.scanned_albums)
        for _ in range(4):
            self.window.storage_status()
        self.assertEqual(self.window.work.call_count, 1)

    def test_delayed_boot_mount_loads_without_button_press(self):
        for _ in range(3):
            self.window.storage_status()
        self.window.work.assert_not_called()
        self.mounted = True
        self.window.storage_status()
        self.finish_scan()
        self.assertEqual(self.window.albums, self.scanned_albums)

    def test_empty_library_does_not_trigger_repeated_scans(self):
        self.mounted = True
        self.scanned_albums = []
        self.window.storage_status()
        self.finish_scan()
        for _ in range(3):
            self.window.storage_status()
        self.assertEqual(self.window.work.call_count, 1)

    def test_reconnected_drive_automatically_loads_its_albums(self):
        self.mounted = True
        self.window.storage_status()
        self.finish_scan()
        self.mounted = False
        self.window.storage_status()
        self.assertEqual(self.window.albums, [])
        self.mounted = True
        self.scanned_albums = ["reconnected album"]
        self.window.storage_status()
        self.finish_scan()
        self.assertEqual(self.window.albums, self.scanned_albums)
        self.assertEqual(self.window.work.call_count, 2)

    def test_waits_for_copy_or_library_edit_to_finish(self):
        for flag in ("copying", "library_busy"):
            with self.subTest(busy=flag):
                self.setUp()
                self.mounted = True
                setattr(self.window, flag, True)
                self.window.storage_status()
                self.window.storage_status()
                self.window.work.assert_not_called()
                setattr(self.window, flag, False)
                self.window.storage_status()
                self.finish_scan()
                self.assertEqual(self.window.albums, self.scanned_albums)

    def test_reconnect_during_scan_queues_scan_after_completion(self):
        self.mounted = True
        self.window.storage_status()
        self.mounted = False
        self.window.storage_status()
        self.mounted = True
        self.window.storage_status()
        self.assertEqual(self.window.work.call_count, 1)
        self.finish_scan()
        self.window.storage_status()
        self.assertEqual(self.window.work.call_count, 2)
        self.finish_scan()
        self.window.storage_status()
        self.assertEqual(self.window.work.call_count, 2)

    def test_unmount_before_scan_completes_discards_its_albums(self):
        self.mounted = True
        self.window.storage_status()
        self.mounted = False
        self.finish_scan()
        self.assertEqual(self.window.albums, [])
        self.mounted = True
        self.window.storage_status()
        self.finish_scan()
        self.assertEqual(self.window.albums, self.scanned_albums)

    def test_unmount_between_storage_check_and_scan_can_recover(self):
        calls = []

        def disappearing_drive(config, create=False):
            calls.append(create)
            self.mounted = len(calls) == 1
            return self.require_storage(config, create)

        self.namespace["require_storage"] = disappearing_drive
        self.window.storage_status()
        self.window.work.assert_not_called()
        self.namespace["require_storage"] = self.require_storage
        self.mounted = True
        self.window.storage_status()
        self.finish_scan()
        self.assertEqual(self.window.albums, self.scanned_albums)

    def test_manual_refresh_still_works_without_duplicate_automatic_scan(self):
        self.mounted = True
        self.window.refresh_library()
        self.window.storage_status()
        self.finish_scan()
        self.window.storage_status()
        self.assertEqual(self.window.work.call_count, 1)
        self.window.refresh_library()
        self.finish_scan()
        self.assertEqual(self.window.work.call_count, 2)


if __name__ == "__main__":
    unittest.main()

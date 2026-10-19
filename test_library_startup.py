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

# The core test helper provides import shims when optional packages are absent.
import test_core
from core import format_file_size


class LibraryStartupTests(unittest.TestCase):
    def setUp(self):
        self.mounted = False
        self.scanned_albums = ["stored album"]
        self.pending_workers = []
        self.disk = SimpleNamespace(free=720_000_000_000, used=280_000_000_000, total=1_000_000_000_000)
        source = Path(__file__).with_name("discroom.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        window.body = [node for node in window.body if isinstance(node, ast.FunctionDef)
                       and node.name in ("storage_status", "refresh_library")]
        window.bases = []
        window.decorator_list = []
        namespace = {
            "require_storage": self.require_storage,
            "shutil": SimpleNamespace(disk_usage=lambda path: self.disk),
            "format_file_size": format_file_size,
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
        self.window.storage_bar = Mock()
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

    def test_storage_bar_uses_decimal_used_bytes_on_a_fixed_one_tb_scale(self):
        self.mounted = True
        # A smaller partition still uses the same graph scale: 280 GB is 28%.
        self.disk.total = 500_000_000_000
        self.disk.free = 220_000_000_000
        self.window.storage_status()
        self.window.storage_bar.setValue.assert_called_once_with(280)
        self.window.storage_bar.setEnabled.assert_called_once_with(True)
        self.assertIn("280.0 GB", self.window.storage.setText.call_args.args[0])
        self.assertIn("500.0 GB", self.window.storage_bar.setToolTip.call_args.args[0])
        self.assertIn("fixed at 0–1 TB", self.window.storage_bar.setToolTip.call_args.args[0])

    def test_storage_larger_than_one_tb_caps_the_bar_and_keeps_exact_used_label(self):
        self.mounted = True
        self.disk = SimpleNamespace(free=300_000_000_000, used=1_700_000_000_000, total=2_000_000_000_000)
        self.window.storage_status()
        self.window.storage_bar.setValue.assert_called_once_with(1000)
        self.assertIn("1.7 TB", self.window.storage.setText.call_args.args[0])

    def test_disconnected_storage_bar_is_disabled_and_unavailable(self):
        self.window.storage_status()
        self.window.storage_bar.setValue.assert_called_once_with(0)
        self.window.storage_bar.setEnabled.assert_called_once_with(False)
        self.assertIn("unavailable", self.window.storage_bar.setToolTip.call_args.args[0])
        self.window.work.assert_not_called()

    def test_demo_bar_is_an_explicit_example_without_reading_storage(self):
        self.window.demo = True
        self.namespace["require_storage"] = Mock(side_effect=AssertionError("Demo must not read storage"))
        self.window.storage_status()
        self.window.storage_bar.setValue.assert_called_once_with(128)
        self.window.storage_bar.setEnabled.assert_called_once_with(True)
        self.assertIn("demo", self.window.storage.setText.call_args.args[0])
        self.assertIn("Example", self.window.storage_bar.setToolTip.call_args.args[0])
        self.namespace["require_storage"].assert_not_called()
        self.window.work.assert_not_called()


if __name__ == "__main__":
    unittest.main()

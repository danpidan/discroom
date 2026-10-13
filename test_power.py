"""System power tests; every command and GUI service is replaced with a mock.

The actual power callback is compiled from the GUI source, as in the storage
startup tests, so Qt and a physical Pi are not needed. No test invokes systemctl.
"""
import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import power


class PowerCommandTests(unittest.TestCase):
    @patch("power.subprocess.run")
    def test_reboot_is_normal_noninteractive_request(self, run):
        run.return_value = SimpleNamespace(returncode=0)
        self.assertEqual(power.request_power("reboot"), "Restarting Pi…")
        run.assert_called_once_with(["systemctl", "--no-ask-password", "reboot"],
                                    capture_output=True, text=True, timeout=20)

    @patch("power.subprocess.run")
    def test_poweroff_is_normal_noninteractive_request(self, run):
        run.return_value = SimpleNamespace(returncode=0)
        self.assertEqual(power.request_power("poweroff"), "Shutting down Pi…")
        run.assert_called_once_with(["systemctl", "--no-ask-password", "poweroff"],
                                    capture_output=True, text=True, timeout=20)

    @patch("power.subprocess.run")
    def test_unrecognised_action_never_runs_a_command(self, run):
        for action in ("--force", "halt", "reboot; echo bad", "", None):
            with self.subTest(action=action), self.assertRaises(ValueError):
                power.request_power(action)
        run.assert_not_called()

    @patch("power.subprocess.run")
    def test_permission_failure_reports_refusal(self, run):
        run.return_value = SimpleNamespace(returncode=1, stderr="Interactive authentication required.", stdout="")
        with self.assertRaisesRegex(RuntimeError, "Interactive authentication required"):
            power.request_power("poweroff")

    @patch("power.subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 20))
    def test_timeout_is_reported(self, run):
        with self.assertRaisesRegex(RuntimeError, "did not respond"):
            power.request_power("reboot")

    @patch("power.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_systemctl_is_reported(self, run):
        with self.assertRaisesRegex(RuntimeError, "not found"):
            power.request_power("poweroff")


class PowerFlowTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("discroom.py")
        tree = ast.parse(source.read_text())
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        window.body = [node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == "power_action"]
        window.bases = []
        window.decorator_list = []
        self.dialog = object()
        self.delayed = []
        self.app = Mock()
        self.box = SimpleNamespace(Yes=1, No=2, question=Mock(return_value=1), information=Mock())
        self.save = Mock()
        self.request = Mock(return_value="Power request accepted")
        namespace = {
            "QMessageBox": self.box,
            "QTimer": SimpleNamespace(singleShot=lambda delay, callback: self.delayed.append(callback)),
            "QApplication": SimpleNamespace(instance=lambda: self.app),
            "save_config": self.save,
            "request_power": self.request,
        }
        exec(compile(ast.Module(body=[window], type_ignores=[]), str(source), "exec"), namespace)
        self.window = namespace["MainWindow"]()
        self.window.demo = False
        self.window.power_pending = False
        self.window.copying = False
        self.window.library_busy = False
        self.window.config = {}
        self.window.jobs = []
        self.window.message = Mock()
        self.window.timer = Mock()
        self.window.storage_timer = Mock()
        self.window.player = Mock()
        self.window.error = Mock()
        self.window.setEnabled = Mock()
        self.window.isFullScreen = Mock(return_value=True)
        self.window.work = Mock(side_effect=lambda *callbacks: self.window.jobs.append(callbacks))

    def complete_power_worker(self, failure=None):
        function, accepted, failed = self.window.jobs[0]
        if failure:
            failed(failure)
        else:
            accepted(function(None))
        self.window.jobs.pop(0)

    def run_delayed_callback(self):
        self.delayed.pop(0)()

    def test_confirmation_defaults_to_no_and_cancel_does_nothing(self):
        self.box.question.return_value = self.box.No
        self.window.power_action("reboot", self.dialog)
        self.assertEqual(self.box.question.call_args.args[-1], self.box.No)
        self.window.work.assert_not_called()
        self.window.player.stop.assert_not_called()
        self.save.assert_not_called()
        self.assertFalse(self.window.power_pending)

    def test_copy_or_edit_blocks_power_without_confirmation(self):
        for flag in ("copying", "library_busy"):
            setattr(self.window, flag, True)
            self.window.power_action("poweroff", self.dialog)
            setattr(self.window, flag, False)
        self.assertEqual(self.box.information.call_count, 2)
        self.box.question.assert_not_called()
        self.window.work.assert_not_called()

    def test_copy_starting_during_confirmation_still_blocks_power(self):
        def confirm(*arguments):
            self.window.copying = True
            return self.box.Yes
        self.box.question.side_effect = confirm
        self.window.power_action("reboot", self.dialog)
        self.window.work.assert_not_called()
        self.assertFalse(self.window.power_pending)

    def test_demo_never_makes_power_requests(self):
        self.window.demo = True
        self.window.power_action("poweroff", self.dialog)
        self.box.question.assert_not_called()
        self.window.work.assert_not_called()
        self.request.assert_not_called()

    def test_settings_and_audio_are_handled_before_request_and_exit(self):
        self.window.power_action("reboot", self.dialog)
        self.save.assert_called_once_with({"fullscreen": True})
        self.window.player.stop.assert_called_once()
        self.window.timer.stop.assert_called_once()
        self.window.storage_timer.stop.assert_called_once()
        self.request.assert_not_called()
        self.complete_power_worker()
        self.request.assert_called_once_with("reboot")
        self.app.exit.assert_not_called()
        self.run_delayed_callback()
        self.app.exit.assert_called_once_with(0)

    def test_background_workers_finish_before_request(self):
        self.window.jobs.append("existing drive scan")
        self.window.power_action("poweroff", self.dialog)
        self.window.work.assert_not_called()
        self.save.assert_not_called()
        self.window.jobs.clear()
        self.run_delayed_callback()
        self.window.work.assert_called_once()
        self.save.assert_called_once()
        self.complete_power_worker()
        self.request.assert_called_once_with("poweroff")

    def test_failed_setting_save_restores_controls_without_system_request(self):
        self.save.side_effect = OSError("Settings disk full")
        self.window.power_action("reboot", self.dialog)
        self.window.work.assert_not_called()
        self.window.player.stop.assert_not_called()
        self.assertFalse(self.window.power_pending)
        self.window.setEnabled.assert_called_with(True)
        self.window.timer.start.assert_called_once()
        self.window.storage_timer.start.assert_called_once()
        self.window.error.assert_called_once_with("Settings disk full")

    def test_refused_system_request_restores_app_without_exit(self):
        self.window.power_action("poweroff", self.dialog)
        self.complete_power_worker(failure="System refused shutdown")
        self.assertFalse(self.window.power_pending)
        self.window.setEnabled.assert_called_with(True)
        self.window.timer.start.assert_called_once()
        self.window.storage_timer.start.assert_called_once()
        self.window.error.assert_called_once_with("System refused shutdown")
        self.app.exit.assert_not_called()

    def test_repeated_press_only_starts_one_request(self):
        self.window.power_action("reboot", self.dialog)
        self.window.power_action("poweroff", self.dialog)
        self.box.question.assert_called_once()
        self.window.work.assert_called_once()


if __name__ == "__main__":
    unittest.main()

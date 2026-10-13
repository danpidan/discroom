import subprocess
import unittest
from unittest.mock import patch

from audio import discover_outputs, parse_outputs


DEVICE_LIST = """List of detected audio devices:
  'auto' (Autoselect device)
  'alsa/sysdefault:CARD=Device_1' (USB Audio Device, USB Audio
    Default Audio Device)
  'pulse/alsa_output.usb-C-Media_USB_Audio_Device-00.analog-stereo' (USB Audio Device Analog Stereo)
  'alsa/hdmi:CARD=vc4hdmi0,DEV=0' (vc4-hdmi-0, MAI PCM i2s-hifi-0
    HDMI Audio Output)
"""


class AudioTests(unittest.TestCase):
    def test_exact_changed_card_id_is_preserved(self):
        devices = parse_outputs(DEVICE_LIST)
        self.assertEqual(devices[1].name, "alsa/sysdefault:CARD=Device_1")
        self.assertNotIn("alsa/plughw:CARD=Device,DEV=0", [device.name for device in devices])

    def test_multiline_description_and_desktop_backend_are_usable(self):
        devices = parse_outputs(DEVICE_LIST)
        self.assertEqual(devices[1].description, "USB Audio Device, USB Audio Default Audio Device")
        self.assertIn("desktop audio", devices[2].label)
        self.assertEqual(devices[0].label, "System default output")

    def test_duplicates_from_stdout_and_stderr_do_not_repeat(self):
        self.assertEqual(len(parse_outputs(DEVICE_LIST + DEVICE_LIST)), 4)

    def test_detection_uses_mpv_ids_without_guessing(self):
        result = subprocess.CompletedProcess([], 0, stdout=DEVICE_LIST, stderr="")
        with patch("audio.subprocess.run", return_value=result) as command:
            devices = discover_outputs()
        self.assertEqual(command.call_args.args[0], ["mpv", "--no-config", "--audio-device=help"])
        self.assertEqual(len(devices), 4)

    def test_missing_mpv_has_actionable_message(self):
        with patch("audio.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "sudo apt install mpv"):
                discover_outputs()

    def test_timeout_is_reported(self):
        with patch("audio.subprocess.run", side_effect=subprocess.TimeoutExpired("mpv", 10)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                discover_outputs()

    def test_failed_detection_does_not_fall_back_to_hdmi(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="Cannot initialise audio")
        with patch("audio.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "could not be detected"):
                discover_outputs()

    def test_empty_output_requests_reconnection(self):
        result = subprocess.CompletedProcess([], 0, stdout="List of detected audio devices:\n", stderr="")
        with patch("audio.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "Check your DAC"):
                discover_outputs()


if __name__ == "__main__":
    unittest.main()

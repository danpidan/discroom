import json
import socket
import threading
import unittest
from unittest.mock import patch

from mpv_control import ControlCancelled, request_batch, report_playback_exit


class FakeSocket:
    """Protocol-level fake: no network or socket-bind permission is required."""
    def __init__(self, chunks, on_read=None):
        self.chunks = list(chunks)
        self.commands = []
        self.on_read = on_read
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, value):
        self.timeout = value

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.commands.extend(json.loads(line) for line in data.splitlines())

    def recv(self, size):
        if self.on_read:
            self.on_read()
        chunk = self.chunks.pop(0) if self.chunks else b""
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


class ControlTests(unittest.TestCase):
    def test_volume_reaches_player_despite_events_and_fragmented_replies(self):
        second = json.dumps({"request_id": 2, "error": "success", "data": 85}).encode() + b"\n"
        stream = FakeSocket([b'{"event":"idle"}\n', socket.timeout(), second[:15], second[15:],
                             b'{"request_id":1,"error":"success","data":null}\n'])
        with patch("mpv_control.socket.socket", return_value=stream):
            results = request_batch("/test/player.sock", [["set_property", "volume", 85], ["get_property", "volume"]])
        self.assertEqual(stream.commands[0]["command"], ["set_property", "volume", 85])
        self.assertEqual(results[0]["error"], "success")
        self.assertEqual(results[1]["data"], 85)
        self.assertTrue(stream.closed)

    def test_new_track_cancels_old_control_reply(self):
        cancelled = threading.Event()
        stream = FakeSocket([b'{"event":"shutdown"}\n'], on_read=cancelled.set)
        with patch("mpv_control.socket.socket", return_value=stream):
            with self.assertRaises(ControlCancelled):
                request_batch("/test/player.sock", [["get_property", "time-pos"]], cancelled.is_set)
        self.assertTrue(stream.closed)

    def test_closed_socket_is_not_mistaken_for_a_successful_command(self):
        stream = FakeSocket([b'{"event":"idle"}\n', b""])
        with patch("mpv_control.socket.socket", return_value=stream):
            with self.assertRaises(ConnectionError):
                request_batch("/test/player.sock", [["set_property", "volume", 85]])

    def test_switching_tracks_and_stop_never_report_expected_exit(self):
        for code in (0, 4, 143, -15):
            for normal in (True, False):
                self.assertFalse(report_playback_exit(code, normal, intentional=True))

    def test_genuine_failure_is_still_reported(self):
        self.assertTrue(report_playback_exit(4, True, intentional=False))
        self.assertTrue(report_playback_exit(-11, False, intentional=False))
        self.assertFalse(report_playback_exit(0, True, intentional=False))


if __name__ == "__main__":
    unittest.main()

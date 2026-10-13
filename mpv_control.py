"""Framed local mpv control protocol; read replies even when events arrive first."""
import errno
import json
import socket
import time


class ControlCancelled(Exception):
    pass


def request_batch(path, commands, cancelled=lambda: False, timeout=1.2):
    """Wait for every command reply, ignoring unrelated and fragmented events."""
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(0.15)
        while True:
            if cancelled():
                raise ControlCancelled()
            try:
                stream.connect(path)
                break
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("Playback controls are not ready yet.") from None
                time.sleep(0.025)
        if cancelled():
            raise ControlCancelled()
        for number, command in enumerate(commands, 1):
            stream.sendall((json.dumps({"command": command, "request_id": number}) + "\n").encode())
        replies = {}
        buffer = b""
        while len(replies) < len(commands):
            if cancelled():
                raise ControlCancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Playback control reply timed out.")
            try:
                chunk = stream.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("Playback process closed its control connection.")
            buffer += chunk
            if len(buffer) > 1024 * 1024:
                raise ValueError("Unexpectedly large playback control message.")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                message = json.loads(line)
                request_id = message.get("request_id")
                if isinstance(request_id, int) and 1 <= request_id <= len(commands):
                    replies[request_id] = message
        return [replies[number] for number in range(1, len(commands) + 1)]


def report_playback_exit(exit_code, normal_exit, intentional):
    return not intentional and (exit_code != 0 or not normal_exit)

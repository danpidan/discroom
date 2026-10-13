"""Asynchronous mpv controls and large touchscreen transport/volume controls."""
import bisect
import os
from pathlib import Path
import queue
import tempfile
import threading
import uuid

from PyQt5.QtCore import Qt, QProcess, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QMessageBox

from core import require_storage, save_config
from mpv_control import ControlCancelled, request_batch, report_playback_exit


def control_button(text, callback):
    item = QPushButton(text)
    item.setMinimumHeight(58)
    item.clicked.connect(callback)
    return item


def clock(value):
    value = int(value or 0)
    return f"{value // 60}:{value % 60:02d}"


class ControlWorker(QThread):
    completed = pyqtSignal(int, str, object)
    failed = pyqtSignal(int, str, str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.tasks = queue.Queue()
        self.lock = threading.Lock()
        self.pending = set()
        self.volumes = {}
        self.generation = 0

    def session(self, generation):
        self.generation = generation

    def submit(self, generation, kind, command=None):
        key = (generation, kind)
        with self.lock:
            if kind == "volume":
                self.volumes[generation] = command
            if kind in ("state", "volume"):
                if key in self.pending:
                    return
                self.pending.add(key)
        self.tasks.put((generation, kind, command))

    def run(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            generation, kind, command = task
            with self.lock:
                self.pending.discard((generation, kind))
                if kind == "volume":
                    command = self.volumes.pop(generation, command)
            if generation != self.generation:
                continue
            try:
                if kind == "state":
                    properties = ["time-pos", "duration", "pause", "playlist-pos", "chapter", "volume"]
                    replies = request_batch(self.path, [["get_property", key] for key in properties],
                                            cancelled=lambda: generation != self.generation)
                    result = {key: reply.get("data") for key, reply in zip(properties, replies)
                              if reply.get("error") == "success"}
                else:
                    actual = ["set_property", "volume", command] if kind == "volume" else command
                    reply = request_batch(self.path, [actual], cancelled=lambda: generation != self.generation)[0]
                    if reply.get("error") != "success":
                        raise RuntimeError(reply.get("error", "Playback control failed."))
                    result = command if kind == "volume" else reply.get("data")
                self.completed.emit(generation, kind, result)
            except ControlCancelled:
                pass
            except Exception as exc:
                self.failed.emit(generation, kind, str(exc))

    def shutdown(self):
        self.generation = -1
        self.tasks.put(None)
        self.wait(1800)


class Player(QWidget):
    changed = pyqtSignal()

    def __init__(self, config, parent):
        super().__init__(parent)
        self.main = parent
        self.config = config
        self.demo = parent.demo
        self.sources = []
        self.names = []
        self.position = -1
        self.from_cd = False
        self.cd_offsets = []
        self.duration = 0
        self.generation = 0
        self.intentional_stop = False
        self.pending_volume = None
        self.ipc = str(Path(tempfile.gettempdir()) / f"discroom-{os.getuid()}-{uuid.uuid4().hex}.sock")
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.started.connect(self.started)
        self.process.readyReadStandardError.connect(self.read_error)
        self.diagnostics = ""
        self.control = ControlWorker(self.ipc)
        self.control.completed.connect(self.control_result)
        self.control.failed.connect(self.control_failure)
        self.control.start()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        labels = QVBoxLayout()
        self.title = QLabel("Choose something to play")
        self.title.setMinimumWidth(150)
        self.title.setMaximumWidth(245)
        self.title.setWordWrap(True)
        self.artist = QLabel("Your music")
        self.artist.setWordWrap(True)
        self.artist.setMaximumWidth(245)
        labels.addWidget(self.title)
        labels.addWidget(self.artist)
        top.addLayout(labels, 1)
        top.addWidget(control_button("Previous", lambda: self.skip(-1)))
        self.pause = control_button("Play", self.toggle)
        top.addWidget(self.pause)
        top.addWidget(control_button("Next", lambda: self.skip(1)))
        top.addWidget(control_button("Stop", self.stop))
        top.addSpacing(10)
        top.addWidget(control_button("-", lambda: self.volume.setValue(self.volume.value() - 5)))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(max(0, min(100, int(config.get("volume", 40)))))
        self.volume.setMinimumWidth(130)
        self.volume.setMaximumWidth(190)
        self.volume.setMinimumHeight(40)
        self.volume.setPageStep(5)
        top.addWidget(self.volume)
        top.addWidget(control_button("+", lambda: self.volume.setValue(self.volume.value() + 5)))
        self.volume_text = QLabel(f"{self.volume.value()}%")
        self.volume_text.setMinimumWidth(50)
        top.addWidget(self.volume_text)
        layout.addLayout(top)
        timeline = QHBoxLayout()
        self.elapsed = QLabel("0:00")
        timeline.addWidget(self.elapsed)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setMinimumHeight(30)
        self.seek.sliderReleased.connect(self.seek_to)
        timeline.addWidget(self.seek, 1)
        self.total = QLabel("0:00")
        timeline.addWidget(self.total)
        layout.addLayout(timeline)
        self.volume.valueChanged.connect(self.set_volume)
        self.volume_save_timer = QTimer(self)
        self.volume_save_timer.setSingleShot(True)
        self.volume_save_timer.timeout.connect(self.save_volume)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(800)

    def set_volume(self, value):
        self.config["volume"] = value
        self.volume_text.setText(f"{value}%")
        self.volume_save_timer.start(500)
        if self.process.state() == QProcess.Running:
            self.pending_volume = value
            self.control.submit(self.generation, "volume", value)

    def save_volume(self):
        if not self.demo:
            try:
                save_config(self.config)
            except Exception as exc:
                self.main.message.setText(f"Volume changed, but its preference could not be saved: {exc}")

    def started(self):
        self.pending_volume = self.volume.value()
        self.control.submit(self.generation, "volume", self.volume.value())

    def play(self, sources, names, from_cd=False, cd_offsets=None, start_seconds=0, start_index=0):
        # Copy lists before stop clears the previous session, including replay calls.
        sources, names, cd_offsets = list(sources), list(names), list(cd_offsets or [])
        self.stop()
        if not sources:
            return
        if self.demo:
            self.title.setText(names[0][0])
            self.artist.setText(names[0][1] + " · demo")
            self.pause.setText("Pause")
            return
        if not self.config.get("audio_device"):
            QMessageBox.information(self, "Choose an audio output", "Open Settings, choose your USB DAC and press Save.")
            return
        if not from_cd:
            try:
                require_storage(self.config)
            except Exception as exc:
                QMessageBox.warning(self, "Music drive", str(exc))
                return
        self.sources, self.names, self.from_cd = sources, names, from_cd
        self.cd_offsets = cd_offsets
        self.position = -1
        self.diagnostics = ""
        self.intentional_stop = False
        args = ["--no-video", "--no-config", "--keep-open=no", "--msg-level=all=warn",
                f"--input-ipc-server={self.ipc}", f"--audio-device={self.config['audio_device']}",
                f"--volume={self.volume.value()}"]
        if from_cd:
            args += [f"--cdrom-device={self.config['device']}", f"--start={start_seconds}"]
        elif start_index:
            args.append(f"--playlist-start={start_index}")
        self.process.start("mpv", args + ["--"] + sources)
        display = max(0, min(start_index, len(names) - 1))
        self.title.setText(names[display][0])
        self.artist.setText(names[display][1])
        self.pause.setText("Pause")
        self.changed.emit()

    def read_error(self):
        self.diagnostics = (self.diagnostics + bytes(self.process.readAllStandardError()).decode("utf-8", "replace"))[-2500:]

    def process_error(self, error):
        if error == QProcess.FailedToStart and not self.intentional_stop:
            QMessageBox.warning(self, "Playback", "mpv could not start. Install it with: sudo apt install mpv")

    def finished(self, code, status):
        self.read_error()
        self.pause.setText("Play")
        if report_playback_exit(code, status == QProcess.NormalExit, self.intentional_stop):
            QMessageBox.warning(self, "Playback stopped",
                                "Playback could not continue. Check the selected audio output in Settings.\n\n"
                                + (self.diagnostics or "The playback process stopped unexpectedly."))
        self.changed.emit()

    def command(self, command):
        if self.process.state() == QProcess.Running:
            self.control.submit(self.generation, "command", command)

    def toggle(self):
        if self.process.state() == QProcess.Running:
            self.command(["cycle", "pause"])
        elif self.sources:
            self.play(self.sources, self.names, self.from_cd, self.cd_offsets)

    def skip(self, direction):
        self.command(["add", "chapter", direction] if self.from_cd else
                     ["playlist-next" if direction > 0 else "playlist-prev", "force"])

    def stop(self):
        self.intentional_stop = True
        self.generation += 1
        self.control.session(self.generation)
        self.pending_volume = None
        if self.process.state() != QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(700):
                self.process.kill()
                self.process.waitForFinished(500)
        self.from_cd = False
        self.sources = []
        self.names = []
        self.cd_offsets = []
        self.pause.setText("Play")
        self.duration = 0
        self.elapsed.setText("0:00")
        self.total.setText("0:00")
        self.seek.setValue(0)
        if os.path.exists(self.ipc):
            os.unlink(self.ipc)

    def seek_to(self):
        if self.duration:
            self.command(["seek", self.seek.value() * self.duration / 1000, "absolute"])

    def tick(self):
        if self.process.state() == QProcess.Running:
            self.control.submit(self.generation, "state")

    def control_result(self, generation, kind, result):
        if generation != self.generation or self.process.state() != QProcess.Running:
            return
        if kind == "volume":
            if result == self.pending_volume:
                self.pending_volume = None
            return
        if kind != "state":
            return
        elapsed = result.get("time-pos") or 0
        self.duration = result.get("duration") or 0
        self.elapsed.setText(clock(elapsed))
        self.total.setText(clock(self.duration))
        self.pause.setText("Play" if result.get("pause") else "Pause")
        if self.duration and not self.seek.isSliderDown():
            self.seek.setValue(int(elapsed / self.duration * 1000))
        if self.pending_volume is None and not self.volume.isSliderDown() and isinstance(result.get("volume"), (int, float)):
            value = max(0, min(100, round(result["volume"])))
            self.volume.blockSignals(True)
            self.volume.setValue(value)
            self.volume.blockSignals(False)
            self.volume_text.setText(f"{value}%")
        position = result.get("chapter" if self.from_cd else "playlist-pos")
        if self.from_cd and not isinstance(position, int) and self.cd_offsets:
            position = max(0, bisect.bisect_right(self.cd_offsets, elapsed) - 1)
        if isinstance(position, int) and 0 <= position < len(self.names):
            self.title.setText(self.names[position][0])
            self.artist.setText(self.names[position][1])
            if position != self.position:
                self.position = position
                self.changed.emit()

    def control_failure(self, generation, kind, message):
        if generation != self.generation or self.process.state() != QProcess.Running:
            return
        if kind == "volume":
            self.pending_volume = None
            self.main.message.setText(f"Volume control could not apply the change: {message}")
        elif kind == "command":
            self.main.message.setText(f"Playback control could not apply the change: {message}")

    def shutdown(self):
        self.timer.stop()
        self.volume_save_timer.stop()
        self.save_volume()
        self.stop()
        if self.control.isRunning():
            self.control.shutdown()

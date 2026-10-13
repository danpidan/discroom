#!/usr/bin/env python3
"""Disc Room: native Qt touchscreen CD player and music library, first hardware build."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import uuid

from PyQt5.QtCore import Qt, QEvent, QProcess, QThread, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
                            QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                            QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                            QProgressBar, QPushButton, QScrollArea, QSlider,
                            QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                            QVBoxLayout, QWidget)

from core import (VERSION, Album, Disc, Track, blank_metadata, drive_has_disc,
                  load_config, lookup_disc, read_disc, require_storage,
                  rip_album, save_config, scan_library, run_checked)
from audio import discover_outputs
from player import Player
from library_management import edit_tags, move_to_trash, list_trash, restore_trash, delete_trash_forever
from updater import UpdateStore, RESTART_CODE
from power import request_power

RESTART_FLAGS = []

STYLE = """
QWidget { background: #0d1522; color: #eef1f7; font-family: 'DejaVu Sans'; font-size: 17px; }
QLabel { background: transparent; }
QFrame#panel { background: #182332; border: 1px solid #2b394d; border-radius: 16px; }
QPushButton { background: #1c2839; border: 1px solid #35435a; border-radius: 12px; padding: 10px 14px; min-height: 32px; font-size: 18px; }
QPushButton:hover, QPushButton:checked { background: #354055; border-color: #f4be68; }
QPushButton:disabled { color: #788496; border-color: #253247; }
QPushButton#primary { background: #f4be68; color: #171a22; font-weight: bold; border-color: #f4be68; }
QPushButton#tile { text-align: left; padding: 0; }
QLineEdit, QComboBox { background: #1b2636; border: 1px solid #3a4860; border-radius: 10px; padding: 10px; min-height: 24px; }
QLineEdit:focus { border-color: #f4be68; }
QComboBox QAbstractItemView { selection-background-color: #475268; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #152030; width: 14px; }
QScrollBar::handle:vertical { background: #47556b; min-height: 35px; border-radius: 6px; }
QTableWidget, QListWidget { background: #152030; border: 1px solid #33415a; gridline-color: #33415a; selection-background-color: #43516b; }
QHeaderView::section { background: #233047; color: #f4be68; padding: 10px; border: none; }
QTableWidget::item { padding: 6px; }
QProgressBar { border: 1px solid #47556b; border-radius: 8px; text-align: center; min-height: 28px; }
QProgressBar::chunk { background: #c28f46; border-radius: 6px; }
QSlider::groove:horizontal { height: 8px; background: #465165; border-radius: 4px; }
QSlider::handle:horizontal { background: #f4be68; width: 30px; margin: -10px 0; border-radius: 15px; }
QCheckBox { spacing: 12px; padding: 8px; }
QCheckBox::indicator { width: 24px; height: 24px; }
"""


def button(text, action=None, primary=False):
    item = QPushButton(text)
    item.setMinimumHeight(58)
    if primary:
        item.setObjectName("primary")
    if action:
        item.clicked.connect(action)
    return item


def heading(text, size=26):
    item = QLabel(text)
    item.setFont(QFont("DejaVu Sans", size, QFont.Bold))
    item.setStyleSheet(f"font-size: {size}px; font-weight: bold;")
    return item


def clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


class Worker(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, function):
        super().__init__()
        self.function = function
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.function(self))
        except Exception as exc:
            self.error.emit(str(exc))


class Keyboard(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(3)
        self.upper = False
        self.keys = []
        for letters in ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm.,-"):
            row = QHBoxLayout()
            row.setSpacing(3)
            for character in letters:
                key = button(character)
                key.setFocusPolicy(Qt.NoFocus)
                key.setMinimumHeight(42)
                key.setStyleSheet("padding: 2px; min-height: 30px;")
                key.clicked.connect(lambda checked=False, c=character: self.type(c.upper() if self.upper else c))
                self.keys.append((key, character))
                row.addWidget(key)
            layout.addLayout(row)
        row = QHBoxLayout()
        for label, callback in (("Shift", self.shift), ("Space", lambda: self.type(" ")),
                                ("Backspace", self.backspace), ("Hide keyboard", self.hide)):
            key = button(label, callback)
            key.setFocusPolicy(Qt.NoFocus)
            key.setMinimumHeight(42)
            key.setStyleSheet("padding: 2px; min-height: 30px;")
            row.addWidget(key)
        layout.addLayout(row)
        self.hide()

    def type(self, text):
        target = QApplication.focusWidget()
        if isinstance(target, QLineEdit):
            target.insert(text)

    def backspace(self):
        target = QApplication.focusWidget()
        if isinstance(target, QLineEdit):
            target.backspace()

    def shift(self):
        self.upper = not self.upper
        for key, character in self.keys:
            key.setText(character.upper() if self.upper else character)


class EditDelegate(QStyledItemDelegate):
    editing = pyqtSignal()

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        self.editing.emit()
        return editor


class Cover(QWidget):
    def __init__(self, album):
        super().__init__()
        self.album = album
        self.pixmap = QPixmap()
        if album.cover:
            self.pixmap.loadFromData(album.cover)
        self.setMinimumHeight(140)
        self.setMaximumHeight(190)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.pixmap.isNull():
            pixmap = self.pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap((self.width() - pixmap.width()) // 2, (self.height() - pixmap.height()) // 2, pixmap)
            return
        seed = hashlib.sha256(self.album.key.encode()).digest()
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor.fromHsv(seed[0] * 359 // 255, 140, 145))
        gradient.setColorAt(1, QColor.fromHsv(seed[1] * 359 // 255, 180, 65))
        painter.fillRect(self.rect(), gradient)
        painter.setPen(QColor(255, 255, 255, 90))
        for i in range(5):
            painter.drawEllipse(int(self.width() * .35 - i * 14), int(self.height() * .12 - i * 14), 80 + i * 28, 80 + i * 28)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("DejaVu Sans", 30, QFont.Bold))
        painter.drawText(self.rect().adjusted(18, 0, -18, -12), Qt.AlignLeft | Qt.AlignBottom,
                         "".join(word[0] for word in self.album.artist.split()[:2]).upper() or "DR")


class MetadataDialog(QDialog):
    def __init__(self, disc, metadata, matches, parent, editing=False):
        super().__init__(parent)
        self.disc = disc
        self.metadata = copy.deepcopy(metadata)
        self.matches = matches
        self.setWindowTitle("Album details")
        self.resize(min(1120, parent.width()), min(720, parent.height()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(heading("Edit album details" if editing else "Album details", 24))
        layout.addWidget(QLabel("Update the stored album and track details." if editing else "Confirm or edit the details before copying."))
        self.match_combo = QComboBox()
        self.match_combo.addItem("Enter details manually")
        for match in matches:
            self.match_combo.addItem(f"{match['artist']} — {match['album']}  {match['year']} {match['country']}")
        layout.addWidget(self.match_combo)
        if editing:
            self.match_combo.hide()
        fields = QGridLayout()
        self.inputs = {}
        for index, (key, label) in enumerate((("artist", "Album artist"), ("album", "Album title"),
                                              ("year", "Year"), ("genre", "Genre"))):
            fields.addWidget(QLabel(label), 0, index)
            edit = QLineEdit()
            edit.installEventFilter(self)
            self.inputs[key] = edit
            fields.addWidget(edit, 1, index)
        layout.addLayout(fields)
        self.compilation = QCheckBox("Various artists / compilation")
        self.compilation.toggled.connect(self.compilation_changed)
        layout.addWidget(self.compilation)
        self.table = QTableWidget(len(disc.numbers), 3)
        self.table.setHorizontalHeaderLabels(["Track", "Title", "Track artist"])
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 500)
        self.table.horizontalHeader().setStretchLastSection(True)
        delegate = EditDelegate(self.table)
        delegate.editing.connect(lambda: self.keyboard.show())
        self.table.setItemDelegate(delegate)
        self.table.setEditTriggers(QTableWidget.AllEditTriggers)
        layout.addWidget(self.table, 1)
        self.keyboard = Keyboard()
        layout.addWidget(self.keyboard)
        actions = QHBoxLayout()
        actions.addWidget(button("Cancel", self.reject))
        actions.addWidget(button("Save details" if editing else "Save & Copy", self.confirm, True))
        layout.addLayout(actions)
        self.populate(self.metadata)
        self.match_combo.currentIndexChanged.connect(self.change_match)
        if matches:
            self.match_combo.setCurrentIndex(1)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn and isinstance(obj, QLineEdit) and hasattr(self, "keyboard"):
            self.keyboard.show()
        return super().eventFilter(obj, event)

    def populate(self, metadata):
        self.metadata = copy.deepcopy(metadata)
        for key, edit in self.inputs.items():
            edit.setText(metadata[key])
        for row, (number, track) in enumerate(zip(self.disc.numbers, metadata["tracks"])):
            item = QTableWidgetItem(str(number))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(track["title"]))
            self.table.setItem(row, 2, QTableWidgetItem(track["artist"]))
            self.table.setRowHeight(row, 56)
        self.compilation.blockSignals(True)
        self.compilation.setChecked(metadata["artist"].lower() == "various artists")
        self.compilation.blockSignals(False)

    def change_match(self, index):
        self.populate(self.matches[index - 1] if index else blank_metadata(self.disc))

    def compilation_changed(self, enabled):
        if enabled:
            self.inputs["artist"].setText("Various artists")

    def confirm(self):
        # Move focus to commit an active table-cell editor before reading the model.
        self.table.clearFocus()
        self.setFocus()
        QApplication.processEvents()
        original_artist = self.metadata["artist"]
        for key, edit in self.inputs.items():
            self.metadata[key] = edit.text().strip()
        if not self.metadata["artist"] or not self.metadata["album"]:
            QMessageBox.warning(self, "Missing details", "Enter an album artist and album title.")
            return
        if self.metadata["year"] and (not self.metadata["year"].isdigit() or len(self.metadata["year"]) != 4):
            QMessageBox.warning(self, "Year", "Enter a four-digit year, or leave Year blank.")
            return
        self.metadata["tracks"] = []
        for row, number in enumerate(self.disc.numbers):
            title = self.table.item(row, 1).text().strip()
            if not title:
                QMessageBox.warning(self, "Missing title", f"Enter a title for track {number}.")
                return
            artist = self.table.item(row, 2).text().strip()
            if not artist or (artist == original_artist and not self.compilation.isChecked()):
                artist = self.metadata["artist"]
            self.metadata["tracks"].append({"title": title, "artist": artist})
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, config, demo=False):
        super().__init__()
        self.config = config
        self.demo = demo
        self.jobs = []
        self.albums = []
        self.disc = None
        self.matches = []
        self.metadata = None
        self.copy_job = None
        self.disc_busy = False
        self.copying = False
        self.scan_busy = False
        self.library_busy = False
        self.storage_available = False
        self.library_refresh_pending = False
        self.power_pending = False
        self.presence = False
        self.page = "Library"
        self.setWindowTitle(f"Disc Room {VERSION}")
        self.resize(1280, 720)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 8)
        top = QHBoxLayout()
        logo = heading("DISC  ROOM", 19)
        logo.setStyleSheet("color: #f4be68; font-size: 23px; font-weight: bold;")
        top.addWidget(logo)
        top.addStretch()
        self.storage = QLabel("Checking music drive…")
        top.addWidget(self.storage)
        outer.addLayout(top)
        content = QHBoxLayout()
        nav = QVBoxLayout()
        self.nav_buttons = {}
        for label in ("Library", "Favourites", "Queue", "Settings"):
            control = button(label, lambda checked=False, name=label: self.navigate(name))
            control.setCheckable(True)
            self.nav_buttons[label] = control
            nav.addWidget(control)
        nav.addStretch()
        nav.addWidget(button("Refresh library", self.refresh_library))
        content.addLayout(nav)
        main = QVBoxLayout()
        self.page_title = heading("Your music", 28)
        main.addWidget(self.page_title)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search artists, albums or tracks")
        self.search.textChanged.connect(self.render_albums)
        self.search.installEventFilter(self)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(button("Keyboard", self.show_keyboard))
        main.addLayout(search_row)
        filters = QHBoxLayout()
        self.filters = {}
        for name in ("Artist", "Genre", "Year"):
            combo = QComboBox()
            combo.addItem(f"All {name.lower()}s")
            combo.currentIndexChanged.connect(self.render_albums)
            filters.addWidget(combo)
            self.filters[name] = combo
        self.sort = QComboBox()
        self.sort.addItems(["Recently added", "Artist A–Z", "Album A–Z", "Year"])
        self.sort.currentIndexChanged.connect(self.render_albums)
        filters.addWidget(self.sort)
        main.addLayout(filters)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(16)
        self.grid.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.grid_widget)
        main.addWidget(self.scroll, 1)
        self.message = QLabel("Welcome. Connect your music drive to get started.")
        self.message.setWordWrap(True)
        main.addWidget(self.message)
        content.addLayout(main, 1)
        self.cd_panel = QFrame()
        self.cd_panel.setObjectName("panel")
        self.cd_panel.setFixedWidth(265)
        cd_layout = QVBoxLayout(self.cd_panel)
        cd_layout.setContentsMargins(18, 18, 18, 18)
        cd_layout.addWidget(heading("CD", 24))
        self.cd_title = heading("Insert a CD", 19)
        self.cd_title.setWordWrap(True)
        self.cd_info = QLabel("Play it now, or save it to your library.")
        self.cd_info.setWordWrap(True)
        cd_layout.addWidget(self.cd_title)
        cd_layout.addWidget(self.cd_info)
        cd_layout.addStretch()
        self.play_cd_button = button("Play", self.play_cd, True)
        self.copy_button = button("Copy", self.copy_cd)
        cd_actions = QHBoxLayout()
        cd_actions.addWidget(self.play_cd_button)
        cd_actions.addWidget(self.copy_button)
        cd_layout.addLayout(cd_actions)
        self.progress = QProgressBar()
        self.progress.hide()
        cd_layout.addWidget(self.progress)
        self.cancel_copy = button("Cancel copy", self.cancel_rip)
        self.cancel_copy.hide()
        cd_layout.addWidget(self.cancel_copy)
        cd_controls = QHBoxLayout()
        cd_controls.addWidget(button("Tracks", self.cd_tracks))
        self.eject_button = button("Eject", self.eject)
        cd_controls.addWidget(self.eject_button)
        cd_layout.addLayout(cd_controls)
        note = QLabel("Lossless FLAC\nEdit details before copying")
        note.setWordWrap(True)
        cd_layout.addWidget(note)
        content.addWidget(self.cd_panel)
        outer.addLayout(content, 1)
        self.keyboard = Keyboard()
        outer.addWidget(self.keyboard)
        self.player = Player(config, self)
        outer.addWidget(self.player)
        self.nav_buttons["Library"].setChecked(True)
        self.set_cd_buttons()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_disc)
        self.timer.start(4000)
        self.storage_timer = QTimer(self)
        self.storage_timer.timeout.connect(self.storage_status)
        self.storage_timer.start(5000)
        if demo:
            self.seed_demo()
        else:
            QTimer.singleShot(500, self.poll_disc)
        self.storage_status()

    def work(self, function, result, error=None):
        worker = Worker(function)
        self.jobs.append(worker)
        worker.result.connect(result)
        worker.error.connect(error or self.error)
        worker.finished.connect(lambda: self.job_finished(worker))
        worker.start()
        return worker

    def job_finished(self, worker):
        if worker in self.jobs:
            self.jobs.remove(worker)
        worker.deleteLater()

    def error(self, text):
        self.message.setText(text)
        QMessageBox.warning(self, "Disc Room", text)

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.FocusIn:
            self.keyboard.show()
        return super().eventFilter(obj, event)

    def show_keyboard(self):
        self.search.setFocus()
        self.keyboard.show()

    def seed_demo(self):
        names = [("Quiet Roads", "Northbound", "Folk", "2024"), ("Blue Hour", "Mira Vale", "Jazz", "2025"),
                 ("Open Skies", "The Pines", "Folk", "2023"), ("After Midnight", "Jon Bell", "Jazz", "2022"),
                 ("Slow Waves", "Coastline", "Ambient", "2025"), ("Small Hours", "Ada Grey", "Soul", "2024")]
        self.albums = [Album(str(i), title, artist, year, genre, modified=i,
                             tracks=[Track("demo", title, artist, 1, 248)])
                       for i, (title, artist, genre, year) in enumerate(names)]
        self.disc = Disc(list(range(1, 13)), [18000] * 12, [i * 18000 for i in range(12)])
        self.metadata = blank_metadata(self.disc)
        self.metadata.update(album="Blue Hour", artist="Mira Vale", year="2025", genre="Jazz")
        self.matches = []
        self.cd_title.setText("CD detected")
        self.cd_info.setText("Blue Hour\nMira Vale\n\n12 tracks · 48 min")
        self.message.setText("Design preview · hardware actions are disabled in demo mode.")
        self.update_filters()
        self.render_albums()
        self.set_cd_buttons()

    def storage_status(self):
        if self.demo:
            self.storage.setText("●  Design preview")
            return
        try:
            library = require_storage(self.config, create=True)
            disk = shutil.disk_usage(library)
            self.storage.setText(f"●  Music drive · {disk.free / 1024**3:.0f} GiB free")
            self.storage.setStyleSheet("color: #77dda2;")
        except Exception:
            self.storage_available = False
            self.library_refresh_pending = False
            self.storage.setText("●  Music drive disconnected")
            self.storage.setStyleSheet("color: #f4be68;")
            self.albums = []
            self.update_filters()
            self.render_albums()
            return
        if not self.storage_available:
            self.storage_available = True
            self.library_refresh_pending = True
        if self.library_refresh_pending and not (self.scan_busy or self.library_busy or self.copying):
            self.refresh_library()

    def refresh_library(self):
        if self.demo:
            self.render_albums()
            return
        if self.scan_busy or self.library_busy:
            return
        try:
            library = require_storage(self.config, create=True)
        except Exception as exc:
            self.storage_available = False
            self.library_refresh_pending = False
            self.message.setText(str(exc))
            return
        self.storage_available = True
        self.library_refresh_pending = False
        self.scan_busy = True
        self.message.setText("Reading your music library…")
        def result(value):
            self.scan_busy = False
            try:
                require_storage(self.config)
            except Exception:
                self.storage_available = False
                self.library_refresh_pending = False
                self.albums = []
                self.update_filters()
                self.render_albums()
                return
            self.albums, skipped = value
            self.update_filters()
            self.render_albums()
            self.message.setText(f"{len(self.albums)} albums" + (f" · {skipped} unreadable files skipped" if skipped else "")
                                 if self.albums else "No albums yet. Insert a CD and choose Copy CD to add an album.")
        def failed(text):
            self.scan_busy = False
            self.error(text)
        self.work(lambda worker: scan_library(library), result, failed)

    def update_filters(self):
        for name, combo in self.filters.items():
            previous = combo.currentText()
            attr = name.lower()
            values = sorted({getattr(album, attr) for album in self.albums if getattr(album, attr)})
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(f"All {attr}s")
            combo.addItems(values)
            if previous in values:
                combo.setCurrentText(previous)
            combo.blockSignals(False)

    def render_albums(self):
        if not hasattr(self, "grid"):
            return
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        term = self.search.text().strip().casefold()
        albums = []
        for album in self.albums:
            if self.page == "Favourites" and album.key not in self.config.get("favourites", []):
                continue
            if any(combo.currentIndex() and getattr(album, name.lower()) != combo.currentText()
                   for name, combo in self.filters.items()):
                continue
            haystack = " ".join([album.title, album.artist] + [track.title + " " + track.artist for track in album.tracks])
            if term and term not in haystack.casefold():
                continue
            albums.append(album)
        order = self.sort.currentIndex()
        albums.sort(key=(lambda album: -album.modified) if order == 0 else
                    (lambda album: album.artist.casefold()) if order == 1 else
                    (lambda album: album.title.casefold()) if order == 2 else
                    (lambda album: album.year), reverse=(order == 3))
        columns = max(1, min(3, (self.width() - 470) // 280))
        for index, album in enumerate(albums):
            tile = QFrame()
            tile.setObjectName("panel")
            tile.setMinimumWidth(160)
            layout = QVBoxLayout(tile)
            layout.setContentsMargins(10, 10, 10, 10)
            art = Cover(album)
            art.mousePressEvent = lambda event, item=album: self.open_album(item)
            layout.addWidget(art)
            title = QLabel(album.title)
            title.setWordWrap(True)
            title.setFont(QFont("DejaVu Sans", 13, QFont.Bold))
            layout.addWidget(title)
            label = QLabel(album.artist)
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addWidget(button("Open album", lambda checked=False, item=album: self.open_album(item)))
            self.grid.addWidget(tile, index // columns, index % columns)
        if not albums:
            self.grid.addWidget(QLabel("No matching albums" if self.albums else "Your albums will appear here."), 0, 0)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)
        for column in range(columns, 4):
            self.grid.setColumnStretch(column, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "grid"):
            self.render_albums()

    def navigate(self, name):
        self.keyboard.hide()
        if name == "Settings":
            self.settings()
            return
        if name == "Queue":
            self.queue()
            return
        self.page = name
        self.page_title.setText("Your music" if name == "Library" else "Favourites")
        for label, control in self.nav_buttons.items():
            control.setChecked(label == name)
        self.render_albums()

    def open_album(self, album):
        dialog = QDialog(self)
        dialog.setWindowTitle(album.title)
        dialog.resize(min(1100, self.width()), 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(heading(album.title, 22))
        layout.addWidget(QLabel(f"{album.artist} · {album.year} · {len(album.tracks)} tracks"))
        tracks = QListWidget()
        for track in album.tracks:
            item = QListWidgetItem(f"{track.number:02d}   {track.title}     {clock(track.seconds)}")
            item.setSizeHint(QSize(0, 58))
            tracks.addItem(item)
        layout.addWidget(tracks, 1)
        if album.tracks:
            tracks.setCurrentRow(0)
        controls = QGridLayout()
        def play(start=0):
            self.player.play([track.path for track in album.tracks],
                             [(track.title, track.artist) for track in album.tracks], start_index=start)
            dialog.accept()
        controls.addWidget(button("Play album", lambda: play(), True), 0, 0)
        controls.addWidget(button("Play selected", lambda: play(max(0, tracks.currentRow()))), 0, 1)
        controls.addWidget(button("Add to queue", lambda: self.enqueue(album, dialog)), 0, 2)
        favourite = button("Unfavourite" if album.key in self.config.get("favourites", []) else "Favourite")
        def toggle_favourite():
            favourites = self.config.setdefault("favourites", [])
            if album.key in favourites:
                favourites.remove(album.key)
            else:
                favourites.append(album.key)
            if not self.demo:
                save_config(self.config)
            favourite.setText("Unfavourite" if album.key in favourites else "Favourite")
            self.render_albums()
        favourite.clicked.connect(toggle_favourite)
        controls.addWidget(button("Edit album", lambda: self.edit_album(album, dialog)), 1, 0)
        controls.addWidget(button("Edit track", lambda: self.edit_track(album, tracks.currentRow(), dialog)), 1, 1)
        controls.addWidget(favourite, 1, 2)
        controls.addWidget(button("Delete album", lambda: self.delete_music(album, None, dialog)), 2, 0)
        controls.addWidget(button("Delete track", lambda: self.delete_music(album, tracks.currentRow(), dialog)), 2, 1)
        controls.addWidget(button("Close", dialog.reject), 2, 2)
        layout.addLayout(controls)
        tracks.itemDoubleClicked.connect(lambda: play(max(0, tracks.currentRow())))
        dialog.exec_()

    def manage(self, operation, paths=None, after=None):
        if self.demo:
            self.message.setText("Demo mode does not edit or delete music.")
            return False
        if self.copying or self.scan_busy or self.library_busy:
            QMessageBox.information(self, "Please wait", "Finish the current copy or library update before managing music.")
            return False
        if set(paths or []) & set(self.player.sources):
            self.player.stop()
        self.library_busy = True
        config = copy.deepcopy(self.config)
        def result(message):
            self.library_busy = False
            if after:
                try:
                    after()
                except Exception as exc:
                    self.message.setText(str(exc))
            self.refresh_library()
            self.storage_status()
            QMessageBox.information(self, "Music library", message)
        def failed(message):
            self.library_busy = False
            self.error(message)
        self.work(lambda worker: operation(config), result, failed)
        return True

    def edit_album(self, album, parent):
        numbers = [track.number if 1 <= track.number <= 99 else index for index, track in enumerate(album.tracks, 1)]
        disc = Disc(numbers, [int(track.seconds * 75) for track in album.tracks],
                    [0] * len(album.tracks))
        metadata = blank_metadata(disc)
        metadata.update(album=album.title, artist=album.artist, year=album.year, genre=album.genre,
                        tracks=[{"title": track.title, "artist": track.artist} for track in album.tracks])
        editor = MetadataDialog(disc, metadata, [], self, editing=True)
        if editor.exec_() != QDialog.Accepted:
            return
        value = editor.metadata
        updates = [(track.path, {"album": value["album"], "albumartist": value["artist"],
                                "title": details["title"], "artist": details["artist"],
                                "date": value["year"], "genre": value["genre"]})
                   for track, details in zip(album.tracks, value["tracks"])]
        def update_favourite():
            favourites = self.config.get("favourites", [])
            if album.key in favourites:
                favourites.remove(album.key)
                directory = str(Path(album.tracks[0].path).parent)
                favourites.append(f"{directory}|{value['artist']}|{value['album']}")
                save_config(self.config)
        if self.manage(lambda config: edit_tags(config, updates), [track.path for track in album.tracks], update_favourite):
            parent.accept()

    def edit_track(self, album, row, parent):
        if not 0 <= row < len(album.tracks):
            QMessageBox.information(parent, "Select a track", "Choose a track to edit.")
            return
        track = album.tracks[row]
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit track")
        dialog.resize(850, 560)
        layout = QVBoxLayout(dialog)
        layout.addWidget(heading("Edit track", 24))
        layout.addWidget(QLabel("Title"))
        title = QLineEdit(track.title)
        layout.addWidget(title)
        layout.addWidget(QLabel("Track artist"))
        artist = QLineEdit(track.artist)
        layout.addWidget(artist)
        keyboard = Keyboard()
        layout.addWidget(keyboard)
        keyboard.show()
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addWidget(button("Cancel", dialog.reject))
        def save():
            if not title.text().strip() or not artist.text().strip():
                QMessageBox.warning(dialog, "Missing details", "Enter a track title and artist.")
                return
            dialog.accept()
        actions.addWidget(button("Save details", save, True))
        layout.addLayout(actions)
        title.setFocus()
        if dialog.exec_() != QDialog.Accepted:
            return
        updates = [(track.path, {"title": title.text().strip(), "artist": artist.text().strip()})]
        if self.manage(lambda config: edit_tags(config, updates), [track.path]):
            parent.accept()

    def delete_music(self, album, row, parent):
        if row is None:
            paths = [track.path for track in album.tracks]
            label = f"{album.artist} — {album.title}"
        elif 0 <= row < len(album.tracks):
            paths = [album.tracks[row].path]
            label = f"{album.tracks[row].artist} — {album.tracks[row].title}"
        else:
            QMessageBox.information(parent, "Select a track", "Choose a track to delete.")
            return
        answer = QMessageBox.question(parent, "Delete music",
                                      f"Move {label} ({len(paths)} tracks) to Trash?\n\n"
                                      "You can restore it from Settings → Trash. Permanent deletion there frees disk space.",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if self.manage(lambda config: move_to_trash(config, paths, label, include_cover=row is None), paths):
            parent.accept()

    def trash(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Trash")
        dialog.resize(1000, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(heading("Trash", 24))
        note = QLabel("Restore removed music, or permanently delete an item to free disk space.")
        note.setWordWrap(True)
        layout.addWidget(note)
        items = QListWidget()
        layout.addWidget(items, 1)
        status = QLabel("Reading Trash…")
        layout.addWidget(status)
        controls = QHBoxLayout()
        def populate(entries):
            if not dialog.isVisible():
                return
            items.clear()
            for entry in entries:
                item = QListWidgetItem(f"{entry['label']}\n{entry['count']} tracks · {entry['bytes'] / 1024**2:.1f} MiB")
                item.setSizeHint(QSize(0, 76))
                item.setData(Qt.UserRole, entry)
                items.addItem(item)
            status.setText("Select an item" if entries else "Trash is empty")
        def refresh():
            if self.demo:
                status.setText("Demo mode does not access Trash.")
                return
            config = copy.deepcopy(self.config)
            self.work(lambda worker: list_trash(config), populate,
                      lambda message: status.setText(message) if dialog.isVisible() else None)
        def act(permanent=False):
            item = items.currentItem()
            if not item:
                return
            entry = item.data(Qt.UserRole)
            if permanent:
                answer = QMessageBox.question(dialog, "Delete permanently",
                                              f"Permanently delete {entry['label']}?\n\nThis cannot be restored from Trash.",
                                              QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
            action = delete_trash_forever if permanent else restore_trash
            if self.manage(lambda config: action(config, entry["entry"])):
                dialog.accept()
        controls.addWidget(button("Restore", lambda: act(), True))
        controls.addWidget(button("Delete permanently", lambda: act(True)))
        controls.addWidget(button("Refresh", refresh))
        controls.addWidget(button("Close", dialog.reject))
        layout.addLayout(controls)
        QTimer.singleShot(0, refresh)
        dialog.exec_()

    def enqueue(self, album, dialog):
        if self.player.process.state() != QProcess.Running or self.player.from_cd:
            self.player.play([track.path for track in album.tracks], [(track.title, track.artist) for track in album.tracks])
        else:
            for track in album.tracks:
                self.player.command(["loadfile", track.path, "append"])
                self.player.sources.append(track.path)
                self.player.names.append((track.title, track.artist))
        dialog.accept()

    def queue(self):
        self.nav_buttons["Queue"].setChecked(False)
        dialog = QDialog(self)
        dialog.setWindowTitle("Queue")
        dialog.resize(800, 540)
        layout = QVBoxLayout(dialog)
        layout.addWidget(heading("Play queue", 22))
        items = QListWidget()
        for title, artist in self.player.names:
            item = QListWidgetItem(f"{title} — {artist}")
            items.addItem(item)
        layout.addWidget(items)
        def play_selected():
            if items.currentRow() >= 0:
                self.player.command(["set_property", "chapter" if self.player.from_cd else "playlist-pos", items.currentRow()])
        layout.addWidget(button("Play selected", play_selected, True))
        layout.addWidget(button("Close", dialog.reject))
        dialog.exec_()

    def poll_disc(self):
        if self.demo or self.disc_busy or self.copying:
            return
        self.disc_busy = True
        def probe(worker):
            present = drive_has_disc(self.config["device"])
            return (present, read_disc(self.config["device"]) if present and not self.presence else None)
        def detected(value):
            self.disc_busy = False
            present, disc = value
            self.presence = present
            if not present:
                self.disc = None
                self.metadata = None
                self.matches = []
                self.cd_title.setText("Insert a CD")
                self.cd_info.setText("Play it now, or save it to your library.")
            elif disc:
                self.disc = disc
                self.metadata = blank_metadata(disc)
                self.cd_title.setText("CD detected")
                self.cd_info.setText(f"{len(disc.numbers)} tracks · {clock(disc.seconds)}\nLooking up album details…")
                self.work(lambda worker: lookup_disc(disc), lambda matches: self.lookup_done(disc, matches),
                          lambda text: self.lookup_failed(disc))
            self.set_cd_buttons()
        def failed(text):
            self.disc_busy = False
            self.disc = None
            self.presence = False
            self.cd_title.setText("Check CD drive")
            self.cd_info.setText(text[-220:])
            self.set_cd_buttons()
        self.work(probe, detected, failed)

    def lookup_done(self, disc, matches):
        if self.disc is not disc or self.copying:
            return
        self.matches = matches
        if matches:
            self.metadata = copy.deepcopy(matches[0])
        self.update_cd_info()

    def lookup_failed(self, disc):
        if self.disc is disc:
            self.update_cd_info()
            self.message.setText("Album lookup unavailable. You can enter details before copying.")

    def update_cd_info(self):
        if self.disc and self.metadata:
            self.cd_info.setText(f"{self.metadata['album']}\n{self.metadata['artist']}\n\n"
                                 f"{len(self.disc.numbers)} tracks · {clock(self.disc.seconds)}")

    def set_cd_buttons(self):
        self.play_cd_button.setEnabled(bool(self.disc) and not self.copying)
        self.copy_button.setEnabled(bool(self.disc) and not self.copying)
        self.eject_button.setEnabled(not self.copying)

    def play_cd(self, start=0):
        if not self.disc:
            return
        # mpv opens the whole CD as one stream with a chapter for each track.
        # Track numbers are not URL paths: cdda://1 would mean a device named 1.
        offsets = [(offset - self.disc.offsets[0]) / 75 for offset in self.disc.offsets]
        self.player.play(["cdda://"],
                         [(track["title"], track["artist"]) for track in self.metadata["tracks"]],
                         from_cd=True, cd_offsets=offsets, start_seconds=offsets[start])

    def cd_tracks(self):
        if not self.disc or self.copying:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("CD tracks")
        dialog.resize(700, 540)
        layout = QVBoxLayout(dialog)
        layout.addWidget(heading(self.metadata["album"], 20))
        tracks = QListWidget()
        for number, details in zip(self.disc.numbers, self.metadata["tracks"]):
            tracks.addItem(f"{number:02d}   {details['title']}")
        layout.addWidget(tracks)
        def play():
            self.play_cd(max(0, tracks.currentRow()))
            dialog.accept()
        layout.addWidget(button("Play selected", play, True))
        layout.addWidget(button("Close", dialog.reject))
        dialog.exec_()

    def copy_cd(self):
        if not self.disc or self.copying or self.library_busy:
            return
        self.keyboard.hide()
        dialog = MetadataDialog(self.disc, self.metadata, self.matches, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        if self.demo:
            self.message.setText("Demo: metadata editor works; CD copying runs on your Pi.")
            return
        try:
            require_storage(self.config, create=True)
        except Exception as exc:
            self.error(str(exc))
            return
        if self.player.from_cd:
            self.player.stop()
        self.metadata = dialog.metadata
        self.update_cd_info()
        self.copying = True
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_copy.show()
        self.set_cd_buttons()
        def result(message):
            self.finish_copy()
            self.message.setText(message)
            self.refresh_library()
            QMessageBox.information(self, "Copy complete", message)
        def failed(text):
            self.finish_copy()
            self.error(text)
        config, disc, metadata = copy.deepcopy(self.config), self.disc, copy.deepcopy(self.metadata)
        self.copy_job = self.work(lambda worker: rip_album(config, disc, metadata, worker.progress.emit, worker.cancel),
                                  result, failed)
        self.copy_job.progress.connect(self.copy_progress)

    def copy_progress(self, value, message):
        self.progress.setValue(value)
        self.cd_title.setText("Copying CD")
        self.cd_info.setText(message)

    def finish_copy(self):
        self.copying = False
        self.copy_job = None
        self.progress.hide()
        self.cancel_copy.hide()
        self.cd_title.setText("CD detected")
        self.update_cd_info()
        self.set_cd_buttons()
        self.presence = False
        self.poll_disc()

    def cancel_rip(self):
        if self.copy_job:
            self.copy_job.cancel.set()
            self.cd_info.setText("Cancelling safely…")

    def eject(self):
        if self.demo:
            self.message.setText("Demo: eject runs on your Pi.")
            return
        if self.player.from_cd:
            self.player.stop()
        self.work(lambda worker: run_checked(["eject", self.config["device"]], timeout=15),
                  lambda result: self.poll_disc())

    def settings(self):
        self.nav_buttons["Settings"].setChecked(False)
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(900, 620)
        outer = QVBoxLayout(dialog)
        outer.addWidget(heading("Settings", 24))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        layout.addWidget(QLabel("Music drive mount folder"))
        mount = QLineEdit(self.config["mount"])
        layout.addWidget(mount)
        layout.addWidget(button("Choose mounted drive", lambda: self.choose_mount(mount, dialog)))
        layout.addWidget(QLabel("Audio output"))
        audio = QComboBox()
        audio.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        audio.setMinimumContentsLength(30)
        audio.setMaxVisibleItems(10)
        audio.addItem("Detecting audio outputs…", None)
        layout.addWidget(audio)
        audio_status = QLabel("Connect your Ugreen DAC, then choose its USB audio output.")
        audio_status.setWordWrap(True)
        layout.addWidget(audio_status)
        def populate_outputs(outputs):
            if not dialog.isVisible():
                return
            previous = audio.currentData() or self.config.get("audio_device")
            audio.clear()
            audio.addItem("Choose an audio output", None)
            for entry in outputs:
                audio.addItem(entry.label, entry.name)
                audio.setItemData(audio.count() - 1, entry.name, Qt.ToolTipRole)
            selected = audio.findData(previous)
            audio.setCurrentIndex(selected if selected >= 0 else 0)
            audio_status.setText("Choose the entry describing your USB DAC. A desktop audio entry uses the desktop mixer."
                                 if selected >= 0 or not previous else
                                 "The saved output is not currently listed. Choose your USB DAC from the detected outputs.")
        def detection_error(message):
            if dialog.isVisible():
                audio.clear()
                audio.addItem("No outputs detected — refresh to retry", None)
                audio_status.setText(message)
        def refresh_outputs():
            audio_status.setText("Checking connected audio outputs…")
            if self.demo:
                audio.clear()
                audio.addItem("USB DAC · design preview", "auto")
                audio_status.setText("Demo mode does not query audio hardware.")
                return
            self.work(lambda worker: discover_outputs(), populate_outputs, detection_error)
        layout.addWidget(button("Refresh audio outputs", refresh_outputs))
        fullscreen = QCheckBox("Full-screen interface")
        fullscreen.setChecked(self.isFullScreen())
        layout.addWidget(fullscreen)
        layout.addWidget(button("Trash · restore or delete", self.trash))
        layout.addWidget(button("Software updates", self.software_updates))
        layout.addWidget(heading("Pi power", 20))
        power_actions = QHBoxLayout()
        power_actions.addWidget(button("Restart Pi", lambda: self.power_action("reboot", dialog)))
        power_actions.addWidget(button("Shut Down", lambda: self.power_action("poweroff", dialog)))
        layout.addLayout(power_actions)
        info = QLabel("Albums are saved inside DiscRoom/Music on the selected drive.\n"
                      "After shutdown, wait for the Pi to finish shutting down before switching off its power.")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch()
        actions = QHBoxLayout()
        def save():
            if self.copying or self.scan_busy or self.library_busy:
                QMessageBox.warning(dialog, "Please wait", "Finish copying or scanning before changing settings.")
                return
            candidate = copy.deepcopy(self.config)
            candidate["mount"] = mount.text().strip()
            candidate["library"] = str(Path(candidate["mount"]) / "DiscRoom/Music")
            candidate["audio_device"] = audio.currentData()
            candidate["fullscreen"] = fullscreen.isChecked()
            if not candidate["audio_device"]:
                QMessageBox.warning(dialog, "Audio output", "Choose a connected audio output before saving.")
                return
            if not self.demo:
                try:
                    require_storage(candidate, create=True)
                    save_config(candidate)
                except Exception as exc:
                    QMessageBox.warning(dialog, "Settings", str(exc))
                    return
            self.player.stop()
            self.config.clear()
            self.config.update(candidate)
            self.showFullScreen() if fullscreen.isChecked() else self.showNormal()
            self.refresh_library()
            self.storage_status()
            dialog.accept()
        def close_app():
            dialog.reject()
            self.close()
        actions.addWidget(button("Close app", close_app))
        actions.addWidget(button("Cancel", dialog.reject))
        actions.addWidget(button("Save", save, True))
        outer.addLayout(actions)
        QTimer.singleShot(0, refresh_outputs)
        dialog.exec_()

    def power_action(self, action, dialog):
        if action not in ("reboot", "poweroff"):
            raise ValueError("Unknown Pi power action.")
        if self.demo:
            self.message.setText("Demo: Pi restart and shutdown are disabled.")
            return
        if self.power_pending:
            return
        title = "Restart Pi" if action == "reboot" else "Shut Down"
        if self.copying or self.library_busy:
            QMessageBox.information(dialog, "Please wait", "Finish copying or editing your music before restarting or shutting down.")
            return
        text = ("Restart the Pi now? Disc Room will open again after it boots."
                if action == "reboot" else
                "Shut down the Pi now? Wait for shutdown to finish before switching off the power. "
                "Switch the power off and on to start it again.")
        if QMessageBox.question(dialog, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        # A confirmation opens a nested event loop; check again before making
        # the app unavailable in case a queued library operation has started.
        if self.copying or self.library_busy:
            QMessageBox.information(dialog, "Please wait", "Finish copying or editing your music first.")
            return
        self.power_pending = True
        self.setEnabled(False)
        self.timer.stop()
        self.storage_timer.stop()
        self.message.setText("Finishing background checks…")

        def failed(message):
            self.power_pending = False
            self.setEnabled(True)
            self.timer.start()
            self.storage_timer.start()
            self.error(message)

        def accepted(message):
            self.message.setText(message)
            # Let the command worker finish before Qt destroys its threads.
            def finish():
                if self.jobs:
                    QTimer.singleShot(200, finish)
                    return
                QApplication.instance().exit(0)
            finish()

        def prepare():
            if self.jobs:
                QTimer.singleShot(200, prepare)
                return
            try:
                self.config["fullscreen"] = self.isFullScreen()
                save_config(self.config)
            except Exception as exc:
                failed(str(exc))
                return
            self.player.stop()
            self.message.setText("Restarting Pi…" if action == "reboot" else "Shutting down Pi…")
            self.work(lambda worker: request_power(action), accepted, failed)
        prepare()

    def software_updates(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Software updates")
        dialog.resize(1000, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(heading("Software updates", 24))
        layout.addWidget(QLabel(f"Running version {VERSION}"))
        store = UpdateStore()
        layout.addWidget(QLabel("Git repository clone URL"))
        repository = QLineEdit(store.config().get("repository", ""))
        repository.setPlaceholderText("git@github.com:YOURNAME/discroom.git")
        layout.addWidget(repository)
        explanation = QLabel("Updates use numbered release tags. Private repositories need Git authentication configured on the Pi.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        status = QLabel("Check for a release, or use Update app to install a newer release and restart.")
        status.setWordWrap(True)
        layout.addWidget(status, 1)
        actions = QGridLayout()
        check_button = button("Check for updates")
        update_button = button("Update app", primary=True)
        restart_button = button("Restart app")
        rollback_button = button("Roll back update")
        close_button = button("Close", dialog.reject)
        controls = [check_button, update_button, restart_button, rollback_button, close_button]
        def busy(value):
            repository.setEnabled(not value)
            for control in controls:
                control.setEnabled(not value)
        def failed(message):
            busy(False)
            status.setText(message)
        def restart():
            if self.power_pending:
                return
            if self.copying or self.library_busy:
                status.setText("Finish the current copy or music-library change before restarting.")
                return
            busy(True)
            status.setText("Finishing background checks, then restarting the app…")
            self.timer.stop()
            self.storage_timer.stop()
            def finish():
                if self.jobs:
                    QTimer.singleShot(200, finish)
                    return
                self.config["fullscreen"] = self.isFullScreen()
                try:
                    save_config(self.config)
                except Exception as exc:
                    failed(str(exc))
                    self.timer.start()
                    self.storage_timer.start()
                    return
                RESTART_FLAGS[:] = ["--fullscreen"] if self.isFullScreen() else []
                QApplication.instance().exit(RESTART_CODE)
            finish()
        def install(release):
            status.setText(f"Installing {release['tag']} and checking its files…")
            def installed(message):
                status.setText(message)
                restart()
            self.work(lambda worker: store.install(release, VERSION), installed, failed)
        def check(also_install=False):
            if self.demo:
                status.setText("Updates are disabled in demo mode.")
                return
            if self.copying or self.library_busy:
                status.setText("Finish the current CD copy or music-library change before updating.")
                return
            try:
                store.configure(repository.text())
            except Exception as exc:
                failed(str(exc))
                return
            busy(True)
            status.setText("Checking Git for numbered releases…")
            def checked(release):
                if release["available"] and also_install:
                    install(release)
                else:
                    busy(False)
                    status.setText(f"{release['tag']} is available. Press Update app to install it."
                                   if release["available"] else "Your app is up to date.")
            self.work(lambda worker: store.check(VERSION), checked, failed)
        def rollback():
            if self.demo or self.copying or self.library_busy:
                status.setText("Finish copying or managing music before rolling back.")
                return
            try:
                message = store.rollback()
                status.setText(message)
                restart()
            except Exception as exc:
                failed(str(exc))
        check_button.clicked.connect(lambda: check())
        update_button.clicked.connect(lambda: check(True))
        restart_button.clicked.connect(restart)
        rollback_button.clicked.connect(rollback)
        actions.addWidget(check_button, 0, 0)
        actions.addWidget(update_button, 0, 1)
        actions.addWidget(restart_button, 1, 0)
        actions.addWidget(rollback_button, 1, 1)
        actions.addWidget(close_button, 2, 0, 1, 2)
        layout.addLayout(actions)
        dialog.exec_()

    def choose_mount(self, field, dialog):
        folder = QFileDialog.getExistingDirectory(dialog, "Choose the drive's mount folder", "/media")
        if folder:
            field.setText(folder)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.keyboard.hide()
            if self.isFullScreen():
                self.showNormal()
        elif event.key() == Qt.Key_Q and event.modifiers() & Qt.ControlModifier:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.power_pending:
            event.ignore()
            return
        if self.jobs:
            if self.copy_job:
                self.copy_job.cancel.set()
                self.message.setText("Cancelling copy safely. Close the app again when cancellation finishes.")
            else:
                self.message.setText("Please wait for the current drive check or lookup to finish, then close again.")
            event.ignore()
            return
        self.timer.stop()
        self.storage_timer.stop()
        self.player.shutdown()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="Disc Room touchscreen music library")
    parser.add_argument("--mount", help="Existing HDD/SSD mount folder; creates only DiscRoom/Music inside it")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Preview layout without any hardware or storage writes")
    parser.add_argument("--screenshot", help="Save a demo screenshot and exit (requires --demo)")
    parser.add_argument("--ready-file", help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    config = load_config() if not args.demo else __import__('core').default_config()
    if args.mount:
        config["mount"] = args.mount
        config["library"] = str(Path(args.mount) / "DiscRoom/Music")
        if not args.demo:
            try:
                require_storage(config, create=True)
                save_config(config)
            except Exception as exc:
                QMessageBox.critical(None, "Music drive", str(exc))
                return 1
    window = MainWindow(config, args.demo)
    app.aboutToQuit.connect(window.player.shutdown)
    window.showFullScreen() if args.fullscreen or config.get("fullscreen", False) else window.show()
    if args.ready_file:
        marker = Path(args.ready_file)
        allowed = (UpdateStore().root / "run").resolve()
        if marker.resolve().parent != allowed:
            parser.error("Invalid startup readiness file")
        QTimer.singleShot(1500, lambda: marker.write_text("ready"))
    if args.screenshot:
        if not args.demo:
            parser.error("--screenshot requires --demo")
        def capture():
            window.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(900, capture)
    return app.exec_()


if __name__ == "__main__":
    # Old launch commands keep working after the first managed update. A Git
    # checkout remains a development entrypoint, and demo previews stay local.
    source_directory = Path(__file__).resolve().parent
    if not os.environ.get("DISCROOM_LAUNCHER") and "--demo" not in sys.argv and not (source_directory / ".git").exists():
        state = UpdateStore().state()
        if state["pending"] or state["current"]:
            os.execv(sys.executable, [sys.executable, str(source_directory / "launch.py"), *sys.argv[1:]])
    result = main()
    if result == RESTART_CODE and not os.environ.get("DISCROOM_LAUNCHER"):
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().parent / "launch.py"), *RESTART_FLAGS])
    sys.exit(result)

#!/usr/bin/env python3
"""Capture real Qt widgets in demo mode for the README; no hardware is used."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots")
    parser.add_argument("--font-file", type=Path, action="append", default=[],
                        help="Load a font explicitly when the offscreen platform has no system fonts")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtCore import QEvent, QTimer, Qt
    from PyQt5.QtGui import QFontDatabase
    from PyQt5.QtWidgets import QApplication, QDialog, QScrollArea
    from core import default_config, Track
    from discroom import MainWindow, STYLE

    app = QApplication([sys.argv[0]])
    for font in args.font_file:
        if QFontDatabase.addApplicationFont(str(font)) < 0:
            raise RuntimeError(f"Could not load font {font}.")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    class CaptureWindow(MainWindow):
        def seed_demo(self):
            # Populate after the initial show/resize events, as the real
            # background library scan does, so the grid has settled first.
            pass

    window = CaptureWindow(default_config(), demo=True)
    window.resize(1280, 720)

    # Fictional tracks provide representative detail screens without files.
    titles = ["First Light", "Blue Hour", "Rain on Glass", "Crossing Town",
              "Still Water", "Quiet Streets", "Soft Signals", "The Way Home",
              "Window Seat", "Night Train", "Low Tide", "Afterglow"]
    window.show()
    errors = []

    def save(widget, name):
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents()
        output = args.output / name
        if not widget.grab().save(str(output)):
            raise RuntimeError(f"Could not save {output}.")
        print(f"Saved {output}")

    def capture_dialog(open_dialog, name, scroll_to_bottom=False):
        def capture():
            dialog = QApplication.activeModalWidget()
            try:
                if not isinstance(dialog, QDialog):
                    raise RuntimeError(f"The {name} dialog did not open.")
                # Screens show the details form before the keyboard is opened.
                if hasattr(dialog, "keyboard"):
                    dialog.keyboard.hide()
                dialog.setFocus(Qt.OtherFocusReason)
                QApplication.processEvents()
                if scroll_to_bottom:
                    for area in dialog.findChildren(QScrollArea):
                        bar = area.verticalScrollBar()
                        bar.setValue(bar.maximum())
                save(dialog, name)
            except Exception as exc:
                errors.append(exc)
            finally:
                if isinstance(dialog, QDialog):
                    dialog.reject()
                else:
                    app.exit(1)
        QTimer.singleShot(400, capture)
        open_dialog()
        if errors:
            raise errors[0]

    def capture_all():
        try:
            save(window, "library.png")
            capture_dialog(lambda: window.open_album(window.demo_album), "album.png")
            capture_dialog(window.copy_cd, "copy-details.png")
            capture_dialog(window.settings, "settings.png", scroll_to_bottom=True)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            app.exit(1)
        else:
            app.exit(0)

    def prepare_demo():
        MainWindow.seed_demo(window)
        album = next(item for item in window.albums if item.title == "Blue Hour")
        window.demo_album = album
        album.tracks = [Track(f"demo/{number}.flac", title, album.artist, number, 240, 24_000_000 + number * 350_000)
                        for number, title in enumerate(titles, 1)]
        window.metadata.update(album=album.title, artist=album.artist, year=album.year,
                               genre=album.genre, country="", discnumber="1")
        window.metadata["tracks"] = [{"title": title, "artist": album.artist} for title in titles]
        window.sort.setCurrentIndex(1)
        QTimer.singleShot(400, capture_all)

    QTimer.singleShot(200, prepare_demo)
    try:
        return app.exec_()
    finally:
        window.player.shutdown()


if __name__ == "__main__":
    sys.exit(main())

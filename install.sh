#!/bin/sh
# Run from the extracted discroom folder as the normal Pi desktop user.
set -eu
if [ "$(id -u)" = "0" ]; then
    echo "Run ./install.sh as your normal user, without sudo."
    exit 1
fi
sudo apt update
sudo apt install python3-pyqt5 python3-mutagen python3-requests mpv cdparanoia flac eject git
python3 -c "from PyQt5 import QtWidgets; import mutagen.flac, requests; print('Disc Room dependencies ready')"
echo "Installation complete. See README.md for the launch command."

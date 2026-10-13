"""Hardware and storage operations for Disc Room. No desktop configuration changes."""
from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

import requests
from mutagen.flac import FLAC

VERSION = "0.3.1"
USER_AGENT = f"DiscRoom/{VERSION} (personal Raspberry Pi CD library)"
CONFIG_PATH = Path.home() / ".config/discroom/config.json"


@dataclass
class Disc:
    numbers: list[int]
    lengths: list[int]
    offsets: list[int]

    @property
    def signature(self):
        return (tuple(self.numbers), tuple(self.lengths), tuple(self.offsets))

    @property
    def seconds(self):
        return sum(self.lengths) / 75

    @property
    def disc_id(self):
        offsets = [0] * 99
        for number, offset in zip(self.numbers, self.offsets):
            offsets[number - 1] = offset + 150
        leadout = self.offsets[-1] + self.lengths[-1] + 150
        toc = f"{self.numbers[0]:02X}{self.numbers[-1]:02X}{leadout:08X}"
        toc += "".join(f"{offset:08X}" for offset in offsets)
        return base64.b64encode(hashlib.sha1(toc.encode("ascii")).digest()).decode().translate(
            str.maketrans("+/=", "._-"))


def parse_toc(output: str) -> Disc:
    rows = re.findall(r"^\s*(\d+)\.\s+(\d+)\s+\[[\d:.]+\]\s+(\d+)\s+\[[\d:.]+\]", output, re.M)
    if not rows:
        raise RuntimeError("No audio tracks could be read. Check the disc and drive.")
    numbers, lengths, offsets = zip(*[(int(n), int(length), int(offset)) for n, length, offset in rows])
    if any(n < 1 or n > 99 for n in numbers):
        raise RuntimeError("Invalid CD track numbers.")
    return Disc(list(numbers), list(lengths), list(offsets))


def read_disc(device="/dev/sr0"):
    result = subprocess.run(["cdparanoia", "-d", device, "-Q"], capture_output=True,
                            text=True, timeout=25)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1200:] or "Cannot read this CD.")
    return parse_toc(result.stdout + "\n" + result.stderr)


def drive_has_disc(device):
    """Query drive status without ripping, using Linux's CDROM ioctl."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOMEDIUM, errno.ENODEV):
            return False
        raise
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        status = libc.ioctl(fd, 0x5326, 0x7FFFFFFF)  # CDROM_DRIVE_STATUS, CDSL_CURRENT
        if status < 0:
            error = ctypes.get_errno()
            if error in (errno.ENOMEDIUM, errno.ENODEV):
                return False
            raise OSError(error, os.strerror(error))
        return status == 4  # CDS_DISC_OK
    finally:
        os.close(fd)


def default_config():
    mount = Path("/media") / Path.home().name / "DA265AA768731CC7"
    return {"mount": str(mount), "library": str(mount / "DiscRoom/Music"),
            "device": "/dev/sr0", "audio_device": "",
            "favourites": [], "volume": 40}


def load_config():
    config = default_config()
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text()))
    return config


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=CONFIG_PATH.parent, prefix="config-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_storage(config, create=False):
    mount = Path(config["mount"]).resolve()
    library = Path(config["library"]).resolve()
    if not os.path.ismount(mount):
        raise RuntimeError("Music drive is not mounted. Connect it and open it in File Manager.")
    if library == mount or mount not in library.parents:
        raise RuntimeError("Music folder must be inside the selected music drive.")
    if create:
        library.mkdir(parents=True, exist_ok=True)
    if not library.is_dir():
        raise RuntimeError("Music folder has not been created yet.")
    if not os.access(library, os.W_OK):
        raise RuntimeError("Music folder is not writable. The drive may be mounted read-only.")
    return library


def safe_name(value):
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", value).strip(" .")[:110]
    return value or "Unknown"


def run_checked(args, cancel=None, timeout=900):
    """Capture diagnostics without filling a pipe or allowing a stuck drive to run forever."""
    cancel = cancel or threading.Event()
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, stdout=log, stderr=log)
        start = time.monotonic()
        problem = None
        while process.poll() is None:
            if cancel.is_set():
                problem = "Copy cancelled. The album was not added to your library."
                break
            if time.monotonic() - start > timeout:
                problem = "The drive stopped responding. Check the disc and retry."
                break
            time.sleep(0.1)
        if problem:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise RuntimeError(problem)
        log.seek(0)
        output = log.read().decode("utf-8", "replace")
        if process.returncode:
            raise RuntimeError(output[-1800:] or f"{args[0]} failed.")
        return output


def artist_credit(credits):
    return "".join(item.get("name", item.get("artist", {}).get("name", "")) + item.get("joinphrase", "")
                   for item in credits or [])


def lookup_disc(disc):
    response = requests.get(f"https://musicbrainz.org/ws/2/discid/{disc.disc_id}",
                            params={"inc": "artists+recordings", "fmt": "json"},
                            headers={"User-Agent": USER_AGENT}, timeout=(5, 15))
    if response.status_code == 404:
        return []
    response.raise_for_status()
    matches = []
    for release in response.json().get("releases", []):
        for medium in release.get("media", []):
            if not any(item.get("id") == disc.disc_id for item in medium.get("discs", [])):
                continue
            tracks = medium.get("tracks", [])
            if len(tracks) != len(disc.numbers):
                continue
            artist = artist_credit(release.get("artist-credit")) or "Unknown artist"
            matches.append({"album": release.get("title", "Unknown album"), "artist": artist,
                            "year": release.get("date", "")[:4], "genre": "",
                            "discnumber": str(medium.get("position", 1)),
                            "release_id": release["id"], "country": release.get("country", ""),
                            "tracks": [{"title": t.get("title") or t.get("recording", {}).get("title") or f"Track {n}",
                                        "artist": artist_credit(t.get("artist-credit") or t.get("recording", {}).get("artist-credit")) or artist}
                                       for n, t in zip(disc.numbers, tracks)]})
    return matches


def blank_metadata(disc):
    return {"album": "Unknown album", "artist": "Unknown artist", "year": "", "genre": "",
            "discnumber": "1", "release_id": "", "country": "",
            "tracks": [{"title": f"Track {number}", "artist": "Unknown artist"} for number in disc.numbers]}


def fetch_cover(release_id):
    if not re.fullmatch(r"[0-9a-f-]{36}", release_id or ""):
        return b""
    try:
        with requests.get(f"https://coverartarchive.org/release/{release_id}/front-250",
                          headers={"User-Agent": USER_AGENT}, timeout=(4, 8), stream=True) as response:
            if response.status_code != 200:
                return b""
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > 4 * 1024 * 1024:
                    return b""
            return bytes(content)
    except requests.RequestException:
        return b""


def rip_album(config, disc, metadata, progress, cancel):
    library = require_storage(config, create=True)
    album_root = library / safe_name(metadata["artist"]) / safe_name(metadata["album"])
    destination = album_root / f"Disc {safe_name(metadata.get('discnumber', '1'))}"
    if destination.exists():
        raise RuntimeError("This album and disc already exist. Edit the album name for another edition.")
    required = int(disc.seconds * 44100 * 4 + 256 * 1024 * 1024)
    if shutil.disk_usage(library).free < required:
        raise RuntimeError("Not enough free space to copy this disc.")
    if len(metadata.get("tracks", [])) != len(disc.numbers):
        raise RuntimeError("Track details no longer match the inserted disc.")
    staging_root = library / ".copying"
    staging_root.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="disc-", dir=staging_root))
    published = False
    try:
        for index, (number, details) in enumerate(zip(disc.numbers, metadata["tracks"]), 1):
            if cancel.is_set():
                raise RuntimeError("Copy cancelled.")
            require_storage(config)
            if read_disc(config["device"]).signature != disc.signature:
                raise RuntimeError("The CD changed during copying. Reinsert the original disc and retry.")
            progress(int((index - 1) * 95 / len(disc.numbers)), f"Reading track {index} of {len(disc.numbers)}")
            wav = staging / "current-track.wav"
            output = staging / f"{number:02d} - {safe_name(details['title'])}.flac"
            run_checked(["cdparanoia", "--abort-on-skip", "-d", config["device"], str(number), str(wav)], cancel)
            require_storage(config)
            progress(int((index - 0.35) * 95 / len(disc.numbers)), f"Saving track {index} of {len(disc.numbers)}")
            run_checked(["flac", "--verify", "--silent", "-o", str(output), str(wav)], cancel, timeout=180)
            audio = FLAC(output)
            audio["title"] = details["title"]
            audio["artist"] = details["artist"] or metadata["artist"]
            audio["albumartist"] = metadata["artist"]
            audio["album"] = metadata["album"]
            audio["tracknumber"] = str(number)
            audio["tracktotal"] = str(len(disc.numbers))
            audio["discnumber"] = metadata.get("discnumber", "1")
            audio["date"] = metadata.get("year", "")
            audio["genre"] = metadata.get("genre", "")
            if metadata.get("release_id"):
                audio["musicbrainz_albumid"] = metadata["release_id"]
            audio.save()
            wav.unlink()
            with output.open("rb") as stream:
                os.fsync(stream.fileno())
        progress(96, "Finishing album…")
        if cancel.is_set():
            raise RuntimeError("Copy cancelled.")
        cover = fetch_cover(metadata.get("release_id", ""))
        require_storage(config)
        if cancel.is_set():
            raise RuntimeError("Copy cancelled.")
        if cover:
            cover_path = staging / "cover.jpg"
            cover_path.write_bytes(cover)
            with cover_path.open("rb") as stream:
                os.fsync(stream.fileno())
        album_root.mkdir(parents=True, exist_ok=True)
        require_storage(config)
        # Publish only the completed directory; reserve the destination exclusively.
        publish_directory(staging, destination)
        published = True
        # Flush the filesystem before announcing success or opening the tray.
        sync_filesystem(destination)
        progress(100, "Album saved. Opening CD tray…")
        try:
            run_checked(["eject", config["device"]], timeout=15)
            return "Album saved successfully. CD ejected."
        except (RuntimeError, OSError) as exc:
            return f"Album saved successfully. Eject manually: {exc}"
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)


def publish_directory(source, target):
    # A portable exclusive reservation also works on NTFS/FUSE filesystems that
    # do not implement renameat2(RENAME_NOREPLACE). Concurrent app instances see
    # the reservation and refuse the duplicate. The final rename is atomic.
    target = Path(target)
    try:
        target.mkdir()
    except FileExistsError:
        raise RuntimeError("Album already exists. The existing album was preserved.") from None
    try:
        os.rename(source, target)
    except Exception:
        try:
            target.rmdir()  # only the empty reservation; never remove its contents
        except OSError:
            pass
        raise


def sync_filesystem(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.syncfs(fd):
            error = ctypes.get_errno()
            raise OSError(error, "Could not flush music storage; check the drive before ejecting.")
    finally:
        os.close(fd)


@dataclass
class Track:
    path: str
    title: str
    artist: str
    number: int
    seconds: float


@dataclass
class Album:
    key: str
    title: str
    artist: str
    year: str = ""
    genre: str = ""
    cover: bytes = b""
    modified: float = 0
    tracks: list[Track] = field(default_factory=list)


def scan_library(library):
    albums = {}
    skipped = 0
    for directory, folders, files in os.walk(library):
        folders[:] = [name for name in folders if not name.startswith(".")]
        for name in sorted(files):
            if not name.lower().endswith(".flac"):
                continue
            path = Path(directory) / name
            try:
                audio = FLAC(path)
                get = lambda tag, fallback="": audio.get(tag, [fallback])[0]
                title = get("album", path.parent.name)
                artist = get("albumartist", get("artist", "Unknown artist"))
                key = f"{directory}|{artist}|{title}"
                if key not in albums:
                    cover = audio.pictures[0].data if audio.pictures else b""
                    if not cover:
                        for cover_name in ("cover.jpg", "cover.png", "folder.jpg"):
                            cover_path = path.parent / cover_name
                            if cover_path.is_file() and cover_path.stat().st_size < 4 * 1024 * 1024:
                                cover = cover_path.read_bytes()
                                break
                    albums[key] = Album(key, title, artist, get("date")[:4], get("genre"), cover)
                number_text = get("tracknumber", "0").split("/")[0]
                number = int(number_text) if number_text.isdigit() else 0
                albums[key].tracks.append(Track(str(path), get("title", path.stem), get("artist", artist),
                                               number, audio.info.length))
                albums[key].modified = max(albums[key].modified, path.stat().st_mtime)
            except Exception:
                skipped += 1
    for album in albums.values():
        album.tracks.sort(key=lambda track: (track.number, track.title))
    return list(albums.values()), skipped

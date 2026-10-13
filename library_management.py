"""Restrict editing and Trash operations to the application's mounted music folder."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from mutagen.flac import FLAC
from core import require_storage, sync_filesystem


def _track(library, path):
    path = Path(path)
    resolved = path.resolve()
    relative = resolved.relative_to(library)
    if path.is_symlink() or any(part.startswith(".") for part in relative.parts) or resolved.suffix.lower() != ".flac":
        raise RuntimeError("Only music files inside the library can be managed.")
    if not resolved.is_file():
        raise RuntimeError("A selected track is no longer available. Refresh the library.")
    return resolved


def _work_folder(library, name):
    path = library / name
    if path.is_symlink():
        raise RuntimeError("The library work folder cannot be a symbolic link.")
    path.mkdir(exist_ok=True)
    return path


def edit_tags(config, updates):
    """Prepare all tag edits first; retain originals until the commit completes."""
    library = require_storage(config)
    updates = [(_track(library, path), tags) for path, tags in updates]
    if len({path for path, tags in updates}) != len(updates):
        raise RuntimeError("A track was selected more than once.")
    staging = Path(tempfile.mkdtemp(prefix="tags-", dir=_work_folder(library, ".editing")))
    committed = []
    cleanup = True
    try:
        versions = []
        for index, (path, tags) in enumerate(updates):
            require_storage(config)
            stat = path.stat()
            versions.append((stat.st_size, stat.st_mtime_ns))
            backup, edited = staging / f"{index}.original.flac", staging / f"{index}.edited.flac"
            shutil.copy2(path, backup)
            shutil.copy2(path, edited)
            audio = FLAC(edited)
            for key, value in tags.items():
                audio[key] = str(value)
            audio.save()
            for prepared in (backup, edited):
                with prepared.open("rb") as stream:
                    os.fsync(stream.fileno())
        sync_filesystem(staging)
        for index, (path, tags) in enumerate(updates):
            require_storage(config)
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != versions[index]:
                raise RuntimeError("A track changed during editing. Refresh and try again.")
            os.replace(staging / f"{index}.edited.flac", path)
            committed.append((index, path))
        sync_filesystem(library)
    except Exception as original:
        try:
            for index, path in reversed(committed):
                require_storage(config)
                os.replace(staging / f"{index}.original.flac", path)
        except Exception:
            cleanup = False
            raise RuntimeError(f"Storage disconnected during an edit. Original backups are in {staging}.") from original
        raise
    finally:
        if cleanup and staging.exists():
            shutil.rmtree(staging)
    return "Music details updated."


def _manifest(entry, record):
    temporary = entry / "manifest.tmp"
    with temporary.open("w") as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, entry / "manifest.json")


def move_to_trash(config, paths, label, include_cover=False):
    library = require_storage(config)
    paths = [_track(library, path) for path in paths]
    if not paths or len(set(paths)) != len(paths):
        raise RuntimeError("Choose distinct tracks to delete.")
    if include_cover:
        selected = set(paths)
        for directory in {path.parent for path in paths}:
            all_flacs = {path.resolve() for path in directory.iterdir() if path.suffix.lower() == ".flac"}
            if all_flacs <= selected:
                for name in ("cover.jpg", "cover.png", "folder.jpg"):
                    cover = directory / name
                    if cover.is_file() and not cover.is_symlink():
                        paths.append(cover)
    entry = _work_folder(library, ".trash") / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    entry.mkdir()
    data = entry / "data"
    data.mkdir()
    record = {"label": label, "created": time.time(), "state": "pending",
              "files": [{"original": str(path.relative_to(library)), "stored": f"data/{index:03d}{path.suffix.lower()}"}
                        for index, path in enumerate(paths)]}
    moved = []
    try:
        _manifest(entry, record)
        for path, item in zip(paths, record["files"]):
            require_storage(config)
            os.rename(path, entry / item["stored"])
            moved.append((path, entry / item["stored"]))
        record["state"] = "complete"
        _manifest(entry, record)
        for directory in {path.parent for path in paths}:
            while directory != library and library in directory.parents:
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent
        sync_filesystem(library)
    except Exception as original:
        try:
            for path, stored in reversed(moved):
                require_storage(config)
                if path.exists():
                    raise RuntimeError("Restore path already exists.")
                path.parent.mkdir(parents=True, exist_ok=True)
                os.rename(stored, path)
        except Exception:
            raise RuntimeError(f"Storage interrupted the deletion. Recoverable files remain in {entry}.") from original
        shutil.rmtree(entry)
        raise
    return "Moved to Trash. Restore it in Settings → Trash, or delete it permanently to free space."


def _read_entry(library, entry):
    trash = _work_folder(library, ".trash").resolve()
    entry = Path(entry)
    if entry.is_symlink() or entry.resolve().parent != trash:
        raise RuntimeError("This is not an application Trash entry.")
    entry = entry.resolve()
    record = json.loads((entry / "manifest.json").read_text())
    for item in record["files"]:
        original = library / item["original"]
        stored = entry / item["stored"]
        relative = original.resolve().relative_to(library)
        cover = original.name in ("cover.jpg", "cover.png", "folder.jpg")
        if any(part.startswith(".") for part in relative.parts) or (original.suffix.lower() != ".flac" and not cover):
            raise RuntimeError("Invalid original track path in Trash.")
        if stored.is_symlink() or entry not in stored.resolve().parents:
            raise RuntimeError("Invalid stored track path in Trash.")
    return entry, record


def list_trash(config):
    library = require_storage(config)
    trash = _work_folder(library, ".trash")
    items = []
    for entry in trash.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            try:
                entry, record = _read_entry(library, entry)
                files = [entry / item["stored"] for item in record["files"] if (entry / item["stored"]).is_file()]
                items.append({"entry": str(entry), "label": record.get("label", entry.name),
                              "created": record.get("created", 0), "count": sum(path.suffix.lower() == ".flac" for path in files),
                              "bytes": sum(path.stat().st_size for path in files)})
            except (OSError, ValueError, KeyError, RuntimeError, TypeError):
                continue
    return sorted(items, key=lambda item: item["created"], reverse=True)


def restore_trash(config, entry):
    library = require_storage(config)
    entry, record = _read_entry(library, entry)
    files = [(entry / item["stored"], library / item["original"]) for item in record["files"]
             if (entry / item["stored"]).is_file()]
    if any(original.exists() for stored, original in files):
        raise RuntimeError("A track already exists at the restore location. Nothing was overwritten.")
    moved = []
    try:
        for stored, original in files:
            require_storage(config)
            original.parent.mkdir(parents=True, exist_ok=True)
            _restore_file(stored, original)
            moved.append((stored, original))
        sync_filesystem(library)
    except Exception as original_error:
        for stored, original in reversed(moved):
            try:
                require_storage(config)
                os.rename(original, stored)
            except Exception:
                raise RuntimeError("Storage interrupted restoration. Reconnect the drive and inspect the Trash entry.") from original_error
        raise
    shutil.rmtree(entry)
    return "Music restored to the library."


def _restore_file(stored, original):
    # An exclusive empty reservation prevents races with another restore/copy.
    # Replace only the reservation this operation created, never existing music.
    try:
        with original.open("xb"):
            pass
    except FileExistsError:
        raise RuntimeError("A track already exists at the restore location. Nothing was overwritten.") from None
    try:
        os.replace(stored, original)
    except Exception:
        if original.exists() and original.stat().st_size == 0:
            original.unlink()
        raise


def delete_trash_forever(config, entry):
    library = require_storage(config)
    entry, record = _read_entry(library, entry)
    shutil.rmtree(entry)
    sync_filesystem(library)
    return "Trash item permanently deleted."

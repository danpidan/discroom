"""Install immutable Git release tags beside the running app, never into music storage."""
import ast
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
import uuid

RESTART_CODE = 75
DEFAULT_REPOSITORY = "https://github.com/danpidan/discroom.git"
VERSION_PATTERN = re.compile(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})\Z")
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def version(value):
    match = VERSION_PATTERN.fullmatch(value or "")
    return tuple(map(int, match.groups())) if match else None


def application_version(directory):
    module = ast.parse((Path(directory) / "core.py").read_text())
    for item in module.body:
        if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in item.targets):
            value = ast.literal_eval(item.value)
            if isinstance(value, str) and version(value):
                return value
    raise RuntimeError("The release does not declare a valid application version.")


def validate_repository(value):
    value = value.strip()
    if not value or value.startswith("-") or any(character in value for character in "\n\r\x00"):
        raise RuntimeError("Enter a valid Git repository clone URL.")
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError("Use a plain HTTPS clone URL without passwords or access tokens.")
    elif value.startswith("ssh://"):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.password:
            raise RuntimeError("Use a valid SSH clone URL.")
    elif re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./-]+", value):
        pass
    elif Path(value).is_absolute() and Path(value).is_dir():
        pass  # local Git repositories are useful for development and offline tests
    else:
        raise RuntimeError("Use an HTTPS or SSH clone URL, or an existing absolute local repository path.")
    return value


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_extract(data, destination):
    if len(data) > MAX_SOURCE_BYTES:
        raise RuntimeError("The application release is too large.")
    destination = Path(destination).resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > 2000 or sum(member.size for member in members) > MAX_SOURCE_BYTES:
            raise RuntimeError("The application release contains too many or too large files.")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or ".git" in path.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError("The release archive contains an unsupported path or file type.")
            target = destination / path
            if destination != target.resolve() and destination not in target.resolve().parents:
                raise RuntimeError("Release file would escape the application folder.")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                with target.open("xb") as stream:
                    shutil.copyfileobj(source, stream)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def validate_release(directory, tag):
    directory = Path(directory)
    required = ("discroom.py", "core.py", "updater.py", "launch.py")
    if any(not (directory / name).is_file() for name in required):
        raise RuntimeError("The release is missing application or launcher files.")
    if version(application_version(directory)) != version(tag):
        raise RuntimeError("The release tag does not match its declared application version.")
    for source in directory.rglob("*.py"):
        compile(source.read_bytes(), str(source), "exec")
    result = subprocess.run([sys.executable, "-c", "import PyQt5.QtWidgets, mutagen.flac, requests; import discroom"],
                            cwd=directory, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Release dependency/import checks failed. The current version is unchanged.\n"
                           + result.stderr[-1200:])


class UpdateStore:
    def __init__(self, root=None, configuration=None):
        self.root = Path(root or Path.home() / ".local/share/discroom/app")
        self.configuration = Path(configuration or Path.home() / ".config/discroom/updater.json")
        self.releases = self.root / "releases"
        self.cache = self.root / "repository.git"
        self.state_file = self.root / "state.json"

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "update.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another update is already running. Try again when it finishes.") from None
            yield

    def config(self):
        return json.loads(self.configuration.read_text()) if self.configuration.exists() else {"repository": DEFAULT_REPOSITORY}

    def configure(self, repository):
        config = self.config()
        config["repository"] = validate_repository(repository)
        atomic_json(self.configuration, config)

    def state(self):
        if self.state_file.exists():
            state = json.loads(self.state_file.read_text())
        else:
            state = {}
        return {"current": None, "previous": None, "pending": None, "failed": [], **state}

    def release_path(self, release):
        name = release.get("directory", "")
        if not re.fullmatch(r"v\d+\.\d+\.\d+-[0-9a-f]{12}", name):
            raise RuntimeError("Invalid installed release folder.")
        path = self.releases / name
        if path.is_symlink() or path.resolve().parent != self.releases.resolve():
            raise RuntimeError("Invalid installed release path.")
        return path

    def _git(self, arguments, timeout=120, binary=False):
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"
        try:
            result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments],
                                    capture_output=True, text=not binary, env=environment, timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("Git is missing. Install it with: sudo apt install git") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("Git timed out. Check the network and repository access.") from None
        if result.returncode:
            raise RuntimeError("Git could not access the repository or release. Check the clone URL, network and authentication. "
                               "For a private repository, configure a read-only SSH deploy key on the Pi.")
        return result.stdout

    def _fetch(self, repository):
        if not self.cache.exists():
            temporary = self.root / f"clone-{uuid.uuid4().hex}"
            try:
                self._git(["clone", "--bare", "--", repository, str(temporary)])
                os.rename(temporary, self.cache)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        else:
            origin = self._git(["--git-dir", str(self.cache), "remote", "get-url", "origin"]).strip()
            if origin != repository:
                raise RuntimeError("This installation already uses another repository. Ask the developer to switch its cache before changing the source.")
            # Published release tags are immutable; a rewritten tag is not silently accepted.
            self._git(["--git-dir", str(self.cache), "fetch", "--tags", "--prune", "origin"])

    def check(self, current_version):
        repository = validate_repository(self.config().get("repository", ""))
        with self.lock():
            self._fetch(repository)
            tags = self._git(["--git-dir", str(self.cache), "tag", "--list"]).splitlines()
            tags = sorted((tag for tag in tags if tag.startswith("v") and version(tag)), key=version, reverse=True)
            if not tags:
                raise RuntimeError("No numbered releases found. Publish a tag such as v0.3.0.")
            tag = tags[0]
            sha = self._git(["--git-dir", str(self.cache), "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"] ).strip()
            state = self.state()
            if sha in state["failed"]:
                raise RuntimeError("The newest release previously failed startup or was rolled back. Publish a newer release before updating again.")
            return {"tag": tag, "commit": sha, "repository": repository,
                    "available": version(tag) > version(current_version),
                    "directory": f"{tag}-{sha[:12]}"}

    def install(self, release, current_version, validator=validate_release):
        with self.lock():
            if release["repository"] != self.config().get("repository"):
                raise RuntimeError("The repository changed. Check for updates again.")
            if not version(release["tag"]) or version(release["tag"]) <= version(current_version):
                raise RuntimeError("This release is not newer than the running app.")
            commit = release.get("commit", "")
            if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                raise RuntimeError("Invalid release commit.")
            if release.get("directory") != f"{release['tag']}-{commit[:12]}":
                raise RuntimeError("Release folder does not match its tag and commit.")
            state = self.state()
            if state["current"] and version(state["current"]["tag"]) >= version(release["tag"]):
                if state["current"]["commit"] == commit:
                    return "This release is already installed. Restart through the launcher to use it."
                raise RuntimeError("A newer managed release is already installed. Launch the app through launch.py.")
            expected = self._git(["--git-dir", str(self.cache), "rev-parse", "--verify", f"refs/tags/{release['tag']}^{{commit}}"] ).strip()
            if expected != commit:
                raise RuntimeError("Release changed after checking. Check for updates again.")
            self.releases.mkdir(parents=True, exist_ok=True)
            target = self.release_path(release)
            if shutil.disk_usage(self.root).free < MAX_SOURCE_BYTES * 3:
                raise RuntimeError("Not enough space on the system storage for an app update.")
            temporary = Path(tempfile.mkdtemp(prefix="staging-", dir=self.root))
            try:
                archive = self._git(["--git-dir", str(self.cache), "archive", "--format=tar", commit], binary=True)
                safe_extract(archive, temporary)
                validator(temporary, release["tag"])
                if not target.exists():
                    os.rename(temporary, target)
                else:
                    validator(target, release["tag"])
                installed = {key: release[key] for key in ("tag", "commit", "directory")}
                state = self.state()
                state["pending"] = installed
                atomic_json(self.state_file, state)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return f"{release['tag']} installed. Restart the app to finish the update."

    def mark_ready(self, release):
        with self.lock():
            state = self.state()
            if state["pending"] != release:
                raise RuntimeError("The pending release changed during startup.")
            state["previous"] = state["current"]
            state["current"] = release
            state["pending"] = None
            atomic_json(self.state_file, state)

    def reject_pending(self):
        with self.lock():
            state = self.state()
            if state["pending"]:
                state["failed"] = list(set(state["failed"] + [state["pending"]["commit"]]))
                state["pending"] = None
                atomic_json(self.state_file, state)

    def rollback(self):
        with self.lock():
            state = self.state()
            if state["pending"]:
                state["pending"] = None
            elif state["current"]:
                failed = state["current"]["commit"]
                state["current"], state["previous"] = state["previous"], None
                state["failed"] = list(set(state["failed"] + [failed]))
            else:
                raise RuntimeError("There is no installed update to roll back.")
            atomic_json(self.state_file, state)
        return "Previous version selected. Restart the app to use it."

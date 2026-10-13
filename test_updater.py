import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import updater
import launch


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repository = self.base / "source"
        self.repository.mkdir()
        self.git("init", "-b", "main")
        self.store = updater.UpdateStore(self.base / "application", self.base / "configuration/updater.json")
        self.store.configure(str(self.repository))
        self.publish("0.3.0")

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *arguments):
        result = subprocess.run(["git", "-c", "user.name=Disc Room Tests", "-c", "user.email=tests@localhost",
                                 "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *arguments],
                                cwd=self.repository, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def publish(self, version):
        (self.repository / "core.py").write_text(f'VERSION = "{version}"\n')
        for name in ("discroom.py", "launch.py", "updater.py"):
            (self.repository / name).write_text("# release entrypoint\n")
        self.git("add", ".")
        self.git("commit", "-m", f"Release {version}")
        self.git("tag", f"v{version}")

    def validator(self, directory, tag):
        self.assertEqual(updater.version(updater.application_version(directory)), updater.version(tag))
        self.assertTrue((Path(directory) / "discroom.py").is_file())

    def install_initial(self):
        release = self.store.check("0.2.0")
        self.store.install(release, "0.2.0", validator=self.validator)
        pending = self.store.state()["pending"]
        self.store.mark_ready(pending)
        return pending

    def test_first_fetch_and_newer_release_detection(self):
        release = self.store.check("0.2.0")
        self.assertEqual(release["tag"], "v0.3.0")
        self.assertTrue(release["available"])
        self.assertFalse(self.store.check("0.3.0")["available"])

    def test_release_tags_sort_numerically_and_ignore_prereleases(self):
        self.publish("0.9.0")
        self.publish("0.10.0")
        self.git("tag", "v99.0.0-beta")
        self.assertEqual(self.store.check("0.3.0")["tag"], "v0.10.0")

    def test_install_is_pending_until_gui_is_ready(self):
        release = self.store.check("0.2.0")
        self.store.install(release, "0.2.0", validator=self.validator)
        state = self.store.state()
        self.assertIsNone(state["current"])
        self.assertEqual(state["pending"]["commit"], release["commit"])
        self.assertTrue((self.store.release_path(state["pending"]) / "discroom.py").is_file())
        self.store.mark_ready(state["pending"])
        self.assertEqual(self.store.state()["current"]["tag"], "v0.3.0")
        self.assertIsNone(self.store.state()["pending"])

    def test_failed_validation_preserves_installed_version(self):
        initial = self.install_initial()
        self.publish("0.4.0")
        release = self.store.check("0.3.0")
        def failure(directory, tag):
            raise RuntimeError("dependency missing")
        with self.assertRaisesRegex(RuntimeError, "dependency missing"):
            self.store.install(release, "0.3.0", validator=failure)
        self.assertEqual(self.store.state()["current"], initial)
        self.assertIsNone(self.store.state()["pending"])
        self.assertFalse(self.store.release_path(release).exists())

    def test_failed_startup_keeps_old_release_and_blocks_retry_of_same_commit(self):
        initial = self.install_initial()
        self.publish("0.4.0")
        release = self.store.check("0.3.0")
        self.store.install(release, "0.3.0", validator=self.validator)
        self.store.reject_pending()
        self.assertEqual(self.store.state()["current"], initial)
        self.assertIsNone(self.store.state()["pending"])
        with self.assertRaisesRegex(RuntimeError, "previously failed"):
            self.store.check("0.3.0")

    def test_successful_update_keeps_previous_release_for_rollback(self):
        initial = self.install_initial()
        self.publish("0.4.0")
        release = self.store.check("0.3.0")
        self.store.install(release, "0.3.0", validator=self.validator)
        self.store.mark_ready(self.store.state()["pending"])
        self.assertEqual(self.store.state()["previous"], initial)
        self.store.rollback()
        self.assertEqual(self.store.state()["current"], initial)
        self.assertTrue(self.store.release_path(release).exists())

    def test_bootstrap_remains_fallback_for_first_update(self):
        self.install_initial()
        self.store.rollback()
        self.assertIsNone(self.store.state()["current"])
        self.assertIsNone(self.store.state()["pending"])

    def test_rewritten_tag_is_not_silently_fetched(self):
        release = self.store.check("0.2.0")
        (self.repository / "core.py").write_text('VERSION = "0.3.0"\n# rewritten\n')
        self.git("add", ".")
        self.git("commit", "-m", "Rewrite release")
        self.git("tag", "-f", "v0.3.0")
        with self.assertRaisesRegex(RuntimeError, "Git could not access"):
            self.store.check("0.2.0")
        self.assertIsNone(self.store.state()["pending"])

    def test_update_lock_prevents_concurrent_installs(self):
        with self.store.lock():
            with self.assertRaisesRegex(RuntimeError, "already running"):
                self.store.check("0.2.0")

    def test_invalid_release_path_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid installed release folder"):
            self.store.release_path({"directory": "../../music"})

    def test_stale_bootstrap_does_not_replace_previous_release_with_itself(self):
        initial = self.install_initial()
        release = self.store.check("0.2.0")
        message = self.store.install(release, "0.2.0", validator=self.validator)
        self.assertIn("already installed", message)
        self.assertEqual(self.store.state()["current"], initial)
        self.assertIsNone(self.store.state()["pending"])
        self.assertIsNone(self.store.state()["previous"])

    def test_release_version_must_match_tag(self):
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            updater.validate_release(self.repository, "v0.4.0")

    def test_password_or_token_is_not_saved_in_clone_url(self):
        with self.assertRaisesRegex(RuntimeError, "without passwords"):
            self.store.configure("https://secret-token@github.com/danpidan/discroom.git")
        self.assertEqual(self.store.config()["repository"], str(self.repository))

    def test_application_install_does_not_touch_music_or_existing_config(self):
        music = self.base / "HDD/Music/track.flac"
        music.parent.mkdir(parents=True)
        music.write_bytes(b"music stays here")
        config = self.base / "configuration/config.json"
        config.write_text('{"volume":75}')
        self.install_initial()
        self.assertEqual(music.read_bytes(), b"music stays here")
        self.assertEqual(config.read_text(), '{"volume":75}')


class ArchiveTests(unittest.TestCase):
    def test_archive_cannot_escape_or_install_symlinks(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "../outside" if kind == tarfile.SYMTYPE else ""
                archive.addfile(member, io.BytesIO(b""))
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(RuntimeError, "unsupported path"):
                    updater.safe_extract(stream.getvalue(), directory)

    def test_launcher_rejects_bad_update_then_runs_old_app(self):
        class Store:
            def __init__(self):
                self.pending = {"directory": "bad"}
                self.rejected = False
            def state(self):
                return {"pending": self.pending, "current": None}
            def release_path(self, release):
                return Path("/tmp/bad-release")
            def reject_pending(self):
                self.rejected = True
                self.pending = None
        class Process:
            returncode = 1
            def wait(self):
                return 0
        store = Store()
        with patch("launch.UpdateStore", return_value=store), \
             patch("launch.launch_once", side_effect=[(Process(), False), (Process(), True)]) as start:
            self.assertEqual(launch.main([]), 0)
        self.assertTrue(store.rejected)
        self.assertEqual(start.call_count, 2)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Test and tag a committed release; publishing requires the explicit --push flag."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from updater import application_version


def git(*arguments):
    result = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true", help="Publish main and the release tag to origin after tests")
    args = parser.parse_args()
    try:
        if git("status", "--porcelain"):
            raise RuntimeError("Commit your edits before tagging a release.")
        if git("branch", "--show-current") != "main":
            raise RuntimeError("Switch to main before publishing a release.")
        tag = "v" + application_version(ROOT)
        tests = subprocess.run([sys.executable, "-m", "unittest", "test_core", "test_audio", "test_controls",
                                "test_management", "test_updater", "test_library_startup", "test_power"], cwd=ROOT)
        if tests.returncode:
            raise RuntimeError("Tests failed; no release tag was created or published.")
        for name in git("ls-files", "--", "*.py").splitlines():
            source = ROOT / name
            compile(source.read_bytes(), str(source), "exec")
        existing = git("tag", "--list", tag)
        if existing:
            if git("rev-parse", f"{tag}^{{commit}}") != git("rev-parse", "HEAD"):
                raise RuntimeError("This version tag already names another commit. Increase VERSION; never rewrite a published release.")
        else:
            git("tag", tag)
        if args.push:
            git("push", "origin", "main")
            git("push", "origin", tag)
            print(f"Published {tag}. The Pi can now install it from Software updates.")
        else:
            print(f"Created/verified {tag} locally. Use --push when ready to publish it.")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Launch managed releases; keep the old app if a pending release fails startup."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from updater import UpdateStore, RESTART_CODE


def launch_once(directory, arguments, store, readiness=True, timeout=30):
    run = store.root / "run"
    run.mkdir(parents=True, exist_ok=True)
    ready = run / f"ready-{uuid.uuid4().hex}"
    environment = os.environ.copy()
    environment["DISCROOM_LAUNCHER"] = "1"
    command = [sys.executable, str(directory / "discroom.py"), *arguments]
    if readiness:
        command += ["--ready-file", str(ready)]
    process = subprocess.Popen(command, cwd=directory, env=environment)
    deadline = time.monotonic() + timeout
    if readiness:
        while not ready.exists():
            if process.poll() is not None:
                return process, False
            if time.monotonic() >= deadline:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                return process, False
            time.sleep(.1)
        ready.unlink(missing_ok=True)
    return process, True


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    store = UpdateStore()
    bootstrap = Path(__file__).resolve().parent
    while True:
        state = store.state()
        pending = state["pending"]
        release = pending or state["current"]
        directory = store.release_path(release) if release else bootstrap
        process, ready = launch_once(directory, arguments, store)
        if not ready:
            if pending:
                store.reject_pending()
                print("The update failed startup; launching the previous app.", file=sys.stderr)
                continue
            if release:
                store.rollback()
                print("The installed app failed startup; launching its fallback.", file=sys.stderr)
                continue
            return process.returncode or 1
        if pending:
            store.mark_ready(pending)
        code = process.wait()
        if code != RESTART_CODE:
            return code


if __name__ == "__main__":
    sys.exit(main())

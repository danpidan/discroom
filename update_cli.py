#!/usr/bin/env python3
"""Remote-terminal commands for the same updater used in Settings."""
import argparse
import json
from pathlib import Path
import sys

from updater import UpdateStore, application_version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configure", "status", "check", "install", "rollback"])
    parser.add_argument("--repository", help="HTTPS or SSH clone URL")
    args = parser.parse_args()
    store = UpdateStore()
    try:
        current = store.state()["current"]
        installed = current["tag"] if current else application_version(Path(__file__).parent)
        if args.action == "configure":
            store.configure(args.repository or "")
            print("Update repository saved.")
        elif args.action == "status":
            print(json.dumps(store.state(), indent=2))
        elif args.action == "rollback":
            print(store.rollback())
        else:
            release = store.check(installed)
            if args.action == "check":
                print(f"Running {installed}; newest {release['tag']}; update available: {release['available']}")
            elif release["available"]:
                print(store.install(release, installed))
            else:
                print("The app is up to date.")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

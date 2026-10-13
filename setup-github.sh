#!/bin/sh
# One-time setup on the Pi. Authenticate in GitHub's browser flow, then push.
set -eu
if [ "$(id -u)" = "0" ]; then
    echo "Run this as your normal Pi user, without sudo."
    exit 1
fi
discroom_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
discroom_development_dir="$HOME/discroom-dev"
discroom_bundle="$discroom_source_dir/discroom-repository.bundle"
if [ ! -f "$discroom_bundle" ]; then
    echo "The starter Git bundle was not found. Use the downloaded v0.3.0 setup ZIP."
    exit 1
fi
if [ -e "$discroom_development_dir" ]; then
    echo "$discroom_development_dir already exists. Keep it and follow DEVELOPMENT.md to use an existing checkout."
    exit 1
fi
sudo apt update
sudo apt install git gh
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    gh auth login --hostname github.com --git-protocol https --web
fi
gh auth setup-git --hostname github.com
git clone "$discroom_bundle" "$discroom_development_dir"
git -C "$discroom_development_dir" remote set-url origin https://github.com/danpidan/discroom.git
git -C "$discroom_development_dir" push -u origin main
git -C "$discroom_development_dir" push origin v0.3.0
echo "GitHub now has the initial source and v0.3.0 release tag."
echo "Develop in $discroom_development_dir. Run the installed app using python3 launch.py."

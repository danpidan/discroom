# GitHub development and application updates

Repository: https://github.com/danpidan/discroom.git

## One-time setup on the Pi

1. Close Disc Room. Extract the v0.3.0 setup ZIP over `~/discroom`, replacing its
   application files. The ZIP includes a starter Git bundle with source history
   and an initial `v0.3.0` release tag. The developer did not push into GitHub from
   the chat; this step performs that first push from your authenticated Pi.
2. Open a Pi terminal:

   ```sh
   cd ~/discroom
   bash setup-github.sh
   ```

   The script installs Git/GitHub CLI if needed, asks you to log in through
   GitHub's browser flow, clones the starter history to `~/discroom-dev`, and
   pushes `main` and `v0.3.0` to your empty repository. Use your existing GitHub
   account. No password/token needs to be pasted into the app or this chat.
   GitHub CLI configures Git credentials for the Pi user, so a private HTTPS
   repository can also be fetched by the application.

   If `~/discroom-dev` already exists, the script stops and preserves it. For an
   existing checkout, check its contents, set its origin to the URL above, then
   push its branch and release tag normally. A nonempty remote may require a
   normal merge; the script never force-pushes.

3. Launch the app through its supervisor:

   ```sh
   cd ~/discroom
   python3 launch.py
   ```

4. **Settings → Software updates → Check for updates** should report that
   `v0.3.0` is current. **Update app** fetches and installs a higher release tag
   when one exists, validates the source/imports, then restarts the GUI.

The source bundle is only a setup/transfer artifact. It is ignored by Git and
not included in later Git releases. Future updates no longer require USB ZIPs.

## Edit and publish

The two locations have different purposes:

| Location | Purpose |
|---|---|
| `~/discroom-dev` | Git checkout for editing, testing and publishing |
| `~/discroom` | Installed bootstrap app and launcher |
| `~/.local/share/discroom/app/releases` | Installed immutable application releases |
| `~/.config/discroom` | User settings and update source |
| HDD `DiscRoom/Music` | Music library and its Trash |

Edit the development checkout, keeping installed releases unchanged. To preview
the GUI without accessing music or hardware:

```sh
cd ~/discroom-dev
python3 discroom.py --demo
```

For hardware tests, close the normal application and run the developer copy from
the Pi desktop. Only one application should control the CD drive/DAC at a time.
Both live copies use your existing settings; demo mode avoids those writes.

To publish the next version:

1. Make the code changes in `~/discroom-dev` and test the GUI on the Pi.
2. Change `VERSION` in `core.py`, for example from `0.3.0` to `0.3.1`.
3. Commit and publish:

   ```sh
   git add .
   git commit -m "Improve the music interface"
   python3 tools/release.py --push
   ```

   The release helper requires a clean committed `main`, runs the hardware-free
   tests, checks syntax, and creates/pushes `v0.3.1`. It never rewrites an existing
   published tag. GitHub Actions runs the same checks after pushes/PRs; its
   results do not themselves gate the updater. Publish tags only after your
   local tests and hardware checks pass.
4. On the player, press **Settings → Software updates → Update app**.

Ordinary commits on `main` do not update the appliance. Tags like `v0.3.1` are
the update channel; prerelease tags such as `v0.4.0-beta` are ignored.

Set your own commit identity in the development checkout before your first
commit if Git asks for it:

```sh
git config user.name "Your name"
git config user.email "Your GitHub email or GitHub noreply address"
```

## Develop remotely with VS Code

1. Enable SSH on the Pi using Raspberry Pi Configuration or `sudo raspi-config`
   → Interface Options → SSH. Get its network address with `hostname -I`.
2. Install VS Code on your computer and the **Remote - SSH** extension.
3. Connect to `cddigitiser@<Pi-IP-address>` using your Pi login, then open
   `/home/cddigitiser/discroom-dev`.
4. Edit, run unit tests and commit/publish from VS Code's remote terminal. GUI
   hardware testing can still run on the Pi's attached screen.

This requires no remote desktop for editing. Keep remote SSH access on your
home network initially. Secure access from outside the home can be configured
later with a VPN; opening an internet-facing SSH port is not required.

The terminal updater uses the same release store as the GUI:

```sh
cd ~/discroom
python3 update_cli.py status
python3 update_cli.py check
python3 update_cli.py install
python3 update_cli.py rollback
```

CLI install/rollback selects a version for the next application launch and
does not forcibly interrupt a running copy. Quit and relaunch through
`python3 launch.py` when ready. Once boot hosting is configured, these commands
can be paired with a normal service restart.

## Startup validation and rollback

An update is downloaded into a new directory, checked for valid source, a tag
matching `VERSION`, available imports/dependencies and safe archive paths, then
marked pending. The running app's files are not replaced. On restart, the
launcher waits up to 30 seconds for the new GUI's startup readiness marker.
If the process exits or never signals ready, that commit is marked failed and
the prior application is launched. A successfully started update retains its
previous release for **Roll back update**. The original `~/discroom` bootstrap
is the fallback for the first managed update.

The readiness marker confirms startup and a responsive GUI event loop; it does
not certify audio hardware, metadata lookups or every button. Later functional
problems can be rolled back manually. A failed/rolled-back commit is not offered
again until a newer release is published.

Rollback selects application code, not a historical copy of music or settings.
No data migration is introduced in this build. Future changes to storage formats
need an explicit compatibility/migration plan. Dependency checks do not install
system packages or run sudo from the GUI; new dependencies require a deliberate
installer change and manual setup.

Private repositories can alternatively use a read-only SSH deploy key configured
on the Pi and an SSH clone URL in Software updates. Complete normal host/key
setup before using the noninteractive updater. Clone URLs cannot contain access
tokens or passwords.

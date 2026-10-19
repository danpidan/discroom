# Version 0.3.4

- Added Shuffle all to play every stored track once in a random order across
  the entire library, including albums hidden by search or filters.
- Album track lists show each FLAC file's size alongside its duration.
- Added a music-drive usage bar at the top, with a fixed decimal 0–1 TB scale,
  used/free space labels and a disconnected state. Usage includes all files on
  the selected music partition, including Trash.
- Added Pi desktop to minimize the app while playback/copying continues, plus
  a Full screen / Windowed button. F11 toggles full screen; Escape leaves it.
  Select Disc Room on the desktop taskbar and press Full screen to return.
- Enlarged home-screen navigation, album, CD and playback buttons to at least
  72 pixels high, while retaining the separate touchscreen keyboard layout.
- The song timeline supports tapping anywhere and dragging, with elapsed-time
  preview, keyboard controls and protection against stale playback updates.
- Stored playback uses a temporary playlist file so large shuffled libraries
  do not exceed the operating system's command-line limit.
- Added library, storage, window and playback regression coverage, and Linux
  CI checks. The app still uses its existing Pi dependencies.

# Version 0.3.3

- Added Restart Pi and Shut Down buttons to Settings, each with a confirmation
  that defaults to No. Demo mode never makes system power requests.
- Refuses either action during a CD copy or music-library edit. After confirmation,
  stops new drive checks, waits for active workers, saves settings and stops audio.
- Requests a normal systemd restart/shutdown without sudo, forced shutdown or
  interactive password prompts. If saving settings or the system request fails,
  the app stays open and its controls/checks are restored.
- Settings describes when to disconnect power after shutdown.

# Version 0.3.2

- Automatically scans the library when the music drive first becomes available
  after startup or is reconnected, instead of requiring Refresh library.
- Defers this scan while another scan, a CD copy or a music-library edit is busy.
  An available drive is not repeatedly scanned on every storage check.
- Clears unavailable albums and filters when the drive disconnects and rejects
  scan results if the drive is no longer mounted.
- Uses the existing five-second storage timer and background scan worker. This
  release does not change how the operating system mounts the HDD.

# Version 0.3.1

- Removed the Import ripped test track button from Settings and its test-only
  import code. The empty-library message now explains how to copy a CD.
- Updated setup instructions to use normal album playback and CD copying.
- Existing music, including any previously imported test album, is preserved.

# Version 0.3.0

- Added Git release updates at Settings → Software updates, with the source
  preconfigured for https://github.com/danpidan/discroom.git.
- Added separate staged release directories, import/syntax/version checks,
  a supervised restart, startup readiness check and fallback to the old app.
- Added manual rollback and a terminal updater for remote development.
- Added a prepared Git repository, a one-time GitHub authentication/push helper,
  a release helper and hardware-free CI checks.
- Full-screen preference now survives application restarts.
- See DEVELOPMENT.md for setup, Git authentication and remote SSH editing.

# Version 0.2.0

- Reworked mpv controls to wait for matching replies when events arrive first.
  Requests run in a worker thread, so the interface stays responsive. Old-session
  requests are cancelled when stopping or switching playback.
- Volume has larger minus/plus controls, a wider slider, an actual percentage
  display and a saved preference. Playback starts with the chosen software level.
- Stopping an old playback process to select another track no longer produces a
  DAC warning. Actual playback failures still show an error.
- Previous, Next, Play and Pause use readable text instead of unsupported media
  glyphs. Normal buttons are at least 58 pixels high, and interface text is larger.
- Album controls now use a three-column grid with larger track rows. Playing a
  selected track starts there in the full album queue, preserving navigation.
- Added editing of stored album artist/title, year, genre and track title/artist,
  plus a simpler editor for one selected track. Files retain their current paths.
  Edits are prepared with original backups and rollback on commit failure.
- Added album/track deletion with confirmation. Files move into an app-owned
  Trash folder; whole-album deletion includes its cover when no other album
  shares the folder. Settings → Trash can restore or permanently delete an item.
- Trash restores refuse to replace existing music, and operations are restricted
  to the mounted app library. Music outside DiscRoom/Music is not managed.
- Settings content scrolls while Save/Cancel stay visible, and the CD controls
  use a more compact arrangement for landscape screens.

Checks: 38 hardware-free regression tests, Python compilation and shell syntax.
Control protocol tests use in-memory transport fixtures; editing/ripping tests
use mock FLAC/drive operations. This workspace cannot run Qt or the actual DAC,
so the new layout and audible volume changes need a test on your Pi.

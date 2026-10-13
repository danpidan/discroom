# Disc Room — Pi test build, version 0.3.3

Native Python / PyQt5 interface for your Raspberry Pi 4B running Bookworm with
`armhf` applications. Designed around 1280 × 720 landscape; test on your existing
11.6-inch screen before moving to the 7-inch Touch Display 2.

## Get started

1. Download `discroom-pi-first-build.zip` on the Pi, or transfer it from your
   computer using a USB stick. Extract it into your home folder. The resulting
   folder should be `~/discroom`.
2. Open a terminal and run:

   ```sh
   cd ~/discroom
   bash install.sh
   ```

   This installs dependencies through apt, which supports your `armhf` system.
   Run as your ordinary desktop user. The script uses sudo only for apt.

3. Connect the HDD. If needed, open it in File Manager so the desktop mounts it.
   Launch with your confirmed mount folder:

   ```sh
   cd ~/discroom
   python3 launch.py --mount /media/cddigitiser/DA265AA768731CC7
   ```

   Start windowed for the first test. Once happy, add `--fullscreen`. Press Escape
   to leave fullscreen, or use **Settings → Close app** to exit.

4. Open **Settings**, select the Ugreen/USB DAC in the detected **Audio output**
   list, and press **Save**. If your library already contains albums, open an
   album and choose **Play album** to check playback.
5. Insert an audio CD. The app checks every four seconds and shows **Play CD**
   and **Copy CD** after reading the track list. The desktop's removable-media
   popup may still appear during this test; close that popup.
6. Choose **Copy CD**, confirm/edit the album and track details, then choose
   **Save & Copy**. Progress is shown by track. On success, the completed album
   is added to the library and the tray opens.

## What this build includes

- Native dark touchscreen interface with an album grid and persistent player.
- Search by artist, album or track; artist, genre and year filters; sorting.
- Automatic library loading when the music drive becomes available after boot
  or is reconnected. The five-second storage check starts one background scan;
  it waits for an existing scan, CD copy or library edit to finish.
- Favourites, album/track playback, queue, pause, next/previous, seek and volume.
- Larger touch controls, text transport labels, volume +/- buttons and a saved
  volume preference. Playback commands run outside the GUI thread.
- Stored album/track metadata editors and deletion to Trash; Settings → Trash
  offers Restore and Delete permanently. Deletion requires confirmation.
- **Settings → Restart Pi / Shut Down** asks for confirmation and requests an
  orderly system restart/shutdown. Finish any CD copy or library edit first.
  Background checks finish and settings are saved before the request. Power
  commands use the normal logged-in desktop user's system permissions; a refused
  request shows an error and keeps the app open. Demo mode never powers off.
- **Settings → Software updates** checks numbered Git release tags and offers
  Update app, restart and rollback. New releases are installed separately and
  activated only after GUI startup succeeds. See DEVELOPMENT.md for setup.
- On-screen keyboard for Search and pre-copy metadata editing.
- Audio CD detection, direct CD playback and track selection using mpv.
- MusicBrainz album matches, editable album artist/title, year, genre and track
  titles/artists; compilation support. Offline or unmatched CDs can be entered
  manually. Multiple available releases appear in the editor's match selector.
- cdparanoia with abort-on-skip, per-track FLAC encoding verification, tagging,
  optional cover art and software eject only after a successful save.
- New files under `DiscRoom/Music` on the selected drive. Existing files outside
  that folder are not changed. An already existing album/disc is never replaced.
- Temporary copies kept outside the visible album library. A failed or cancelled
  job is removed; the CD stays inserted. The mount is checked before creating
  folders and throughout copying, so a missing HDD is not silently replaced by
  the microSD card as a destination.

## Audio

Version 0.1.1 replaces the guessed USB card name with a detected output list.
Settings queries mpv and saves the exact output ID reported by your Pi. Choose
the entry describing your Ugreen/USB DAC, then press Save. On a desktop system,
a matching "desktop audio" entry routes through the desktop's audio mixer.
If the list only contains HDMI or headphones, check the DAC connection and
press **Refresh audio outputs**. For terminal diagnostics, list devices:

```sh
mpv --audio-device=help
```

The app starts at 40% software volume on a fresh configuration, then remembers
your chosen level. The +/- buttons change it in 5% steps and the slider allows
finer adjustment. Keep your amplifier low during the first test. Close other audio players before testing, as they may hold the
DAC open. A desktop audio server may also own the output; if mpv reports that
it is busy, send the message before changing audio services.

Direct CD playback requires the installed mpv build to support CDDA. The Pi's
previous test confirmed ripping and FLAC playback through aplay; playback from
CD through mpv is a separate check. If unsupported, send its error message so
we can select a compatible playback backend.
The implementation follows the [Bookworm mpv manual](https://manpages.debian.org/bookworm/mpv/mpv.1.en.html):
CDDA uses a single CD stream and tracks are chapters.

## Storage

On your current drive the library is:

```text
/media/cddigitiser/DA265AA768731CC7/DiscRoom/Music
    Album artist/Album title/Disc 1/01 - Track title.flac
```

Configuration is saved in `~/.config/discroom/config.json`. Changing the mount
folder in Settings selects the library on that drive; it does not move or erase
music on the previous drive. A differently numbered disc in the same album gets
a separate `Disc N` folder. To copy a duplicate edition, edit the album title to
make a distinct folder.

The app only scans its dedicated music folder, not the whole HDD. It reads
embedded FLAC cover art, or cover.jpg/cover.png/folder.jpg beside the tracks.
Simple generated artwork appears when no cover is available. The screenshot's
fictional albums are only in explicit demo mode; they are not added to your HDD.

## Current limits and next stages

This is the first hardware-test build, not a completed Brennan replacement.
Full hardware validation needs your Pi. Boot splash, persistent HDD mounting
and Samba sharing are not configured by this package. Full-screen automatic
startup has been configured separately through the Pi's labwc startup file.

Resume after power loss and saved playlist management are not included yet.
A lost-power partial copy remains
hidden in `.copying` for later inspection. Scratched discs can still fail;
encoding verification confirms the FLAC matches the extracted WAV, rather than
independently confirming the CD audio against AccurateRip.

The search keyboard handles letters, digits, basic punctuation and spaces. A
physical keyboard can enter additional characters. Long albums scroll in the
track editor. The first visual check should verify keyboard/layout fit on your
screen; the cloud workspace could not install Qt test dependencies.

## Optional design-only preview

```sh
python3 discroom.py --demo
```

Demo mode shows fictional albums and a pretend CD, and does not access drives,
save configuration or rip anything.

## Tests

```sh
python3 -m unittest -v test_core test_audio test_controls test_management test_updater
```

The tests use fake optical-drive and encoder commands to exercise real storage
orchestration: no publication after failure/cancellation, no overwriting an
existing edition, mount checks, and save-before-eject ordering. They do not
verify the physical drive or graphical rendering.
The control tests check fragmented/event-first replies and intentional stop
handling using an in-memory transport. Library management tests cover editing,
rollback, deleting/restoring albums and tracks, and refusing restore conflicts.

## Managing stored music

Open an album to use **Edit album**, **Edit track**, **Delete album**, or
**Delete track**. Select a track before using its controls. Album editing changes
stored tags while retaining existing file paths; search and display refresh
after saving. Favourites follow an album's changed title/artist.

Delete moves selected music into `DiscRoom/Music/.trash`. These files no longer
appear in the library, but still use disk space until **Settings → Trash →
Delete permanently**. **Restore** puts an item back in its original location
and refuses to overwrite any newer music there. Deleting a whole album includes
its cover if the folder contains no other album's music. Deleting one track
preserves the other tracks and artwork.

The two-row player bar has readable **Previous**, **Play/Pause**, **Next**, and
**Stop** controls. Playing from a selected track retains the full album queue,
so Previous and Next can navigate the album.

## Updating an existing build

Close the app, then extract the newer archive over the existing `~/discroom`
application folder. Replace its application files when asked. Music is in a
separate folder on your HDD, and configuration is in `~/.config/discroom`, so
neither is included in or overwritten by the archive.

Launch `python3 launch.py`, open Settings, select the detected USB DAC output,
and press Save. Dependency installation is not needed again if the first build
is already installed. Output selection still needs testing on your Pi.

For the existing public GitHub repository, the Pi can download the app without
logging in:

```sh
git clone https://github.com/danpidan/discroom.git ~/discroom-git
cd ~/discroom-git
python3 launch.py
```

If this folder already exists, launch its `launch.py` rather than cloning again.
Use **Settings → Software updates → Check for updates → Update app** to install
new numbered releases. The app restarts automatically. Publishing changes to
GitHub still requires the repository owner's authentication.

Check playback/volume and track switching, then use an album to check
editing, deletion and restoration. See CHANGELOG.md for changes and limits.

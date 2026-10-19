# Raspberry Pi OS Lite: display, storage and startup

Follow this after README steps 1–3, using a **fresh Raspberry Pi OS Lite
Bookworm / Debian 12 installation on Pi 4B**. The app was hardware-tested on
Bookworm with a desktop; this fresh Lite/X11 setup still needs a physical Pi
check. Other OS versions and Pi models are untested.

Run commands on the Pi, as its normal user, either at its terminal or through
SSH. These instructions add a minimal graphical session and configure automatic
login. They are not needed on the already configured labwc desktop installation.

## 1. Install the minimal display components

```sh
sudo apt install -y --no-install-recommends \
  xserver-xorg xserver-xorg-input-libinput \
  lightdm lightdm-gtk-greeter openbox x11-xserver-utils xterm \
  dbus-x11 policykit-1 python3-xdg fonts-dejavu-core \
  alsa-utils ntfs-3g exfatprogs
```

These provide X11, a login manager, a small window manager, touch/mouse input,
fonts, a maintenance terminal and common external-drive filesystem support.
They do not install the full Raspberry Pi desktop applications. If prompted
for a default display manager, choose **lightdm**.

Ensure the Pi account has device access, then reboot later to apply membership:

```sh
sudo usermod -aG audio,cdrom,video,input,render "$(id -un)"
```

## 2. Identify the music partition

Connect the external HDD/SSD and run:

```sh
lsblk -o NAME,SIZE,MODEL,FSTYPE,UUID,MOUNTPOINTS
id -u
id -g
```

Identify the intended **external-drive partition** by model and size, then note
its filesystem type and UUID. The microSD OS partitions must remain the OS
storage. Record the numeric user/group IDs; they are often 1000, but use your
actual results below. This procedure mounts an existing formatted partition.

## 3. Configure a persistent music mount

Create the mount folder and make a backup before editing:

```sh
sudo mkdir -p /mnt/discroom-music
sudo cp -n /etc/fstab /etc/fstab.before-discroom
sudo nano /etc/fstab
```

Add **one** of the following lines, matching your partition's filesystem.
Replace `YOUR_DRIVE_UUID`, `YOUR_UID` and `YOUR_GID` with the values from step 2.
If the UUID already has an fstab entry, adjust that entry instead of adding a
second mount for the same partition.

**NTFS:**

```fstab
UUID=YOUR_DRIVE_UUID /mnt/discroom-music ntfs-3g defaults,nofail,x-systemd.device-timeout=10s,uid=YOUR_UID,gid=YOUR_GID,umask=0022 0 0
```

**exFAT:**

```fstab
UUID=YOUR_DRIVE_UUID /mnt/discroom-music exfat defaults,nofail,x-systemd.device-timeout=10s,uid=YOUR_UID,gid=YOUR_GID,umask=0022 0 0
```

**ext4:**

```fstab
UUID=YOUR_DRIVE_UUID /mnt/discroom-music ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2
```

Save in nano with **Ctrl+O**, Enter, then **Ctrl+X**. Load the changed definition,
mount the drive and check the result:

```sh
sudo systemctl daemon-reload
sudo mount /mnt/discroom-music
findmnt /mnt/discroom-music
```

Confirm that `findmnt` reports the external partition at that exact folder.
If mounting fails, correct the error before continuing. `nofail` lets the Pi
boot when the music drive is absent; the app then shows the drive as unavailable.

Create the dedicated library **only after the drive has mounted**:

```sh
(
set -eu
mountpoint -q /mnt/discroom-music
sudo mkdir -p /mnt/discroom-music/DiscRoom/Music
)
```

**For ext4 only**, grant your Pi user ownership of the two app directories:

```sh
(
set -eu
mountpoint -q /mnt/discroom-music
sudo chown "$(id -un):$(id -gn)" \
  /mnt/discroom-music/DiscRoom \
  /mnt/discroom-music/DiscRoom/Music
)
```

NTFS/exFAT use the UID/GID mount options instead. If reusing an existing ext4
DiscRoom library, its existing music files also need suitable write permissions
for editing/deletion; folder ownership alone does not change those file owners.

## 4. Configure full-screen app startup

Create the user startup and log directories. Preserve an existing Openbox
startup file before editing:

```sh
mkdir -p ~/.config/openbox ~/.local/state/discroom
if [ -f ~/.config/openbox/autostart ]; then
  cp -n ~/.config/openbox/autostart ~/.config/openbox/autostart.before-discroom
fi
nano ~/.config/openbox/autostart
```

Add these lines once, retaining any existing commands:

```sh
xset -dpms &
xset s off &
xset s noblank &
xsetroot -solid '#0d1522' &
/usr/bin/python3 "$HOME/discroom-git/launch.py" --fullscreen >> "$HOME/.local/state/discroom/startup.log" 2>&1 &
```

Save and exit nano. The background is a plain dark colour while the app opens.
The launcher selects managed releases, so the update button continues working.
Keep the final `&` on startup commands, as described in the
[Openbox session manual](https://manpages.debian.org/bookworm/openbox/openbox-session.1.en.html).

Now configure LightDM to log your normal Pi user into that session:

```sh
discroom_user="$(id -un)"
sudo mkdir -p /etc/lightdm/lightdm.conf.d
if [ -f /etc/lightdm/lightdm.conf.d/90-discroom.conf ]; then
  sudo cp -n /etc/lightdm/lightdm.conf.d/90-discroom.conf /etc/lightdm/lightdm.conf.d/90-discroom.conf.before-discroom
fi
sudo tee /etc/lightdm/lightdm.conf.d/90-discroom.conf >/dev/null <<EOF
[Seat:*]
autologin-user=$discroom_user
autologin-user-timeout=0
autologin-session=openbox
user-session=openbox
EOF
sudo systemctl enable lightdm
sudo systemctl set-default graphical.target
```

The login/session settings follow LightDM's
[configuration reference](https://github.com/canonical/lightdm/blob/main/data/lightdm.conf).
The app runs as your normal user, not as root.

## 5. Reboot and finish setup in the app

With the display, DAC, CD drive and music drive connected:

```sh
sudo reboot
```

Disc Room should open full-screen on the **attached display**. An SSH connection
will disconnect during reboot. In Settings, choose `/mnt/discroom-music` as the
music drive mount folder, select the USB DAC output and press **Save**. Return
to the README's usage instructions to copy or play music.

Verify display/touch input, CD copying, playback and volume, both power buttons,
automatic library loading after reboot, and an app update before treating this
Lite setup as validated. A portrait touchscreen needs display and touch rotation
configured for landscape; that depends on the particular display connection.

## If startup fails

From an SSH session connected to the Pi:

```sh
systemctl status lightdm --no-pager
tail -n 80 ~/.local/state/discroom/startup.log
findmnt /mnt/discroom-music
```

A missing startup log can mean the graphical session never reached its app
startup command. Check the LightDM status, the Openbox startup file and that
`~/discroom-git/launch.py` exists. Qt display errors mean the graphical session
or display connection needs attention; running `python3 launch.py` in an ordinary
SSH shell does not create a graphical session.

**Close app** leaves the minimal Openbox session running. A right-click on its
background can open a terminal, or use SSH for maintenance. To start manually
from a terminal within that graphical session:

```sh
cd ~/discroom-git
python3 launch.py --fullscreen
```

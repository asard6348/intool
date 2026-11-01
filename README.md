# Intool

Intool (Input Tool) is a hotkey-triggered input pulser. It repeatedly presses keys, mouse buttons, or scrolls the wheel, touchpad, or pinch gesture, and it can run from a small GUI or entirely from the terminal.

## Features

- Actions: any key, mouse button (`mouse0`–`mouse4`), key combinations (`ctrl+z`), or scrolling
- Action sequences: comma-separated steps with explicit `down:`/`up:`, `wait:`, relative `move:DX:DY` and exact `pos:X:Y` pointer steps
- Hotkeys of one or more keys joined with `+` (`f8`, `ctrl+f6`, `ctrl+k+c`), and extra hotkeys that each run a different action
- Recording: capture keys, mouse buttons and pointer positions into an action sequence, with an estimated pointer on Wayland compositors that cannot report it
- Minimize to the system tray, a daily start and stop schedule, and opening on system startup with chosen arguments
- Optional immediate action when the job starts, instead of after the first delay
- Key states: **press** (hold down), **release** (let go), or **press & release** (tap with a set interval)
- Scroll devices: **wheel** (notches), **touchpad** (smooth steps), or **pinch** (Ctrl held while scrolling)
- Delay between triggers, actions per trigger, and number of cycles (0 = forever)
- A global hotkey that toggles the job on and off, and a live countdown to the next trigger
- GUI with light, dark, and OLED themes, plus a terminal mode (`-n`) for scripting

## Download

Pre-built executables are attached to each [GitHub release](../../releases):

| Platform | File |
| --- | --- |
| Linux (x86_64) | `intool-linux-x86_64` |
| Windows GUI (x86_64) | `intool-windows-x86_64.exe` |
| Windows command line (x86_64) | `intool-cli-windows-x86_64.exe` |

Each release includes `SHA256SUMS.txt`. Verify a download with `sha256sum -c SHA256SUMS.txt` on Linux.

## Platform setup

### Linux

Intool injects input through the kernel's `uinput` device, which needs permission:

```sh
sudo modprobe uinput
sudo usermod -aG input "$USER"
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Log out and back in after changing group membership. On X11 the `xtest` backend also works without uinput. On Wayland, use `uinput`. The **root helper** backend asks for authorization once through `pkexec`.

Make the file executable before the first run:

```sh
chmod +x intool-linux-x86_64
./intool-linux-x86_64
```

### Windows

Two executables are provided:

- `intool-windows-x86_64.exe` opens only the graphical window. Use it for normal, double-click use.
- `intool-cli-windows-x86_64.exe` is a console program. Use it from a terminal with arguments, for example `intool-cli-windows-x86_64.exe -n -k f8 -a mouse0`. Run it with `--help` for all options.

Windows may show a SmartScreen warning for unsigned executables; choose **More info → Run anyway**.

## Usage

### GUI

Run the executable without arguments. Fill in the delay (1 second by default), actions per trigger, repeat cycles, action, and hotkey, then press **Start**, or **Start Listening** to start and stop the job with the hotkey. While a job runs the window counts down to the next trigger. The gear next to **Start Recording** opens the recording settings.

**Start Recording** captures the keys, mouse buttons and pointer you use until you press **Stop Recording** (or the recording hotkey, **F10** by default, which also starts a recording while listening) and writes them into the Action field. To play a recording once, turn on **Act immediately on start** and set **Repeat cycles** to 1. Recording and listening share one input source and can run together; the job hotkey is ignored while recording. Recording is unavailable while a job runs, because injected input would be captured too. With **Record pointer** set to `path`, a recording that holds only pointer movement is kept as a path of `pos:` or `move:` steps.

### Action sequences

Separate steps with commas. A plain step (`a`, `ctrl+z`, `mouse0`, `scrollup`) follows the key state and scroll settings. Extra steps:

| Step | Meaning |
| --- | --- |
| `down:KEY` / `up:KEY` | press or release keys and buttons explicitly (`down:ctrl+c`) |
| `wait:TIME` | pause (`wait:500ms`, `wait:2s`); Stop interrupts it |
| `move:DX:DY` | relative pointer movement (`move:50:-20`) |
| `pos:X:Y` | exact pointer position (`pos:800:600`) |

Example: `pos:400:300, mouse0, wait:1s, move:50:0, mouse0`. The whole sequence runs once per action of every trigger.

Exact positions work on Windows and with the xtest backend. With uinput (and the root helper) Intool needs the screen size, which it detects itself on X11 and on Wayland (Hyprland, Sway, KDE, wlroots compositors with `wlr-randr`, and GNOME), or you can set the **Screen size** setting (`1920x1080`). Multi-monitor layouts are measured from the top-left corner, so monitors at negative offsets need the setting.

### Wayland

Wayland gives applications no way to read the pointer position, so Intool combines three things:

- **Placing the pointer** works everywhere: `pos:X:Y` goes through a virtual absolute pointer, which any Wayland compositor accepts through libinput. It needs the screen size (detected, or set manually).
- **Reading the pointer** (for recording absolute positions and exact `move:` steps) uses a helper command. With `auto`, Intool tries `hyprctl cursorpos` (Hyprland), `kdotool getmouselocation` (KDE) and `wl-find-cursor -p` (Sway and other wlroots compositors, built from [wl-find-cursor](https://github.com/cjacker/wl-find-cursor)) when installed. GNOME offers no such command without a shell extension. For anything else set **Pointer command** to a command that prints `X Y` (the first two integers are used), for example `--pointer-command "hyprctl cursorpos"`.
- **Without a reader**, recording falls back to relative movement read from `/dev/input` (mice and touchpads) and is stored as `move:` steps. With **Pointer coordinates** set to `auto` or `absolute`, Intool instead estimates the pointer so that you still get `pos:` steps: it moves the pointer to the screen center when the recording starts, adds up the motion of mice and touchpads it reads from `/dev/input` and clamps it to the screen. The result is exact only where the compositor applies no pointer acceleration, so treat it as an approximation. `auto` falls back to `move:` steps when the estimate is not possible (for example when the screen size or uinput is missing), `absolute` reports an error, and `relative` never moves the pointer. A reader below is always preferred and is exact. After a `pos:` step, later `move:` steps are tracked exactly; a sequence that starts with `move:` is sent as raw device movement, which the compositor's pointer acceleration may scale. Start such sequences with a `pos:` step for exact results.

Reading `/dev/input` and writing to `/dev/uinput` need the permissions described under Linux setup.

**Relative movement** (Settings): `auto` converts `move:` into an exact position when the pointer can be read (Windows, X11), so it is not affected by pointer acceleration, and otherwise sends raw device movement; `exact` requires the former; `device` always sends raw movement, which pointer acceleration may scale.

A hotkey is one or more keys joined with `+`, for example `f8`, `ctrl+f6` or `ctrl+k+c`; it fires when the last key is pressed while the others are held.

The gear button opens Settings:

- **Theme**: Auto, Dark Slate, Light Slate, or OLED Black
- **Key state**: press, release, or press & release
- **Scroll device**: wheel, touchpad, or pinch
- **Interval**: hold time for press & release (default `100ms`)
  With uinput, libinput ignores a mouse button press that follows a release by less than about 25 ms and merges the two clicks into one long hold. Intool therefore keeps at least 30 ms between a release and the next press of the same button. For a fast clicker use a short Interval: `Delay 40ms` with `Interval 10ms` gives a 50 ms click period, while `Interval 100ms` makes the period at least 130 ms.
- **Scroll value**: scroll amount, where `120` is one wheel notch
- **Act immediately on start** (on by default): perform the action as soon as the job starts instead of waiting for the first delay
- **Hotkey order**: `modifiers first` fires only when the modifiers are already held; `any order` also fires when a modifier is pressed last
- **Relative movement** and **Screen size**: see Action sequences
- **System** (button): minimizing to the tray, the daily schedule, and opening on system startup (see below)
- **Extra Hotkeys** (button): more hotkeys, each running its own action (see below)

The gear next to **Start Recording** holds the recording settings: recording hotkey, pointer capture (`off`, `at events`, `path` with a sample interval), coordinates (`auto`, `absolute`, `relative`), recorded waits and their minimum, leaving out the Stop Recording click, and dropping keys still held at the end.
- **Input backend** (Linux): Auto, uinput, xtest, or root helper

The hotkey field is read-only while the listener is running. Stop the listener to change it.

### Extra hotkeys

**Extra Hotkeys** takes one `HOTKEY = ACTION` per line, for example `f7 = space, wait:1s, a`. Each hotkey toggles its own action with the delay, repeat and key state settings of the main window. Pressing another hotkey while a job runs switches to that action, and pressing the active one again stops it. On the command line use `-B f7=space` (repeatable).

### Tray, schedule and startup

- **Minimize to tray** hides the window in the system tray when it is minimized; the tray menu shows the window, starts or stops the job, or quits. It needs the optional `pystray` and `Pillow` packages (`pip install pystray pillow`, already included in the release builds). Without them the window minimizes normally.
- **Start and stop on a daily schedule** starts the job at the daily start time and stops it at the daily stop time, as `HH:MM` in local time. Either time may be left empty. When Intool opens between the two times, the job starts at once. In the terminal (`-n`) Intool keeps running between runs.
- **Open on system startup** adds Intool to the login items (`~/.config/autostart/intool.desktop` on Linux, the `Run` registry key of the current user on Windows). **Startup arguments** are passed on every start, for example `--minimized --listen` to open in the tray with the listener on, or `--minimized --start` to start the job as well; they are checked against the command line options before they are saved.

Default hotkey: **F8** (recording: **F10**).

### Command line

```sh
intool -n -k f8 -a mouse0 -d 100ms                    # toggle left clicks with F8
intool -n -s -a space -d 1s -r 10                     # press space 10 times, then exit
intool -n -S press -a shift -s                        # hold shift until stopped
intool -n -S "press & release" -i 30ms -a space -s -r 5
intool -n -a scrollup -D pinch -v 240 -d 200ms -s -r 20   # zoom in with Ctrl+wheel
intool -n -a "pos:400:300, mouse0, wait:1s, mouse0" -s -r 1   # action sequence
intool -n -k ctrl+k+c -a mouse0                       # hotkey of three keys held together
intool -n -a mouse0 -B f7=space -B "f6=a, wait:1s, b"  # more hotkeys for other actions
intool -n --schedule --schedule-start 09:00 --schedule-stop 17:30 -a mouse0   # run every day between 09:00 and 17:30
intool --autostart --autostart-arguments="--minimized --listen" --save   # open at login, minimized, listening
intool -R                                             # record; estimates pos: steps on Wayland without a pointer reader
intool --version                                     # print the version
intool --list-keys                                    # all key and button names
intool --check                                        # test which Linux backends work
intool --help                                         # all options
```

Record from the terminal with `intool -R` (stops at the recording hotkey, Ctrl+C or after `-R TIME`); the action is printed and `--save` keeps it. Every setting above has a flag of the same name (`--screen`, `--move-mode`, `--record-pointer`, `--no-record-waits`, ...), listed in `--help`.

Recording reads `/dev/input`, so it sees physical mouse buttons, mouse motion and touchpads. On Windows and X11 the real pointer position is used, so every pointing device is covered there. On Linux without a pointer reader, a touchpad is read directly: one finger moves the pointer (converted from the pad's physical size and scaled by libinput's touchpad factor, without the compositor's acceleration, so fast strokes replay a little short), a quick still tap is recorded as a left click, a two-finger tap as a right click and a three-finger tap as a middle click, and two-finger scrolling is ignored. Taps are recorded whether or not your desktop has tap-to-click enabled, and a physical clickpad press is never counted twice. Compositors accelerate fast strokes, which Intool cannot see, so it adds extra gain to fast finger movement. If replayed movement is consistently too long or too short on your system, tune **Touchpad speed** (`--record-touchpad-speed`; `1` is the estimate, `0.8` shortens it by a fifth) for slow strokes and **Touchpad acceleration** (`--record-touchpad-acceleration`; the gain reached at fast strokes, `1` for none) for fast ones. Tap-and-drag is not detected; hold the clickpad button for drags.

Pointer capture uses the real pointer position on Windows and X11. Elsewhere (for example Wayland) mouse movement is captured from `/dev/input` and recorded as relative `move:` steps; touchpads are read as described above.

Use `--no-immediate` to wait for the first delay. Options set on the command line are saved with `--save`. Use `--reset-config` to start from defaults, and `--config FILE` to use another config file.


## Configuration file

| Platform | Location |
| --- | --- |
| Linux | `~/.config/intool/intool-config.json` |
| Windows | `%APPDATA%\Intool\intool-config.json` |

## Building from source

Requires Python 3.9 or newer with Tk (`python3-tk` on Debian/Ubuntu). Intool has no third-party dependencies to install.

```sh
python3 intool.pyw            # run the GUI
python3 intool.pyw -n --help  # terminal mode

pip install pyinstaller
pyinstaller --onefile --name intool intool.pyw
```

## Testing

The test suite uses only the standard library. The GUI smoke tests need a display (on Linux, run them with `xvfb-run`).

```sh
python3 -m unittest discover -s tests -v
```

GitHub Actions runs the lint and tests on every push and pull request (`.github/workflows/tests.yml`).

## Releasing

Releases are built by `.github/workflows/release.yml`. Pushing a tag that starts with `v` builds all executables, creates `SHA256SUMS.txt`, and opens a **draft** release with the files attached. Review the draft on GitHub and publish it when ready.

```sh
git tag v1.0.0
git push origin v1.0.0
```

Tags containing a hyphen (for example `v1.1.0-beta.1`) are marked as pre-releases.

## Safety

Intool sends real input to whatever window has focus. Test with a harmless action first, and keep the Stop button or the hotkey within reach. Use it only on systems and in applications where automated input is allowed.

## License

Intool is released under the MIT License. See [LICENSE](LICENSE).

# Intool

Intool (Input Tool) is a hotkey-triggered input pulser. It repeatedly presses keys, mouse buttons, or scrolls the wheel, touchpad, or pinch gesture, and it can run from a small GUI or entirely from the terminal.

## Features

- Actions: any key, mouse button (`mouse0`–`mouse4`), key combinations (`ctrl+z`), or scrolling
- Action sequences: comma-separated steps with explicit `down:`/`up:`, `wait:`, relative `move:DX:DY` and exact `pos:X:Y` pointer steps
- Hotkey sequences such as `ctrl+k, ctrl+c`
- Recording: capture keys, mouse buttons and pointer positions into an action sequence
- Optional immediate action when the job starts, instead of after the first delay
- Key states: **press** (hold down), **release** (let go), or **press & release** (tap with a set interval)
- Scroll devices: **wheel** (notches), **touchpad** (smooth steps), or **pinch** (Ctrl held while scrolling)
- Delay between triggers, actions per trigger, and number of cycles (0 = forever)
- A separate global hotkey for each key state, toggling the job on and off
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

Run the executable without arguments. Fill in the delay, actions per trigger, repeat cycles, action, and hotkey, then press **Start**, or **Start Listening** to start and stop the job with the hotkey.

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
- **Reading the pointer** (for recording absolute positions and exact `move:` steps) uses a helper command. With `auto`, Intool tries `hyprctl cursorpos` (Hyprland) and `kdotool getmouselocation` (KDE) when installed. For anything else set **Pointer command** to a command that prints `X Y` (the first two integers are used), for example `--pointer-command "hyprctl cursorpos"`.
- **Without a reader**, recording falls back to relative movement read from `/dev/input` (mice only; touchpads are not tracked) and is stored as `move:` steps. After a `pos:` step, later `move:` steps are tracked exactly; a sequence that starts with `move:` is sent as raw device movement, which the compositor's pointer acceleration may scale. Start such sequences with a `pos:` step for exact results.

Reading `/dev/input` and writing to `/dev/uinput` need the permissions described under Linux setup.

**Relative movement** (Settings): `auto` converts `move:` into an exact position when the pointer can be read (Windows, X11), so it is not affected by pointer acceleration, and otherwise sends raw device movement; `exact` requires the former; `device` always sends raw movement, which pointer acceleration may scale.

A hotkey can also be a sequence of chords, for example `ctrl+k, ctrl+c`; each chord must follow within 1.5 seconds.

The gear button opens Settings:

- **Theme**: Auto, Dark Slate, Light Slate, or OLED Black
- **Key state**: press, release, or press & release. Each state keeps its own hotkey; the Hotkey field edits the selected state.
- **Scroll device**: wheel, touchpad, or pinch
- **Interval**: hold time for press & release (default `100ms`)
- **Scroll value**: scroll amount, where `120` is one wheel notch
- **Act immediately on start**: perform the action as soon as the job starts instead of waiting for the first delay
- **Hotkey order**: `modifiers first` fires only when the modifiers are already held; `any order` also fires when a modifier is pressed last
- **Relative movement** and **Screen size**: see Action sequences
- **Recording Settings** (button): recording hotkey, pointer capture (`off`, `at events`, `path` with a sample interval), coordinates (`auto`, `absolute`, `relative`), recorded waits and their minimum, leaving out the Stop Recording click, and dropping keys still held at the end
- **Input backend** (Linux): Auto, uinput, xtest, or root helper

The hotkey field is read-only while the listener is running. Stop the listener to change it.

Default hotkeys: press & release **F8**, press **F7**, release **F9**.

### Command line

```sh
intool -n -k f8 -a mouse0 -d 100ms                    # toggle left clicks with F8
intool -n -s -a space -d 1s -r 10                     # press space 10 times, then exit
intool -n -S press -a shift -s                        # hold shift until stopped
intool -n -S "press & release" -i 30ms -a space -s -r 5
intool -n -a scrollup -D pinch -v 240 -d 200ms -s -r 20   # zoom in with Ctrl+wheel
intool -n -a "pos:400:300, mouse0, wait:1s, mouse0" -s -r 1   # action sequence
intool -n -k "ctrl+k, ctrl+c" -a mouse0 -I            # hotkey sequence, click at once on start
intool --version                                     # print the version
intool --list-keys                                    # all key and button names
intool --check                                        # test which Linux backends work
intool --help                                         # all options
```

Record from the terminal with `intool -R` (stops at the recording hotkey, Ctrl+C or after `-R TIME`); the action is printed and `--save` keeps it. Every setting above has a flag of the same name (`--screen`, `--move-mode`, `--record-pointer`, `--no-record-waits`, ...), listed in `--help`.

Pointer capture uses the real pointer position on Windows and X11. Elsewhere (for example Wayland) mouse movement is captured from `/dev/input` and recorded as relative `move:` steps; touchpads are not tracked there.

Options set on the command line are saved with `--save`. Use `--reset-config` to start from defaults, and `--config FILE` to use another config file.


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

# Intool

Intool (Input Tool) is a hotkey-triggered input pulser. It repeatedly presses keys, mouse buttons, or scrolls the wheel, touchpad, or pinch gesture, and it can run from a small GUI or entirely from the terminal.

## Features

- Actions: any key, mouse button (`mouse0`–`mouse4`), key combinations (`ctrl+z`), or scrolling
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

Run the executable without arguments. Fill in the delay, actions per trigger, repeat cycles, action, and hotkey, then press **Start**, or **Start Listener** to start and stop the job with the hotkey.

The gear button opens Settings:

- **Theme**: Auto, Dark Slate, Light Slate, or OLED Black
- **Key state**: press, release, or press & release. Each state keeps its own hotkey; the Hotkey field edits the selected state.
- **Scroll device**: wheel, touchpad, or pinch
- **Interval**: hold time for press & release (default `100ms`)
- **Scroll value**: scroll amount, where `120` is one wheel notch
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
intool --version                                     # print the version
intool --list-keys                                    # all key and button names
intool --check                                        # test which Linux backends work
intool --help                                         # all options
```

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

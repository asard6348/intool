# Changelog

All notable changes to Intool are documented in this file.

## [1.1.0] - 2026-11-01

Compare: [v1.0.0...v1.1.0](https://github.com/asard6348/intool/compare/v1.0.0...v1.1.0)

### Added

- **Action sequences.** An action can now be a comma-separated list of steps, run once per action of every trigger:
  - `down:KEY` / `up:KEY` press or release keys and buttons explicitly
  - `wait:TIME` pauses (for example `wait:500ms`, `wait:2s`); Stop interrupts it
  - `move:DX:DY` moves the pointer relatively
  - `pos:X:Y` places the pointer at an exact position
- **Recording.** Capture keys, mouse buttons and pointer positions into an action sequence, from the GUI (**Start Recording** / **Stop Recording**) or the terminal (`intool -R`, with `--save` to keep the result).
  - Default recording hotkey is **F10**; it also starts a recording while listening.
  - A separate **Recording Settings** window covers pointer capture (`off`, `at events`, `path`), coordinates (`auto`, `absolute`, `relative`), recorded waits, leaving out the Stop Recording click, and dropping keys still held at the end.
  - Recording and listening can run together.
- **Multi-key hotkeys.** A hotkey can be several keys joined with `+` (`f8`, `ctrl+f6`, `ctrl+k+c`); it fires when the last key is pressed while the others are held. A **Hotkey order** setting chooses between `modifiers first` and `any order`.
- **Extra hotkeys.** Bind more hotkeys that each run their own action, one `HOTKEY = ACTION` per line in the GUI (for example `f7 = space, wait:1s, a`) or with the repeatable `-B f7=space` flag. Pressing another hotkey while a job runs switches to that action; pressing the active one stops it.
- **Act immediately on start** (`-I` / `--immediate`, `--no-immediate`): perform the action as soon as the job starts instead of waiting for the first delay.
- **System tray.** **Minimize to tray** hides the window in the tray, with a menu to show the window, start or stop the job, or quit. Needs the optional `pystray` and `Pillow` packages, which are bundled in the release builds. `--minimized` opens straight into the tray.
- **Daily schedule.** Start and stop the job automatically at set `HH:MM` times (`--schedule`, `--schedule-start`, `--schedule-stop`). If Intool opens between the two times, the job starts at once. In the terminal, Intool keeps running between runs.
- **Open on system startup.** Adds Intool to the login items (an autostart entry on Linux, the current user's `Run` registry key on Windows), with configurable startup arguments such as `--minimized --listen`. The arguments are validated before they are saved (`--autostart`, `--autostart-arguments`).
- **Live countdown** to the next trigger in the main window while a job runs.
- **Wayland support.**
  - `pos:X:Y` places the pointer through a virtual absolute pointer, which works on any compositor.
  - Screen size is detected on X11 and on Hyprland, Sway, KDE, wlroots compositors with `wlr-randr`, and GNOME; it can also be set manually (**Screen size** setting, `--screen`).
  - The pointer can be read through a helper command: with `auto`, Intool tries `hyprctl cursorpos`, `kdotool getmouselocation` and `wl-find-cursor -p`; any other command that prints `X Y` can be set with **Pointer command** (`--pointer-command`).
  - Without a reader, recording estimates the pointer from motion read from `/dev/input` so you still get `pos:` steps (approximate where the compositor applies pointer acceleration), or falls back to relative `move:` steps.
- **Touchpad recording on Linux** (without a pointer reader): touchpads are read directly, with one finger moving the pointer, a quick tap recorded as a left click, a two-finger tap as a right click and a three-finger tap as a middle click. Tuning options: **Touchpad speed** (`--record-touchpad-speed`) and **Touchpad acceleration** (`--record-touchpad-acceleration`).
- **Relative movement** setting (`auto`, `exact`, `device`) controlling how `move:` steps are sent, so they are not skewed by pointer acceleration where the pointer can be read (Windows, X11).
- Every setting now has a matching command-line flag (`--move-mode`, `--record-pointer`, `--no-record-waits`, and so on), listed in `--help`.

### Changed

- **One global hotkey.** The separate hotkeys for press, release, and press & release have been replaced by a single hotkey, **F8** by default. An existing config with per-state hotkeys keeps working: the hotkey stored for the selected key state is used, and the old `hotkeys` entry is dropped the next time settings are saved.
- **New defaults** for a fresh start:
  - Delay is **1 second** (was 5 seconds).
  - Repeat cycles is **1** (was 0, which means forever).
  - **Act immediately on start** is off.
  - **Record pointer** is `off`.
- The **Settings** window was reorganised into labelled sections with hints for each setting; recording options moved into their own **Recording Settings** window, and the gear next to **Start Recording** opens it.
- The release workflow now installs `pystray` and `pillow` and bundles `pystray`, so the tray works in the downloadable executables.
- The Linux test job runs on `ubuntu-24.04` instead of `ubuntu-latest`.
- The test suite grew from 60 to 278 tests.

### Fixed

- **Fast mouse clicks** now register with the uinput backend. libinput merges a press that follows a release by less than about 25 ms into one long hold, so Intool now keeps at least 30 ms between a release and the next press of the same button. For a fast clicker, use a short **Interval** (`Delay 40ms` with `Interval 10ms` gives a 50 ms click period).

## [1.0.0] - 2026-10-30

First release.

- Hotkey-triggered input pulser that repeatedly presses keys, mouse buttons, or scrolls the wheel, touchpad, or pinch gesture.
- GUI with light, dark, and OLED themes, plus a terminal mode (`-n`) for scripting.
- Key states: press, release, or press & release, with an interval.
- Delay between triggers, actions per trigger, and number of cycles.
- A global hotkey for each key state, toggling the job on and off.
- Linux (`uinput`, `xtest` and root helper backends) and Windows builds.
- MIT license.

[1.1.0]: https://github.com/asard6348/intool/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/asard6348/intool/releases/tag/v1.0.0

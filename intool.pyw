import argparse
import ctypes
import ctypes.util
import glob
import json
import math
import os
import queue
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from os.path import join as joinp

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = ttk = None

IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

if IS_LINUX:
    import fcntl

APP_NAME = "Intool"
APP_DIRNAME = "Intool"
VERSION = "1.0.0"
VIRTUAL_NAME = "Intool Virtual Device"
RESCAN_SECONDS = 3.0
DRAIN_READS = 256
SCROLL_NOTCH = 120
CHORD_TIMEOUT = 1.5

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES = 0, 1, 6, 8, 11
ABS_X, ABS_Y = 0, 1
POINTER_DX, POINTER_DY = -1, -2
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114

EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_DEV_SETUP = 0x405C5503
UI_ABS_SETUP = 0x401C5504
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EVIOCGNAME_256 = 0x81004506
EVIOCGBIT_TYPES = 0x80044520

UINPUT_PATHS = ("/dev/uinput", "/dev/input/uinput", "/dev/misc/uinput")

MOUSE_MAP = {
    "mouse0": BTN_LEFT, "mouse1": BTN_RIGHT, "mouse2": BTN_MIDDLE,
    "mouse3": BTN_SIDE, "mouse4": BTN_EXTRA,
}
MOUSE_CODES = frozenset(MOUSE_MAP.values())
MOUSE_TO_X = {BTN_LEFT: 1, BTN_MIDDLE: 2, BTN_RIGHT: 3, BTN_SIDE: 8, BTN_EXTRA: 9}
WIN_BUTTONS = {
    BTN_LEFT: (0x0002, 0x0004, 0),
    BTN_RIGHT: (0x0008, 0x0010, 0),
    BTN_MIDDLE: (0x0020, 0x0040, 0),
    BTN_SIDE: (0x0080, 0x0100, 0x0001),
    BTN_EXTRA: (0x0080, 0x0100, 0x0002),
}
WIN_MOUSE_VK = {BTN_LEFT: 0x01, BTN_RIGHT: 0x02, BTN_MIDDLE: 0x04, BTN_SIDE: 0x05, BTN_EXTRA: 0x06}
X_BUTTON_MASKS = ((0x100, BTN_LEFT), (0x200, BTN_MIDDLE), (0x400, BTN_RIGHT))
SCROLL_ACTIONS = {"scrollup": 1, "scrolldown": -1}

STATES = ("press", "release", "press & release")
DEVICES = ("wheel", "touchpad", "pinch")
OPTION_DEFAULTS = {"state": "press & release", "device": "wheel", "interval": "100ms", "value": "120"}
DEFAULT_HOTKEYS = {"press": "f7", "release": "f9", "press & release": "f8"}


KEY_ALIASES = {
    "escape": "esc", "return": "enter", "del": "delete", "pgup": "pageup",
    "pgdn": "pagedown", "arrowup": "up", "arrowdown": "down",
    "leftarrow": "left", "rightarrow": "right", "super": "win", "meta": "win",
}
GENERIC_KEYS = frozenset({"ctrl", "shift", "alt"})


def build_key_tables():
    table = {}
    for i, ch in enumerate("1234567890"):
        table[ch] = (2 + i, 0x30 + i)
    for start, row in ((16, "qwertyuiop"), (30, "asdfghjkl"), (44, "zxcvbnm")):
        for i, ch in enumerate(row):
            table[ch] = (start + i, ord(ch.upper()))
    for i in range(12):
        table[f"f{i + 1}"] = (59 + i if i < 10 else 87 + i - 10, 0x70 + i)
    table.update({
        "esc": (1, 0x1B), "minus": (12, 0xBD), "equal": (13, 0xBB),
        "backspace": (14, 0x08), "tab": (15, 0x09), "enter": (28, 0x0D),
        "space": (57, 0x20), "capslock": (58, 0x14),
        "home": (102, 0x24), "end": (107, 0x23), "pageup": (104, 0x21),
        "pagedown": (109, 0x22), "insert": (110, 0x2D), "delete": (111, 0x2E),
        "up": (103, 0x26), "down": (108, 0x28), "left": (105, 0x25), "right": (106, 0x27),
        "ctrl": (29, 0x11), "leftctrl": (29, 0xA2), "rightctrl": (97, 0xA3),
        "shift": (42, 0x10), "leftshift": (42, 0xA0), "rightshift": (54, 0xA1),
        "alt": (56, 0x12), "leftalt": (56, 0xA4), "rightalt": (100, 0xA5),
        "win": (125, 0x5B),
    })
    for alias, name in KEY_ALIASES.items():
        table[alias] = table[name]
    return table


def build_modifier_table():
    table = {
        "ctrl": ({29, 97}, {0x11, 0xA2, 0xA3}),
        "leftctrl": ({29}, {0xA2}), "rightctrl": ({97}, {0xA3}),
        "shift": ({42, 54}, {0x10, 0xA0, 0xA1}),
        "leftshift": ({42}, {0xA0}), "rightshift": ({54}, {0xA1}),
        "alt": ({56, 100}, {0x12, 0xA4, 0xA5}),
        "leftalt": ({56}, {0xA4}), "rightalt": ({100}, {0xA5}),
        "win": ({125, 126}, {0x5B, 0x5C}),
    }
    table["super"] = table["meta"] = table["win"]
    return table


_KEY_TABLE = build_key_tables()
_MOD_TABLE = build_modifier_table()
KEYS = {name: codes[1 if IS_WIN else 0] for name, codes in _KEY_TABLE.items()}
MODS = {name: sets[1 if IS_WIN else 0] for name, sets in _MOD_TABLE.items()}
MODIFIER_CODES = frozenset().union(*MODS.values())
CODE_NAMES = {code: name for name, code in MOUSE_MAP.items()}
CODE_NAMES.update(
    {code: name for name, code in KEYS.items() if name not in KEY_ALIASES and name not in GENERIC_KEYS}
)

DEFAULTS = {
    "h": "0", "m": "0", "s": "5", "ms": "0",
    "clicks": "1", "repeat": "0", "action": "mouse0",
}

ALIASES = {"helper": "root helper", "root": "root helper", "both": "press & release", "tap": "press & release"}

THEMES = {
    "Dark Slate": {
        "bg": "#101216", "fg": "#f0f3f6", "entry_bg": "#1a1e24", "border": "#323842",
        "btn": "#282e39", "btn_active": "#3a4454", "arrow": "#8b949e",
        "arrow_active": "#f0f3f6", "arrow_accent": "#3a4454",
        "idle": "#8b949e", "listening": "#58a6ff", "running": "#3fb950", "error": "#f85149",
    },
    "Light Slate": {
        "bg": "#f0f2f5", "fg": "#111418", "entry_bg": "#ffffff", "border": "#d1d5db",
        "btn": "#e5e7eb", "btn_active": "#cbd5e1", "arrow": "#4b5563",
        "arrow_active": "#111418", "arrow_accent": "#cbd5e1",
        "idle": "#6b7280", "listening": "#2563eb", "running": "#16a34a", "error": "#dc2626",
    },
    "OLED Black": {
        "bg": "#000000", "fg": "#ffffff", "entry_bg": "#121212", "border": "#333333",
        "btn": "#1e1e1e", "btn_active": "#2a2a2a", "arrow": "#888888",
        "arrow_active": "#ffffff", "arrow_accent": "#3a3a3a",
        "idle": "#888888", "listening": "#00ffff", "running": "#00ff66", "error": "#ff3344",
    },
}


class BackendError(Exception):
    pass


@dataclass(frozen=True)
class Job:
    delay: float
    repeats: int
    per_trigger: int
    steps: tuple
    hotkey: tuple = None
    state: str = "press & release"
    interval: float = 0.1
    value: int = 120
    device: str = "wheel"
    immediate: bool = False

    def specs(self):
        return [
            {
                "kind": kind, "payload": payload, "state": self.state,
                "interval": self.interval, "value": self.value, "device": self.device,
            }
            for kind, payload in self.steps
        ]

    def describe(self):
        cycles = self.repeats if self.repeats else "unlimited"
        parts = [f"every {self.delay:g}s", f"{self.per_trigger}x per trigger", f"state: {self.state}"]
        if self.state == "press & release":
            parts.append(f"interval {self.interval * 1000:g}ms")
        if any(kind == "scroll" for kind, _ in self.steps):
            parts.append(f"{self.device} {self.value}")
        if len(self.steps) > 1:
            parts.append(f"{len(self.steps)} steps")
        if self.immediate:
            parts.append("immediate")
        parts.append(f"cycles: {cycles}")
        return ", ".join(parts)


def plan(spec):
    kind, payload = spec["kind"], spec["payload"]
    if kind == "down":
        return [("key", code, True) for code in payload]
    if kind == "up":
        return [("key", code, False) for code in reversed(payload)]
    if kind == "wait":
        return [("wait", payload)]
    if kind == "move":
        return [("move", *payload)]
    if kind == "pos":
        return [("warp", *payload)]

    state, interval = spec["state"], spec["interval"]
    if kind == "press":
        downs = [("key", code, True) for code in payload]
        ups = [("key", code, False) for code in reversed(payload)]
        if state == "press":
            return downs
        if state == "release":
            return ups
        return downs + [("wait", interval)] + ups

    direction, value, device = payload, spec["value"], spec["device"]
    if device == "touchpad":
        gesture = [("wheel", direction * value, 0)]
    else:
        notches = max(1, round(value / SCROLL_NOTCH))
        gesture = [("wheel", direction * SCROLL_NOTCH * notches, direction * notches)]
    if device != "pinch":
        return [] if state == "release" else gesture
    ctrl = KEYS["leftctrl"]
    if state == "press":
        return [("key", ctrl, True)] + gesture
    if state == "release":
        return [("key", ctrl, False)]
    return [("key", ctrl, True)] + gesture + [("wait", interval), ("key", ctrl, False)]


def parse_codes(text):
    codes = []
    for part in (piece.strip() for piece in text.split("+")):
        if not part:
            continue
        code = MOUSE_MAP.get(part, KEYS.get(part))
        if code is None:
            return None
        codes.append(code)
    return codes or None


def parse_directive(head, rest):
    if head in ("down", "up"):
        codes = parse_codes(rest)
        return (head, codes) if codes else None
    if head == "wait":
        try:
            return ("wait", parse_duration(rest))
        except ValueError:
            return None
    if head in ("move", "pos"):
        point = re.fullmatch(r"\s*([+-]?\d+)\s*:\s*([+-]?\d+)\s*", rest)
        return (head, (int(point[1]), int(point[2]))) if point else None
    return None


def parse_action(text):
    s = (text or "").strip().lower()
    if s in SCROLL_ACTIONS:
        return ("scroll", SCROLL_ACTIONS[s])
    head, separator, rest = s.partition(":")
    if separator:
        return parse_directive(head.strip(), rest)
    codes = parse_codes(s)
    return ("press", codes) if codes else None


def parse_sequence(text):
    steps = [parse_action(part) for part in (text or "").split(",") if part.strip()]
    return tuple(steps) if steps and None not in steps else None


def parse_hotkey(text):
    parts = [p.strip() for p in (text or "").lower().split("+") if p.strip()]
    if not parts or parts[-1] not in KEYS:
        return None
    mod_sets = []
    for part in parts[:-1]:
        if part not in MODS:
            return None
        mod_sets.append(MODS[part])
    return mod_sets, KEYS[parts[-1]]


def parse_hotkeys(text):
    chords = [parse_hotkey(part) for part in (text or "").split(",") if part.strip()]
    return tuple(chords) if chords and None not in chords else None


def to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_duration(text):
    s = (text or "").strip().lower().replace(" ", "")
    error = ValueError(f"invalid duration '{text}' (examples: 500ms, 2s, 1m30s, 0.5)")
    if not s:
        raise error
    try:
        value = float(s)
    except ValueError:
        if not re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ms|h|m|s))+", s):
            raise error
        scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
        value = sum(float(n) * scale[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", s))
    if not math.isfinite(value) or value < 0:
        raise error
    return value


def split_duration(seconds):
    total = int(round(seconds * 1000))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return hours, minutes, secs, millis


def job_from_fields(fields, need_hotkey=True):
    try:
        h, m, s, ms = (float(fields[k] or 0) for k in ("h", "m", "s", "ms"))
        repeats = int(fields["repeat"] or 0)
        per_trigger = int(fields["clicks"] or 1)
        value = int(fields.get("value") or OPTION_DEFAULTS["value"])
    except ValueError:
        raise ValueError("Delay, actions, repeat and value fields must be numbers.")
    steps = parse_sequence(fields["action"])
    if steps is None:
        raise ValueError(f"Invalid action '{fields['action']}' (see --list-keys for the syntax).")
    hotkey = parse_hotkeys(fields["hotkey"])
    if hotkey is None and need_hotkey:
        raise ValueError(f"Unknown key in the hotkey '{fields['hotkey']}'.")
    state = match_choice(fields.get("state"), STATES)
    if state is None:
        raise ValueError(f"Unknown key state '{fields.get('state')}'.")
    device = match_choice(fields.get("device"), DEVICES)
    if device is None:
        raise ValueError(f"Unknown scroll device '{fields.get('device')}'.")
    try:
        interval = parse_duration(fields.get("interval") or OPTION_DEFAULTS["interval"])
    except ValueError as exc:
        raise ValueError(f"Interval: {exc}.")
    if value < 1:
        raise ValueError("Scroll value must be at least 1.")
    delay = max(0.001, h * 3600 + m * 60 + s + ms / 1000.0)
    immediate = to_bool(fields.get("immediate", False))
    return Job(
        delay, max(0, repeats), max(1, per_trigger), steps, hotkey, state, interval, value, device, immediate,
    )


def match_choice(text, choices):
    low = (text or "").strip().lower()
    low = ALIASES.get(low, low)
    for choice in choices:
        if choice.lower() == low:
            return choice
    hits = [c for c in choices if c.lower().startswith(low)]
    return hits[0] if low and len(hits) == 1 else None


def pick(value, choices, default):
    return match_choice(str(value or ""), choices) or default


HOTKEY_ORDERS = ("modifiers first", "any order")
MOVE_MODES = ("auto", "device", "exact")
POINTER_MODES = ("off", "at events", "path")
COORDINATES = ("auto", "absolute", "relative")


def parse_screen(text):
    s = (text or "").strip().lower()
    if s in ("", "auto"):
        return None
    size = re.fullmatch(r"(\d+)\s*x\s*(\d+)", s)
    if not size or not int(size[1]) or not int(size[2]):
        raise ValueError("use auto or WIDTHxHEIGHT, for example 1920x1080")
    return int(size[1]), int(size[2])


def parse_optional_hotkeys(text):
    if not (text or "").strip():
        return None
    chords = parse_hotkeys(text)
    if chords is None:
        raise ValueError(f"unknown key in '{text}'")
    return chords


def parse_command(text):
    s = (text or "").strip()
    if not s or s.lower() == "auto":
        return None
    try:
        return shlex.split(s)
    except ValueError as exc:
        raise ValueError(str(exc)) from None


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    default: object
    kind: str = "text"
    choices: tuple = ()
    parse: object = str
    group: str = "input"

    def clean(self, raw):
        raw = self.default if raw is None else raw
        if self.kind == "bool":
            return to_bool(raw)
        if self.kind == "choice":
            return pick(raw, self.choices, self.default)
        return str(raw)

    def value(self, raw):
        cleaned = self.clean(raw)
        if self.kind != "text":
            return cleaned
        try:
            return self.parse(cleaned)
        except ValueError as exc:
            raise ValueError(f"{self.label}: {exc}") from None


OPTIONS = (
    Option("hotkey_order", "Hotkey order", HOTKEY_ORDERS[0], "choice", HOTKEY_ORDERS),
    Option("move_mode", "Relative movement", MOVE_MODES[0], "choice", MOVE_MODES),
    Option("screen", "Screen size (auto or WxH)", "auto", parse=parse_screen),
    Option("pointer_command", "Pointer command (auto, or a command printing X Y)", "auto", parse=parse_command),
    Option("record_hotkey", "Recording hotkey (empty = none)", "f10",
           parse=parse_optional_hotkeys, group="recording"),
    Option("record_pointer", "Record pointer", POINTER_MODES[1], "choice", POINTER_MODES, group="recording"),
    Option("record_coordinates", "Pointer coordinates", COORDINATES[0], "choice", COORDINATES,
           group="recording"),
    Option("record_waits", "Record waits", True, "bool", group="recording"),
    Option("record_min_wait", "Minimum wait", "10ms", parse=parse_duration, group="recording"),
    Option("record_path_interval", "Path sample interval", "50ms", parse=parse_duration, group="recording"),
    Option("record_trim_click", "Leave out the Stop Recording click", True, "bool", group="recording"),
    Option("record_drop_held", "Drop keys still held at the end", True, "bool", group="recording"),
)


def clean_options(values):
    return {option.key: option.clean(values.get(option.key)) for option in OPTIONS}


def resolve_options(values):
    return {option.key: option.value(values.get(option.key)) for option in OPTIONS}


def valid_options(values):
    cleaned = clean_options(values)
    for option in OPTIONS:
        try:
            option.value(cleaned[option.key])
        except ValueError:
            cleaned[option.key] = option.clean(None)
    return cleaned


def resolve_hotkeys(data, state):
    hotkeys = dict(DEFAULT_HOTKEYS)
    stored = data.get("hotkeys")
    if isinstance(stored, dict):
        hotkeys.update({k: str(v) for k, v in stored.items() if k in STATES})
    elif data.get("hotkey"):
        hotkeys[state] = str(data["hotkey"])
    return hotkeys


def default_config_dir():
    home = os.path.expanduser("~")
    if IS_WIN:
        base = os.environ.get("APPDATA") or joinp(home, "AppData", "Roaming")
        return joinp(base, APP_DIRNAME)
    if sys.platform == "darwin":
        return joinp(home, "Library", "Application Support", APP_DIRNAME)
    base = os.environ.get("XDG_CONFIG_HOME") or joinp(home, ".config")
    return joinp(base, "intool")


def default_config_path():
    return joinp(default_config_dir(), "intool-config.json")


def read_config(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def detect_system_theme():
    try:
        if IS_WIN:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "Light Slate" if val == 1 else "Dark Slate"
        if sys.platform == "darwin":
            res = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True,
            )
            return "Dark Slate" if "Dark" in res.stdout else "Light Slate"
        for schema in ("org.gnome.desktop.interface", "org.freedesktop.appearance"):
            res = subprocess.run(
                ["gsettings", "get", schema, "color-scheme"],
                capture_output=True, text=True,
            )
            if "dark" in res.stdout.lower():
                return "Dark Slate"
    except Exception:
        pass
    return "Dark Slate"


def x11_session():
    if not os.environ.get("DISPLAY"):
        return False
    if os.environ.get("WAYLAND_DISPLAY"):
        return False
    return os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"


class Injector:
    screen = None

    def __init__(self):
        self.held = []

    def set_screen(self, size):
        self.screen = size

    def pulse(self, spec):
        for step in plan(spec):
            if step[0] == "key":
                self.key(step[1], step[2])
                self._track(step[1], step[2])
            elif step[0] == "wheel":
                self.wheel(step[1], step[2])
            elif step[0] == "move":
                self.move(step[1], step[2])
            elif step[0] == "warp":
                self.warp(step[1], step[2])
            else:
                self.flush()
                time.sleep(step[1])
        self.flush()

    def move(self, dx, dy):
        raise BackendError("relative pointer movement is not supported by this backend")

    def warp(self, x, y):
        raise BackendError("exact pointer position is not supported by this backend")

    def _track(self, code, down):
        if down and code not in self.held:
            self.held.append(code)
        elif not down and code in self.held:
            self.held.remove(code)

    def release_all(self):
        for code in reversed(self.held):
            self.key(code, False)
        self.held.clear()
        self.flush()

    def flush(self):
        pass


class UInputDevice(Injector):
    label = "uinput"

    def __init__(self):
        Injector.__init__(self)
        self.pointer = None
        self.fd = self._open_node()
        try:
            self._create()
        except Exception as exc:
            os.close(self.fd)
            self.fd = None
            raise BackendError(f"uinput device creation failed ({exc})") from exc

    @staticmethod
    def _open_node():
        errors = []
        for attempt in range(2):
            for path in UINPUT_PATHS:
                try:
                    return os.open(path, os.O_WRONLY)
                except OSError as exc:
                    errors.append(f"{path}: {exc.strerror}")
            if attempt == 0 and os.geteuid() == 0 and shutil.which("modprobe"):
                subprocess.run(["modprobe", "uinput"], capture_output=True)
                time.sleep(0.3)
                errors.clear()
                continue
            break
        raise BackendError("uinput unavailable (" + "; ".join(errors[:3]) + ")")

    def _create(self):
        fd = self.fd
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
        for code in list(range(1, 249)) + sorted(MOUSE_CODES):
            try:
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            except OSError:
                pass
        for code in (REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES):
            fcntl.ioctl(fd, UI_SET_RELBIT, code)
        name = VIRTUAL_NAME.encode()
        try:
            fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x1234, 0x5678, 1, name, 0))
        except OSError:
            legacy = struct.pack("80sHHHHI", name, 0x03, 0x1234, 0x5678, 1, 0)
            os.write(fd, legacy + bytes(4 * 64 * 4))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(0.4)

    def _emit(self, events):
        os.write(self.fd, b"".join(struct.pack(EVENT_FMT, 0, 0, t, c, v) for t, c, v in events))

    def key(self, code, down):
        self._emit([(EV_KEY, code, int(down)), (EV_SYN, 0, 0)])

    def wheel(self, hires, notch):
        events = [(EV_REL, REL_WHEEL_HI_RES, hires)]
        if notch:
            events.append((EV_REL, REL_WHEEL, notch))
        events.append((EV_SYN, 0, 0))
        self._emit(events)

    def move(self, dx, dy):
        self._emit([(EV_REL, REL_X, dx), (EV_REL, REL_Y, dy), (EV_SYN, 0, 0)])

    def warp(self, x, y):
        if self.pointer is None:
            if self.screen is None:
                raise BackendError("screen size unknown; set Screen size in Settings (for example 1920x1080)")
            self.pointer = UInputPointer(*self.screen)
        self.pointer.move_to(x, y)

    def close(self):
        if self.pointer is not None:
            self.pointer.close()
            self.pointer = None
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None


class UInputPointer(UInputDevice):
    def __init__(self, width, height):
        self.size = (width, height)
        super().__init__()

    def _create(self):
        fd = self.fd
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_LEFT)
        for axis, extent in zip((ABS_X, ABS_Y), self.size):
            fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
            fcntl.ioctl(fd, UI_ABS_SETUP, struct.pack("H2x6i", axis, 0, 0, extent - 1, 0, 0, 0))
        name = f"{VIRTUAL_NAME} Pointer".encode()
        fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x1234, 0x5679, 1, name, 0))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(0.4)

    def move_to(self, x, y):
        x = min(max(x, 0), self.size[0] - 1)
        y = min(max(y, 0), self.size[1] - 1)
        self._emit([(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y), (EV_SYN, 0, 0)])


class X11Connection:
    def __init__(self, session_only=True):
        if session_only and not x11_session():
            raise BackendError("not an X11 session")
        if not os.environ.get("DISPLAY"):
            raise BackendError("no X display available")
        name = ctypes.util.find_library("X11")
        if not name:
            raise BackendError("libX11 not found")
        self.lib = ctypes.CDLL(name)
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.lib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        self.lib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        window_p, int_p = ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int)
        self.lib.XQueryPointer.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, window_p, window_p,
            int_p, int_p, int_p, int_p, ctypes.POINTER(ctypes.c_uint),
        ]
        self.dpy = self.lib.XOpenDisplay(None)
        if not self.dpy:
            raise BackendError("cannot open X display")
        self.root = self.lib.XDefaultRootWindow(self.dpy)

    def screen_size(self):
        screen = self.lib.XDefaultScreen(self.dpy)
        return self.lib.XDisplayWidth(self.dpy, screen), self.lib.XDisplayHeight(self.dpy, screen)

    def pointer(self):
        windows = [ctypes.c_ulong() for _ in range(2)]
        ints = [ctypes.c_int() for _ in range(4)]
        mask = ctypes.c_uint()
        self.lib.XQueryPointer(
            self.dpy, self.root, *[ctypes.byref(v) for v in windows + ints], ctypes.byref(mask),
        )
        return ints[0].value, ints[1].value, mask.value

    def position(self):
        return self.pointer()[:2]

    def close(self):
        if self.dpy:
            self.lib.XCloseDisplay(self.dpy)
            self.dpy = None


class XTestInput(X11Connection, Injector):
    label = "xtest"

    def __init__(self):
        X11Connection.__init__(self)
        Injector.__init__(self)
        try:
            name = ctypes.util.find_library("Xtst")
            if not name:
                raise BackendError("libXtst not found")
            self.xtst = ctypes.CDLL(name)
            int_p = ctypes.POINTER(ctypes.c_int)
            self.xtst.XTestQueryExtension.argtypes = [ctypes.c_void_p, int_p, int_p, int_p, int_p]
            self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            self.xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            self.xtst.XTestFakeMotionEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
            ]
            self.xtst.XTestFakeRelativeMotionEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
            ]
            vals = [ctypes.c_int() for _ in range(4)]
            if not self.xtst.XTestQueryExtension(self.dpy, *[ctypes.pointer(v) for v in vals]):
                raise BackendError("XTEST extension missing")
        except BackendError:
            self.close()
            raise

    def key(self, code, down):
        if code in MOUSE_TO_X:
            self.xtst.XTestFakeButtonEvent(self.dpy, MOUSE_TO_X[code], int(down), 0)
        else:
            self.xtst.XTestFakeKeyEvent(self.dpy, code + 8, int(down), 0)

    def wheel(self, hires, notch):
        clicks = abs(notch) if notch else max(1, round(abs(hires) / SCROLL_NOTCH))
        button = 4 if hires > 0 else 5
        for _ in range(clicks):
            self.xtst.XTestFakeButtonEvent(self.dpy, button, 1, 0)
            self.xtst.XTestFakeButtonEvent(self.dpy, button, 0, 0)

    def move(self, dx, dy):
        self.xtst.XTestFakeRelativeMotionEvent(self.dpy, dx, dy, 0)

    def warp(self, x, y):
        self.xtst.XTestFakeMotionEvent(self.dpy, -1, x, y, 0)

    def flush(self):
        self.lib.XSync(self.dpy, 0)


class XKeyPoller(X11Connection):
    label = "x11-poll"

    def __init__(self):
        super().__init__()
        self._buf = (ctypes.c_ubyte * 32)()
        self._prev = self._snapshot()

    def _snapshot(self):
        self.lib.XQueryKeymap(self.dpy, self._buf)
        now = set()
        for index, byte in enumerate(self._buf):
            for bit in range(8):
                keycode = index * 8 + bit
                if keycode >= 8 and byte >> bit & 1:
                    now.add(keycode - 8)
        mask = self.pointer()[2]
        now.update(code for flag, code in X_BUTTON_MASKS if mask & flag)
        return now

    def set_motion(self, enabled):
        pass

    def poll(self, timeout):
        time.sleep(min(timeout, 0.015))
        now = self._snapshot()
        events = [(c, 1) for c in sorted(now - self._prev)]
        events += [(c, 0) for c in sorted(self._prev - now)]
        self._prev = now
        return events


class WindowsInput(Injector):
    label = "windows"

    def __init__(self):
        Injector.__init__(self)
        self.user32 = ctypes.windll.user32

    def key(self, code, down):
        if code in MOUSE_CODES:
            down_flag, up_flag, data = WIN_BUTTONS[code]
            self.user32.mouse_event(down_flag if down else up_flag, 0, 0, data, 0)
        else:
            self.user32.keybd_event(code, 0, 0 if down else 2, 0)

    def wheel(self, hires, notch):
        self.user32.mouse_event(0x0800, 0, 0, hires, 0)

    def move(self, dx, dy):
        self.user32.mouse_event(0x0001, dx, dy, 0, 0)

    def warp(self, x, y):
        self.user32.SetCursorPos(x, y)


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WindowsPointer:
    def __init__(self, user32=None):
        self.user32 = user32 or ctypes.windll.user32

    def position(self):
        point = Point()
        self.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def close(self):
        pass


class WindowsKeyPoller:
    label = "windows-poll"

    def __init__(self, user32=None):
        self.user32 = user32 or ctypes.windll.user32
        self.watched = {code: code for code in set(KEYS.values())}
        self.watched.update(WIN_MOUSE_VK.items())
        self._prev = self._snapshot()

    def _snapshot(self):
        state = self.user32.GetAsyncKeyState
        return {code for code, vk in self.watched.items() if state(vk) & 0x8000}

    def set_motion(self, enabled):
        pass

    def poll(self, timeout):
        time.sleep(min(timeout, 0.005))
        now = self._snapshot()
        events = [(c, 1) for c in sorted(now - self._prev)]
        events += [(c, 0) for c in sorted(self._prev - now)]
        self._prev = now
        return events

    def close(self):
        pass


class EvdevSource:
    label = "evdev"

    def __init__(self):
        self._fds = {}
        self._next_scan = 0.0
        self.motion = False
        self._scan()
        if not self._fds:
            raise BackendError(
                "no readable /dev/input/event* device (add your user to the 'input' group)"
            )

    @staticmethod
    def _accept(fd):
        try:
            buf = bytearray(256)
            fcntl.ioctl(fd, EVIOCGNAME_256, buf)
            if bytes(buf).split(b"\0", 1)[0].decode(errors="replace").startswith(VIRTUAL_NAME):
                return False
            caps = bytearray(4)
            fcntl.ioctl(fd, EVIOCGBIT_TYPES, caps)
            return bool(caps[0] & (1 << EV_KEY))
        except OSError:
            return False

    def _scan(self):
        self._next_scan = time.monotonic() + RESCAN_SECONDS
        known = set(self._fds.values())
        for path in glob.glob("/dev/input/event*"):
            if path in known:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            if self._accept(fd):
                self._fds[fd] = path
            else:
                os.close(fd)

    def _drop(self, fd):
        self._fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass

    def set_motion(self, enabled):
        self.motion = enabled

    def _read(self, fd):
        chunks = []
        for _ in range(DRAIN_READS):
            try:
                chunks.append(os.read(fd, EVENT_SIZE * 64))
            except BlockingIOError:
                break
            if not select.select([fd], [], [], 0)[0]:
                break
        return b"".join(chunks)

    def poll(self, timeout):
        if time.monotonic() >= self._next_scan:
            self._scan()
        if not self._fds:
            time.sleep(timeout)
            return []
        try:
            ready, _, _ = select.select(list(self._fds), [], [], timeout)
        except (OSError, ValueError):
            for fd in list(self._fds):
                self._drop(fd)
            self._next_scan = 0.0
            return []
        raw = []
        for fd in ready:
            try:
                data = self._read(fd)
            except OSError:
                self._drop(fd)
                continue
            for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                sec, usec, etype, code, value = struct.unpack_from(EVENT_FMT, data, offset)
                raw.append((sec, usec, etype, code, value))
        raw.sort(key=lambda event: event[:2])
        events = []
        moved = [0, 0]

        def flush_motion():
            for index, axis in enumerate((POINTER_DX, POINTER_DY)):
                if moved[index]:
                    events.append((axis, moved[index]))
                moved[index] = 0

        for _, _, etype, code, value in raw:
            if etype == EV_KEY and value in (0, 1):
                flush_motion()
                events.append((code, value))
            elif etype == EV_REL and self.motion and code in (REL_X, REL_Y):
                moved[code] += value
        flush_motion()
        return events

    def close(self):
        for fd in list(self._fds):
            self._drop(fd)


class HelperServer:
    def __init__(self, device):
        self.device = device
        self.source = None
        self.thread = None
        self.motion = False
        self.stop = threading.Event()
        self.out_lock = threading.Lock()

    def send(self, obj):
        with self.out_lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def _pump(self):
        while not self.stop.is_set():
            for code, value in self.source.poll(0.2):
                self.send({"ev": [code, value]})

    def start_listen(self):
        if self.thread is not None:
            self.send({"listen": True})
            return
        try:
            self.source = EvdevSource()
        except BackendError as exc:
            self.send({"listen": False, "error": str(exc)})
            return
        self.source.set_motion(self.motion)
        self.stop.clear()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()
        self.send({"listen": True})

    def stop_listen(self):
        if self.thread is None:
            return
        self.stop.set()
        self.thread.join(timeout=2)
        self.source.close()
        self.thread = None
        self.source = None

    def run(self):
        for line in sys.stdin:
            try:
                msg = json.loads(line)
                op = msg.get("op")
                if op == "pulse":
                    self.device.pulse(msg["spec"])
                elif op == "release":
                    self.device.release_all()
                elif op == "listen":
                    self.start_listen()
                elif op == "unlisten":
                    self.stop_listen()
                elif op == "motion":
                    self.motion = bool(msg["on"])
                    if self.source is not None:
                        self.source.set_motion(self.motion)
                elif op == "screen":
                    self.device.set_screen(tuple(msg["size"]))
                elif op == "quit":
                    break
            except Exception as exc:
                self.send({"err": str(exc)})
        self.stop_listen()
        self.device.release_all()
        self.device.close()


def helper_main():
    try:
        device = UInputDevice()
    except Exception as exc:
        sys.stdout.write(json.dumps({"ready": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
        return 1
    server = HelperServer(device)
    server.send({"ready": True})
    server.run()
    return 0


class HelperListener:
    def __init__(self, helper):
        self.helper = helper
        self.label = "root helper"

    def set_motion(self, enabled):
        self.helper.set_motion(enabled)

    def poll(self, timeout):
        return self.helper.poll(timeout)

    def close(self):
        self.helper.stop_listen()


class PrivilegedHelper:
    label = "root helper"

    def __init__(self):
        if IS_LINUX and os.geteuid() == 0:
            raise BackendError("already root and uinput is still unusable")
        if shutil.which("pkexec"):
            launcher = ["pkexec"]
        elif shutil.which("sudo"):
            launcher = ["sudo", "-n"]
        else:
            raise BackendError("neither pkexec nor sudo is available")
        helper_cmd = [sys.executable, "--helper"] if getattr(sys, "frozen", False) \
            else [sys.executable, os.path.abspath(__file__), "--helper"]
        argv = launcher + helper_cmd
        self._send_lock = threading.Lock()
        self._acks = queue.Queue()
        self._events = queue.Queue()
        self._error = None
        self.alive = True
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        threading.Thread(target=self._read_loop, daemon=True).start()
        try:
            ready = self._acks.get(timeout=120)
        except queue.Empty:
            self.close()
            raise BackendError("root helper did not start in time")
        if not ready.get("ready"):
            self.close()
            raise BackendError(ready.get("error") or "root helper was denied or failed")

    def _read_loop(self):
        try:
            for line in self._proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "ev" in msg:
                    self._events.put(tuple(msg["ev"]))
                elif "err" in msg:
                    self._error = msg["err"]
                else:
                    self._acks.put(msg)
        finally:
            self.alive = False
            self._acks.put({"ready": False, "listen": False, "error": "root helper exited"})

    def _send(self, obj):
        with self._send_lock:
            if not self.alive:
                raise BackendError("root helper is not running")
            try:
                self._proc.stdin.write(json.dumps(obj) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self.alive = False
                raise BackendError(f"root helper pipe failed ({exc})") from exc
        if self._error:
            err, self._error = self._error, None
            raise BackendError(err)

    def pulse(self, spec):
        self._send({"op": "pulse", "spec": spec})

    def release_all(self):
        self._send({"op": "release"})

    def set_screen(self, size):
        if size is not None:
            self._send({"op": "screen", "size": list(size)})

    def set_motion(self, enabled):
        self._send({"op": "motion", "on": bool(enabled)})

    def open_listener(self):
        while not self._acks.empty():
            self._acks.get_nowait()
        self._send({"op": "listen"})
        try:
            ack = self._acks.get(timeout=10)
        except queue.Empty:
            raise BackendError("root helper did not answer the listen request")
        if not ack.get("listen"):
            raise BackendError(ack.get("error") or "root helper cannot read input devices")
        return HelperListener(self)

    def stop_listen(self):
        try:
            self._send({"op": "unlisten"})
        except BackendError:
            pass

    def poll(self, timeout):
        events = []
        try:
            events.append(self._events.get(timeout=timeout))
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return events

    def close(self):
        try:
            self._send({"op": "quit"})
        except BackendError:
            pass
        self.alive = False
        try:
            self._proc.wait(timeout=2)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class LinuxInput:
    ORDER = {
        "Auto": ("uinput", "xtest", "helper"),
        "uinput": ("uinput",),
        "xtest": ("xtest",),
        "root helper": ("helper",),
    }
    MODES = tuple(ORDER)

    def __init__(self, mode="Auto"):
        self.mode = mode if mode in self.ORDER else "Auto"
        self.screen = None
        self._lock = threading.RLock()
        self._injector = None
        self._helper = None

    def set_mode(self, mode):
        with self._lock:
            self.mode = mode if mode in self.ORDER else "Auto"
            self.reset_injector()

    def set_screen(self, size):
        with self._lock:
            self.screen = size
            if self._injector is not None:
                self._injector.set_screen(size or probe_screen())

    def _get_helper(self):
        with self._lock:
            if self._helper is None or not self._helper.alive:
                self._helper = PrivilegedHelper()
            return self._helper

    def _first(self, factories):
        failures = []
        for kind in self.ORDER[self.mode]:
            try:
                return factories[kind]()
            except Exception as exc:
                failures.append(f"{kind}: {exc}")
        raise BackendError(
            "No working input backend.\n" + "\n".join(failures)
            + "\n\nFix: sudo modprobe uinput and grant access to /dev/uinput, "
            "or run under X11, or allow the root helper prompt."
        )

    def injector(self):
        with self._lock:
            if self._injector is None:
                self._injector = self._first({
                    "uinput": UInputDevice,
                    "xtest": XTestInput,
                    "helper": self._get_helper,
                })
                self._injector.set_screen(self.screen or probe_screen())
            return self._injector

    def open_source(self):
        with self._lock:
            return self._first({
                "uinput": EvdevSource,
                "xtest": XKeyPoller,
                "helper": lambda: self._get_helper().open_listener(),
            })

    def release_all(self):
        with self._lock:
            if self._injector is not None:
                self._injector.release_all()

    def reset_injector(self):
        with self._lock:
            injector, self._injector = self._injector, None
            if injector is None:
                return
            if injector is self._helper:
                if not injector.alive:
                    injector.close()
                    self._helper = None
            else:
                injector.close()

    def shutdown(self):
        injector, self._injector = self._injector, None
        if injector is not None and injector is not self._helper:
            injector.close()
        if self._helper is not None:
            self._helper.close()
            self._helper = None


class HotkeyMatcher:
    def __init__(self, chords, any_order=False):
        self.chords = chords
        self.any_order = any_order
        self.index = 0
        self.deadline = 0.0

    def _completes(self, chord, code, pressed):
        mod_sets, target = chord
        if not all(pressed & mods for mods in mod_sets):
            return False
        if code == target:
            return True
        return self.any_order and target in pressed and any(code in mods for mods in mod_sets)

    def feed(self, code, pressed, now):
        if now > self.deadline:
            self.index = 0
        for index in dict.fromkeys((self.index, 0)):
            if self._completes(self.chords[index], code, pressed):
                self.index = index + 1
                self.deadline = now + CHORD_TIMEOUT
                if self.index == len(self.chords):
                    self.index = 0
                    return True
                return False
        if code not in MODIFIER_CODES:
            self.index = 0
        return False

    def codes(self):
        return {code for mod_sets, target in self.chords for mods in mod_sets for code in mods} | {
            target for _, target in self.chords
        }


@dataclass(frozen=True)
class RecordOptions:
    pointer: str = "at events"
    coordinates: str = "auto"
    waits: bool = True
    min_wait: float = 0.01
    path_interval: float = 0.05
    trim_click: bool = True
    drop_held: bool = True

    @classmethod
    def from_options(cls, options):
        return cls(
            options["record_pointer"], options["record_coordinates"], options["record_waits"],
            options["record_min_wait"], max(0.005, options["record_path_interval"]),
            options["record_trim_click"], options["record_drop_held"],
        )


TRAILING_STEPS = ("wait", "pos", "move")


class Recorder:
    def __init__(self, pointer=None, clock=time.monotonic, options=None):
        self.options = options or RecordOptions()
        self.pointer = pointer
        self.clock = clock
        self.lock = threading.Lock()
        self.steps = []
        self.held = set()
        self.last_time = None
        self.last_sample = None
        self.location = None
        self.pending = [0, 0]
        self.done = False
        self.tracking = self.options.pointer != "off"
        self.absolute = pointer is not None and self.options.coordinates != "relative"
        self.wants_motion = self.tracking and pointer is None
        if self.tracking and pointer is not None and not self.absolute:
            self.location = tuple(pointer.position())

    def poll_timeout(self):
        return self.options.path_interval / 2 if self.options.pointer == "path" else 0.1

    def motion(self, code, value):
        with self.lock:
            if self.wants_motion and not self.done:
                self.pending[0 if code == POINTER_DX else 1] += value

    def _stamp(self, now):
        if self.options.waits and self.last_time is not None and now - self.last_time >= self.options.min_wait:
            self.steps.append(("wait", now - self.last_time))
        self.last_time = now

    def _pointer_step(self):
        if not self.tracking:
            return None
        if self.pointer is None:
            delta, self.pending = tuple(self.pending), [0, 0]
            return ("move", delta) if any(delta) else None
        location = tuple(self.pointer.position())
        previous, self.location = self.location, location
        if self.absolute:
            return ("pos", location) if location != previous else None
        if previous is None or location == previous:
            return None
        return ("move", (location[0] - previous[0], location[1] - previous[1]))

    def _emit_pointer(self, now):
        step = self._pointer_step()
        if step is not None:
            self._stamp(now)
            self.steps.append(step)

    def feed(self, code, value):
        with self.lock:
            if self.done or code not in CODE_NAMES or (not value and code not in self.held):
                return
            now = self.clock()
            self._emit_pointer(now)
            self._stamp(now)
            if value:
                self.held.add(code)
            else:
                self.held.discard(code)
            self.steps.append(("down" if value else "up", [code]))

    def tick(self):
        with self.lock:
            if self.done or self.options.pointer != "path":
                return
            now = self.clock()
            if self.last_sample is not None and now - self.last_sample < self.options.path_interval:
                return
            self.last_sample = now
            self._emit_pointer(now)

    def _drop_final_click(self, steps):
        keys = [i for i, (kind, _) in enumerate(steps) if kind in ("down", "up")]
        if not keys:
            return
        kind, codes = steps[keys[-1]]
        if codes[0] not in MOUSE_CODES:
            return
        if kind == "down":
            del steps[keys[-1]:]
        elif len(keys) >= 2 and steps[keys[-2]] == ("down", codes):
            del steps[keys[-2]:]

    @staticmethod
    def _is_key(step):
        return step[0] in ("down", "up")

    @staticmethod
    def _pop_while(steps, test):
        while steps and test(steps[-1]):
            steps.pop()

    def _trimmed(self, by_click, chord):
        steps = list(self.steps)
        self._pop_while(steps, lambda step: step[0] in TRAILING_STEPS or step[1][0] in chord)
        if by_click and self.options.trim_click:
            self._drop_final_click(steps)
        if self.options.drop_held:
            for code in self.held:
                indices = [i for i, step in enumerate(steps) if step == ("down", [code])]
                if indices:
                    del steps[indices[-1]]
        self._pop_while(steps, lambda step: step[0] in TRAILING_STEPS)
        if self.options.pointer == "path" and not any(self._is_key(step) for step in steps):
            return self._path_only()
        return steps

    def _path_only(self):
        steps, waited = [], 0.0
        for step in self.steps:
            if step[0] == "wait":
                waited += step[1]
            elif not self._is_key(step):
                if waited:
                    steps.append(("wait", waited))
                    waited = 0.0
                steps.append(step)
        return steps

    def finish(self, by_click=False, chord=()):
        with self.lock:
            self.done = True
            steps = self._trimmed(by_click, set(chord))
            if self.pointer is not None:
                self.pointer.close()
        return ", ".join(format_step(step) for step in steps)


def format_step(step):
    kind, payload = step
    if kind == "wait":
        return f"wait:{round(payload * 1000)}ms"
    if kind in ("pos", "move"):
        return f"{kind}:{payload[0]}:{payload[1]}"
    return f"{kind}:" + "+".join(CODE_NAMES[code] for code in payload)


WAYLAND_POINTER_COMMANDS = (("hyprctl", "cursorpos"), ("kdotool", "getmouselocation"))


class CommandPointer:
    def __init__(self, argv):
        self.argv = argv
        self.position()

    def position(self):
        try:
            done = subprocess.run(self.argv, capture_output=True, text=True, timeout=1)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendError(f"pointer command '{self.argv[0]}' failed ({exc})") from exc
        numbers = re.findall(r"-?\d+", done.stdout)
        if done.returncode != 0 or len(numbers) < 2:
            raise BackendError(f"pointer command '{self.argv[0]}' did not print X and Y coordinates")
        return int(numbers[0]), int(numbers[1])

    def close(self):
        pass


def open_pointer(command=None):
    if command:
        return CommandPointer(command)
    if IS_WIN:
        return WindowsPointer()
    if not IS_LINUX:
        return None
    try:
        return X11Connection()
    except BackendError:
        pass
    for argv in WAYLAND_POINTER_COMMANDS:
        if shutil.which(argv[0]):
            try:
                return CommandPointer(list(argv))
            except BackendError:
                continue
    return None


def boxes_hyprland(text):
    boxes = []
    for monitor in json.loads(text):
        if monitor.get("disabled"):
            continue
        width, height = monitor["width"], monitor["height"]
        if monitor.get("transform", 0) % 2:
            width, height = height, width
        scale = monitor.get("scale") or 1
        boxes.append((monitor["x"], monitor["y"], width / scale, height / scale))
    return boxes


def boxes_sway(text):
    return [
        (o["rect"]["x"], o["rect"]["y"], o["rect"]["width"], o["rect"]["height"])
        for o in json.loads(text) if o.get("active")
    ]


def boxes_kscreen(text):
    boxes = []
    for output in json.loads(text)["outputs"]:
        if not output.get("enabled"):
            continue
        width, height = output["size"]["width"], output["size"]["height"]
        if output.get("rotation") in (2, 8):
            width, height = height, width
        scale = output.get("scale") or 1
        boxes.append((output["pos"]["x"], output["pos"]["y"], width / scale, height / scale))
    return boxes


def boxes_wlr(text):
    boxes = []
    for output in json.loads(text):
        if not output.get("enabled"):
            continue
        mode = next(m for m in output["modes"] if m.get("current"))
        width, height = mode["width"], mode["height"]
        if str(output.get("transform", "normal")) in ("90", "270", "flipped-90", "flipped-270"):
            width, height = height, width
        scale = output.get("scale") or 1
        boxes.append((output["position"]["x"], output["position"]["y"], width / scale, height / scale))
    return boxes


def boxes_mutter(text):
    modes = [
        (int(width), int(height))
        for width, height, props in re.findall(
            r"\('\d+x\d+@[\d.]+', (\d+), (\d+), [\d.]+, [\d.]+, \[[^\]]*\], \{([^}]*)\}\)", text,
        )
        if "'is-current': <true>" in props
    ]
    placements = re.findall(r"\((-?\d+), (-?\d+), ([\d.]+), uint32 (\d+), (?:true|false), \[\(", text)
    if not modes or len(modes) != len(placements):
        raise ValueError("unexpected monitor layout")
    layout = re.search(r"'layout-mode': <uint32 (\d)>", text)
    logical = layout is None or layout.group(1) == "1"
    boxes = []
    for (width, height), (x, y, scale, transform) in zip(modes, placements):
        if int(transform) % 2:
            width, height = height, width
        factor = float(scale) if logical else 1.0
        boxes.append((int(x), int(y), width / factor, height / factor))
    return boxes


SCREEN_PROBES = (
    (("hyprctl", "monitors", "-j"), boxes_hyprland),
    (("swaymsg", "-t", "get_outputs", "-r"), boxes_sway),
    (("kscreen-doctor", "-j"), boxes_kscreen),
    (("wlr-randr", "--json"), boxes_wlr),
    (("gdbus", "call", "--session", "--dest", "org.gnome.Mutter.DisplayConfig",
      "--object-path", "/org/gnome/Mutter/DisplayConfig",
      "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"), boxes_mutter),
)


def screen_extent(boxes):
    if not boxes:
        return None
    return round(max(x + w for x, y, w, h in boxes)), round(max(y + h for x, y, w, h in boxes))


def wayland_screen():
    for argv, parse in SCREEN_PROBES:
        if not shutil.which(argv[0]):
            continue
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, timeout=2)
            size = screen_extent(parse(done.stdout)) if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, StopIteration):
            size = None
        if size:
            return size
    return None


def probe_screen():
    if IS_LINUX and os.environ.get("WAYLAND_DISPLAY"):
        size = wayland_screen()
        if size:
            return size
    try:
        probe = X11Connection(session_only=False)
    except BackendError:
        return None
    try:
        return probe.screen_size()
    finally:
        probe.close()


class Engine:
    def __init__(self, notify, backend="Auto", on_hotkey=None):
        self.notify = notify
        self.on_hotkey = on_hotkey
        self.linux = LinuxInput(backend) if IS_LINUX else None
        self.windows = WindowsInput() if IS_WIN else None
        self.job = None
        self.running = False
        self.listening = False
        self.recording = False
        self.record_options = RecordOptions()
        self.record_chords = None
        self.any_order = False
        self.move_mode = MOVE_MODES[0]
        self.pointer_command = None
        self._screen = False
        self._hotkey = None
        self._recorder = None
        self._monitor_running = False
        self._cursor = None
        self._locator = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.configure({})

    def _emit(self, event, *args):
        try:
            self.notify(event, *args)
        except Exception:
            pass

    def set_backend(self, mode):
        if self.linux:
            self.linux.set_mode(mode)

    def configure(self, values):
        options = resolve_options(values)
        self.record_options = RecordOptions.from_options(options)
        self.record_chords = options["record_hotkey"]
        self.any_order = options["hotkey_order"] == HOTKEY_ORDERS[1]
        self.move_mode = options["move_mode"]
        self.pointer_command = options["pointer_command"]
        if self.linux and options["screen"] != self._screen:
            self._screen = options["screen"]
            self.linux.set_screen(self._screen)

    def _locate(self):
        if self._cursor is not None:
            return self._cursor
        if self._locator is None:
            self._locator = open_pointer(self.pointer_command)
        return tuple(self._locator.position()) if self._locator is not None else None

    def _resolve_move(self, spec):
        if spec["kind"] == "pos":
            self._cursor = tuple(spec["payload"])
            return spec
        if spec["kind"] != "move":
            return spec
        position = None if self.move_mode == "device" else self._locate()
        if position is None:
            if self.move_mode == "exact":
                raise BackendError("exact relative movement needs a readable pointer position (X11 or Windows)")
            self._cursor = None
            return spec
        target = (position[0] + spec["payload"][0], position[1] + spec["payload"][1])
        self._cursor = target
        return {**spec, "kind": "pos", "payload": target}

    def _pulse(self, spec):
        self._execute(self._resolve_move(spec))

    def _execute(self, spec):
        if IS_LINUX:
            try:
                self.linux.injector().pulse(spec)
            except (OSError, BackendError):
                self.linux.reset_injector()
                self.linux.injector().pulse(spec)
        elif IS_WIN:
            self.windows.pulse(spec)
        else:
            raise BackendError("Input injection is not supported on this platform.")

    def _release(self):
        try:
            if IS_LINUX:
                self.linux.release_all()
            elif IS_WIN:
                self.windows.release_all()
        except Exception:
            pass

    def start(self, job=None):
        with self._lock:
            if self.running or self.recording:
                return False
            if job is not None:
                self.job = job
            if self.job is None:
                raise ValueError("No job configured.")
            job = self.job
            self.running = True
            self._stop = threading.Event()
            stop = self._stop
        threading.Thread(target=self._run, args=(stop, job), daemon=True).start()
        self._emit("state")
        return True

    def stop(self):
        with self._lock:
            was_running = self.running
            self.running = False
            self._stop.set()
        if was_running:
            self._emit("state")

    def toggle(self):
        if self.running:
            self.stop()
            return
        try:
            self.start()
        except ValueError as exc:
            self._emit("error", "Invalid Input", str(exc))

    def _play(self, specs, stop):
        self._cursor = None
        for spec in specs:
            if stop.is_set():
                return False
            if spec["kind"] == "wait":
                if stop.wait(spec["payload"]):
                    return False
            else:
                self._pulse(spec)
        return True

    def _run(self, stop, job):
        completed = False
        specs = job.specs()
        try:
            if self.linux is not None:
                self._emit("backend", self.linux.injector().label)
            deadline = time.monotonic() + (0.0 if job.immediate else job.delay)
            count = 0
            while not stop.is_set() and (job.repeats == 0 or count < job.repeats):
                if stop.wait(max(0.0, deadline - time.monotonic())):
                    break
                for _ in range(job.per_trigger):
                    if not self._play(specs, stop):
                        break
                    if job.per_trigger > 1 and stop.wait(0.05):
                        break
                count += 1
                deadline = max(deadline + job.delay, time.monotonic())
            completed = not stop.is_set()
        except Exception as exc:
            self._emit("error", "Input Error", str(exc))
        finally:
            with self._lock:
                owner = self._stop is stop
                if owner:
                    self.running = False
            if owner:
                self._release()
                if completed:
                    self._emit("finished")
                self._emit("state")

    def start_listener(self, hotkey):
        with self._lock:
            if self.listening:
                return False
            self.listening = True
            self._hotkey = hotkey
        self._ensure_monitor()
        self._emit("state")
        return True

    def set_hotkey(self, hotkey):
        self._hotkey = hotkey

    def stop_listener(self):
        with self._lock:
            was_listening = self.listening
            self.listening = False
        self.stop()
        if was_listening:
            self._emit("state")

    def _fire(self):
        (self.on_hotkey or self.toggle)()

    def _open_source(self):
        if IS_LINUX:
            source = self.linux.open_source()
            self._emit("backend", source.label)
            return source
        if IS_WIN:
            return WindowsKeyPoller()
        raise BackendError("Input capture is not supported on this platform.")

    def _ensure_monitor(self):
        with self._lock:
            if self._monitor_running or not (self.listening or self.recording):
                return
            self._monitor_running = True
        threading.Thread(target=self._monitor, daemon=True).start()

    def _monitor(self):
        failed = False
        try:
            source = self._open_source()
            try:
                self._pump(source)
            finally:
                source.close()
        except Exception as exc:
            failed = True
            self._emit("error", "Capture Error", str(exc))
        finally:
            with self._lock:
                self._monitor_running = False
                if failed:
                    self.listening = False
                    recorder, self._recorder = self._recorder, None
                    self.recording = False
            if failed and recorder is not None:
                recorder.finish()
            self._emit("state")
            self._ensure_monitor()

    def _matcher(self, current, chords):
        if not chords:
            return None
        if current is None or current.chords is not chords or current.any_order != self.any_order:
            return HotkeyMatcher(chords, self.any_order)
        return current

    def _pump(self, source):
        pressed = set()
        job_matcher = record_matcher = None
        motion = None
        while self.listening or self.recording:
            recorder = self._recorder
            wants_motion = recorder is not None and recorder.wants_motion
            if wants_motion != motion:
                source.set_motion(wants_motion)
                motion = wants_motion
            job_matcher = self._matcher(job_matcher, self._hotkey if self.listening else None)
            record_matcher = self._matcher(record_matcher, self.record_chords)
            for code, value in source.poll(recorder.poll_timeout() if recorder else 0.25):
                recorder = self._recorder
                if code < 0:
                    if recorder is not None:
                        recorder.motion(code, value)
                    continue
                if value:
                    pressed.add(code)
                else:
                    pressed.discard(code)
                if recorder is not None:
                    recorder.feed(code, value)
                if not value:
                    continue
                now = time.monotonic()
                if job_matcher and job_matcher.feed(code, pressed, now) and not self.recording:
                    self._fire()
                if record_matcher and record_matcher.feed(code, pressed, now):
                    self._toggle_recording(record_matcher)
            recorder = self._recorder
            if recorder is not None:
                recorder.tick()

    def _toggle_recording(self, matcher):
        if self.recording:
            self.stop_recording(chord=matcher.codes())
        else:
            self.start_recording()

    def start_recording(self):
        with self._lock:
            if self.recording or self.running:
                return False
            options = self.record_options
            pointer = None
            try:
                if options.pointer != "off":
                    pointer = open_pointer(self.pointer_command)
                    if pointer is None and options.coordinates == "absolute":
                        raise BackendError(
                            "Absolute pointer positions are not available here; "
                            "use Pointer coordinates: auto or relative."
                        )
                recorder = Recorder(pointer, options=options)
            except BackendError as exc:
                if pointer is not None:
                    pointer.close()
                self._emit("error", "Recording Error", str(exc))
                return False
            self._recorder = recorder
            self.recording = True
        self._ensure_monitor()
        self._emit("state")
        return True

    def stop_recording(self, by_click=False, chord=()):
        with self._lock:
            recorder, self._recorder = self._recorder, None
            was_recording = self.recording
            self.recording = False
        if not was_recording or recorder is None:
            return None
        text = recorder.finish(by_click, chord)
        self._emit("recorded", text)
        self._emit("state")
        return text

    def shutdown(self):
        with self._lock:
            self.listening = False
            recorder, self._recorder = self._recorder, None
            self.recording = False
        if recorder is not None:
            recorder.finish()
        self.stop()
        if self._locator is not None:
            self._locator.close()
            self._locator = None
        if self.linux:
            self.linux.shutdown()


class App:
    def __init__(self, root, config_path=None, overrides=None, auto_listen=False, auto_start=False):
        self.root = root
        self.root.title(APP_NAME)
        self.root.resizable(True, True)
        self.auto_listen = auto_listen
        self.auto_start = auto_start

        self._themed = []
        self._combos = []
        self.entries = {}
        self.labels = {}

        self.configs = config_path or default_config_path()
        self.cfgdir = os.path.dirname(self.configs)
        data = read_config(self.configs)
        data.update(overrides or {})

        theme = data.get("theme", "Auto")
        self.theme_selection = theme if theme == "Auto" or theme in THEMES else "Auto"
        self.remember_pos_var = tk.BooleanVar(value=bool(data.get("remember_pos", True)))
        geometry = data.get("geometry_pos", "")
        self.saved_geometry = geometry if isinstance(geometry, str) else ""
        size = data.get("size", "")
        self.saved_size = size if isinstance(size, str) else ""
        backend = data.get("backend", "Auto")
        self.backend_selection = backend if backend in LinuxInput.MODES else "Auto"
        self.state_selection = pick(data.get("state"), STATES, OPTION_DEFAULTS["state"])
        self.device_selection = pick(data.get("device"), DEVICES, OPTION_DEFAULTS["device"])
        self.interval_var = tk.StringVar(value=str(data.get("interval", OPTION_DEFAULTS["interval"])))
        self.value_var = tk.StringVar(value=str(data.get("value", OPTION_DEFAULTS["value"])))
        self.immediate_var = tk.BooleanVar(value=to_bool(data.get("immediate", False)))
        options = valid_options(data)
        self.option_vars = {
            option.key: (tk.BooleanVar if option.kind == "bool" else tk.StringVar)(value=options[option.key])
            for option in OPTIONS
        }
        self.hotkeys = resolve_hotkeys(data, self.state_selection)

        self.engine = Engine(
            self._engine_event, self.backend_selection,
            on_hotkey=lambda: self._engine_event("hotkey"),
        )
        self.engine.configure(options)

        self.resolve_and_apply_theme()
        self.build_ui()
        for key, default in DEFAULTS.items():
            self.set_entry(key, data.get(key, default))
        self.set_entry("hotkey", self.hotkeys[self.state_selection])
        self.update_hotkey_title()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.bind_all("<Button-1>", self._on_root_click, add="+")
        self._apply_saved_geometry()

        if self.auto_listen:
            self.root.after(150, self.toggle_listener)
        if self.auto_start:
            self.root.after(400, self.start_clicking)

    def _engine_event(self, event, *args):
        try:
            self.root.after(0, self._handle_engine_event, event, *args)
        except (tk.TclError, RuntimeError):
            pass

    def _handle_engine_event(self, event, *args):
        if event == "state":
            self.sync_state()
        elif event == "backend":
            self.set_backend_label(args[0])
        elif event == "error":
            self.show_error_popup(*args)
        elif event == "recorded":
            self.apply_recording(args[0])
        elif event == "hotkey":
            self.toggle_clicking()

    def _themed_widget(self, widget, kind):
        self._themed.append((widget, kind))
        self._apply_widget_theme(widget, kind)
        return widget

    def _apply_widget_theme(self, widget, kind):
        if kind in ("frame", "window"):
            widget.configure(bg=self.bg_color)
        elif kind == "label":
            widget.configure(bg=self.bg_color, fg=self.fg_color)
        elif kind == "entry":
            widget.configure(
                bg=self.entry_bg, fg=self.fg_color, insertbackground=self.fg_color,
                highlightbackground=self.border_color, highlightcolor=self.border_color,
                disabledbackground=self.entry_bg, disabledforeground=self.arrow_color,
            )
        elif kind == "text":
            widget.configure(
                bg=self.entry_bg, fg=self.fg_color, insertbackground=self.fg_color,
                highlightbackground=self.border_color, highlightcolor=self.border_color,
            )
        elif kind == "button":
            widget.configure(
                bg=self.btn_bg, fg=self.fg_color,
                activebackground=self.btn_active, activeforeground=self.fg_color,
            )
        elif kind == "check":
            widget.configure(
                bg=self.bg_color, fg=self.fg_color, selectcolor=self.entry_bg,
                activebackground=self.bg_color, activeforeground=self.fg_color,
            )
        elif kind == "heading":
            widget.configure(bg=self.bg_color, fg=self.status_listening)
        elif kind == "muted":
            widget.configure(bg=self.bg_color, fg=self.arrow_color)
        elif kind == "separator":
            widget.configure(bg=self.border_color)

    def _frame(self, parent, **kw):
        return self._themed_widget(tk.Frame(parent, **kw), "frame")

    def _label(self, parent, text, bold=False):
        label = tk.Label(
            parent, text=text, justify=tk.CENTER,
            font=("Arial", 10, "bold" if bold else "normal"),
        )
        return self._themed_widget(label, "label")

    def _entry(self, parent, width):
        entry = tk.Entry(
            parent, width=width, justify=tk.CENTER, font=("Arial", 10),
            relief=tk.FLAT, highlightthickness=1,
        )
        return self._themed_widget(entry, "entry")

    def _settings_entry(self, parent, variable):
        entry = tk.Entry(
            parent, textvariable=variable, width=1, justify=tk.LEFT, font=("Arial", 10),
            relief=tk.FLAT, highlightthickness=1,
        )
        entry.bind("<FocusOut>", lambda _: self.commit_settings())
        entry.bind("<Return>", lambda _: self.commit_settings())
        return self._themed_widget(entry, "entry")

    def _button(self, parent, text, command, font=("Arial", 10, "bold"), state="normal", **kw):
        button = tk.Button(
            parent, text=text, command=command, font=font, state=state,
            relief=tk.FLAT, highlightthickness=0, bd=0, cursor="hand2", **kw,
        )
        return self._themed_widget(button, "button")

    def _set_minsize(self, win):
        win.update_idletasks()
        win.minsize(win.winfo_reqwidth(), win.winfo_reqheight())

    def _window(self, title):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.resizable(True, True)
        win.transient(self.root)
        self._themed_widget(win, "window")
        try:
            win.grab_set()
        except tk.TclError:
            pass
        return win

    def _combo(self, parent, values, current, handler):
        combo = ttk.Combobox(parent, values=list(values), state="readonly", width=1)
        combo.set(current)
        combo.bind("<<ComboboxSelected>>", handler)
        self._combos.append(combo)
        self._style_combobox_popdown(combo)
        return combo

    def _field(self, parent, key, title):
        cell = self._frame(parent)
        label = self._label(cell, title, bold=True)
        label.configure(anchor="w")
        label.pack(fill=tk.X, pady=(0, 2))
        entry = self._entry(cell, 12)
        entry.pack(fill=tk.X, pady=(0, 10))
        self.entries[key] = entry
        self.labels[key] = label
        return cell

    def build_ui(self):
        main = self._frame(self.root, padx=20, pady=16)
        main.pack(fill=tk.BOTH, expand=True)
        self.main_frame = main
        main.columnconfigure((0, 1), weight=1, uniform="field")

        delay = self._frame(main)
        delay.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        delay.columnconfigure((0, 1, 2, 3), weight=1, uniform="delay")
        self._label(delay, "Delay:", bold=True).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))
        for col, (key, unit) in enumerate((("h", "h"), ("m", "min"), ("s", "s"), ("ms", "ms"))):
            cell = self._frame(delay)
            cell.grid(row=1, column=col, sticky="ew", padx=(0, 6) if col < 3 else 0)
            self._label(cell, unit).pack(anchor="w")
            entry = self._entry(cell, 6)
            entry.pack(fill=tk.X, pady=(0, 2))
            self.entries[key] = entry

        self._field(main, "clicks", "Actions per trigger:").grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self._field(main, "repeat", "Repeat cycles:").grid(row=1, column=1, sticky="ew")
        self._field(main, "hotkey", "Hotkey:").grid(row=2, column=0, sticky="ew", padx=(0, 10))
        self._field(main, "action", "Action:").grid(row=2, column=1, sticky="ew")

        self.listener_btn = self._button(
            main, "Start Listening", self.toggle_listener, font=("Arial", 9, "bold"), width=16,
        )
        self.listener_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.record_btn = self._button(
            main, "Start Recording", self.toggle_recording, font=("Arial", 9, "bold"), width=16,
        )
        self.record_btn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        btn_frame = self._frame(main)
        btn_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        btn_frame.columnconfigure((0, 1), weight=1, uniform="run")
        self.start_btn = self._button(btn_frame, "Start", self.start_clicking, width=10)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = self._button(btn_frame, "Stop", self.engine.stop, width=10, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew")

        status_row = self._frame(main)
        status_row.grid(row=6, column=0, columnspan=2, sticky="ew")
        self.status_label = self._label(status_row, "", bold=True)
        self.status_label.configure(anchor="w")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.info_btn = self._button(
            status_row, "ℹ", self.open_info, font=("Arial", 9), width=2, height=1,
        )
        self.info_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self.gear_btn = self._button(
            status_row, "⚙", self.open_settings, font=("Arial", 9), width=2, height=1,
        )
        self.gear_btn.pack(side=tk.RIGHT)
        self.refresh_status()

    def set_entry(self, key, value):
        entry = self.entries[key]
        state = str(entry.cget("state"))
        entry.configure(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, str(value))
        entry.configure(state=state)

    def _on_root_click(self, event):
        try:
            if event.widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Listbox"):
                return
            event.widget.winfo_toplevel().focus_set()
        except (AttributeError, tk.TclError):
            pass

    def update_hotkey_title(self):
        self.labels["hotkey"].configure(text=f"Hotkey ({self.state_selection}):")

    def refresh_status(self):
        if self.engine.running:
            text, color = "Status: Running...", self.status_running
        elif self.engine.recording:
            text, color = "Status: Recording...", self.status_error
        elif self.engine.listening:
            text, color = "Status: Listening...", self.status_listening
        else:
            text, color = "Status: Idle", self.status_idle
        self.status_label.configure(text=text, fg=color)

    def sync_state(self):
        engine = self.engine
        idle = "normal" if not engine.running and not engine.recording else "disabled"
        self.start_btn.config(state=idle)
        self.stop_btn.config(state="normal" if engine.running else "disabled")
        self.listener_btn.config(text="Stop Listening" if engine.listening else "Start Listening")
        self.record_btn.config(
            text="Stop Recording" if engine.recording else "Start Recording",
            state="disabled" if engine.running else "normal",
        )
        self.entries["hotkey"].configure(state="disabled" if engine.listening else "normal")
        self.refresh_status()

    def set_backend_label(self, label):
        self.root.title(f"{APP_NAME} · {label}")

    def show_message(self, title, message, width=46):
        win = self._window(title)
        frame = self._frame(win, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)
        lines = sum(len(line) // width + 1 for line in message.splitlines() or [""])
        text = tk.Text(
            frame, font=("Arial", 9), relief=tk.FLAT, highlightthickness=1,
            width=width, height=min(max(lines, 3), 16), wrap=tk.WORD,
        )
        text.insert("1.0", message)
        text.config(state="disabled")
        self._themed_widget(text, "text")
        text.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        self._button(frame, "OK", win.destroy, font=("Arial", 9, "bold"), width=10).pack()
        self._set_minsize(win)

    def show_error_popup(self, title, message):
        self.show_message(title, message)

    def open_info(self):
        self.show_message(
            "Information",
            "• Delay: hours, minutes, seconds and milliseconds between triggers.\n"
            "• Actions per trigger: how many times the Action is performed each cycle.\n"
            "• Repeat cycles: number of cycles (0 = forever).\n"
            "• Hotkey: starts/stops the job for the selected key state (f6, ctrl+f6, up). "
            "Separate chords with commas for a sequence (ctrl+k, ctrl+c).\n"
            "• Action: key, mouse button, combo or scroll (mouse0=left, mouse1=right, "
            "mouse2=middle, mouse3=back, mouse4=forward; scrollup/scrolldown; "
            "e, space, enter, tab, f1-f12, ctrl+z, ctrl+left …). Separate steps with commas "
            "for a sequence; extra steps: down:KEY, up:KEY, wait:500ms, move:DX:DY "
            "(relative pointer movement), pos:X:Y (exact pointer position).\n"
            "• Start Recording captures keys, mouse buttons and the pointer into the Action field until "
            "you press Stop Recording or the recording hotkey (while listening or recording). "
            "To play a recording once, turn on Act immediately on start and set Repeat cycles to 1. "
            "Recording is unavailable while a job runs; the job hotkey is ignored while recording.\n"
            "• Settings: Hotkey order, Relative movement (exact or device), Screen size (detected on "
            "X11 and most Wayland compositors; needed for exact positions through uinput), Pointer "
            "command (reads the pointer on Wayland, e.g. hyprctl cursorpos) and Recording settings "
            "(pointer path, relative or absolute coordinates, waits, trimming).\n"
            "• Key state (Settings): press holds the key or button down until released, "
            "release lets it go, press & release taps it with the Interval in between. "
            "Wheel and touchpad ignore release.\n"
            "• Scroll device (Settings): wheel sends notches, touchpad sends smooth steps, "
            "pinch holds ctrl while scrolling to zoom.\n"
            "• Scroll value (Settings): scroll amount, 120 = one wheel notch.\n"
            "• Each key state keeps its own hotkey; the Hotkey field edits the selected state.\n"
            "• Linux backends: uinput (kernel, works everywhere), xtest (X11 only), "
            "root helper (asks for authorization once via pkexec).\n"
            "• CLI: run with --help; -n/--nogui works entirely from the terminal.",
            width=60,
        )

    def _section(self, parent, title, row):
        heading = tk.Label(parent, text=title.upper(), anchor="w", font=("Arial", 8, "bold"))
        self._themed_widget(heading, "heading")
        heading.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0 if row == 0 else 18, 4))
        rule = tk.Frame(parent, height=1)
        self._themed_widget(rule, "separator")
        rule.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        return row + 2

    def _setting(self, parent, row, title, widget, hint=""):
        label = tk.Label(parent, text=title, anchor="w", font=("Arial", 9))
        self._themed_widget(label, "label")
        label.grid(row=row, column=0, sticky="w", padx=(0, 20), pady=5)
        widget.grid(row=row, column=1, sticky="ew", pady=5, ipady=2)
        row += 1
        if hint:
            note = tk.Label(parent, text=hint, anchor="w", font=("Arial", 8))
            self._themed_widget(note, "muted")
            note.grid(row=row, column=1, sticky="w", pady=(0, 4))
            row += 1
        return row

    def _toggle(self, parent, row, text, variable, command):
        check = tk.Checkbutton(
            parent, text=text, variable=variable, anchor="w",
            highlightthickness=0, bd=0, font=("Arial", 9), command=command,
        )
        self._themed_widget(check, "check")
        check.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
        return row + 1

    def _action_row(self, parent, row, actions):
        bar = self._frame(parent)
        bar.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        bar.columnconfigure(tuple(range(len(actions))), weight=1, uniform="action")
        for column, (text, command) in enumerate(actions):
            self._button(bar, text, command, font=("Arial", 9, "bold")).grid(
                row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0), ipady=3,
            )
        return row + 1

    def _settings_frame(self, win):
        frame = self._frame(win, padx=26, pady=22)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1, minsize=220)
        return frame

    def open_settings(self):
        win = self._window("Settings")
        frame = self._settings_frame(win)

        row = self._section(frame, "Appearance", 0)
        row = self._setting(
            frame, row, "Theme",
            self._combo(frame, ["Auto"] + list(THEMES), self.theme_selection, self.on_theme_change),
        )
        row = self._toggle(frame, row, "Remember window position", self.remember_pos_var, self.save_config)

        row = self._section(frame, "Input", row)
        row = self._setting(
            frame, row, "Key state", self._combo(frame, STATES, self.state_selection, self.on_state_change),
        )
        row = self._setting(
            frame, row, "Scroll device",
            self._combo(frame, DEVICES, self.device_selection, self.on_device_change),
        )
        row = self._setting(
            frame, row, "Interval", self._settings_entry(frame, self.interval_var), "hold time for press & release",
        )
        row = self._setting(
            frame, row, "Scroll value", self._settings_entry(frame, self.value_var), "120 = one wheel notch",
        )
        if IS_LINUX:
            row = self._setting(
                frame, row, "Input backend",
                self._combo(frame, LinuxInput.MODES, self.backend_selection, self.on_backend_change),
            )
        row = self._toggle(frame, row, "Act immediately on start", self.immediate_var, self.save_config)

        row = self._section(frame, "Advanced", row)
        row = self._option_rows(frame, "input", row)

        row = self._section(frame, "Manage", row)
        self._action_row(frame, row, (
            ("Recording Settings", self.open_recording_settings),
            ("Open Config Dir", self.open_config_dir),
            ("Delete Config", self.delete_config),
        ))
        self._set_minsize(win)

    def _option_rows(self, frame, group, row):
        options = [option for option in OPTIONS if option.group == group]
        for option in (o for o in options if o.kind != "bool"):
            variable = self.option_vars[option.key]
            title, _, hint = option.label.partition(" (")
            if option.kind == "choice":
                widget = self._combo(
                    frame, option.choices, variable.get(),
                    lambda event, var=variable: self.on_option_choice(var, event.widget.get()),
                )
            else:
                widget = self._settings_entry(frame, variable)
            row = self._setting(frame, row, title, widget, hint.rstrip(")"))
        for option in (o for o in options if o.kind == "bool"):
            row = self._toggle(frame, row, option.label, self.option_vars[option.key], self.commit_settings)
        return row

    def open_recording_settings(self):
        win = self._window("Recording Settings")
        frame = self._settings_frame(win)
        self._option_rows(frame, "recording", 0)
        self._set_minsize(win)

    def on_option_choice(self, variable, value):
        variable.set(value)
        self.commit_settings()

    def option_values(self):
        return {key: variable.get() for key, variable in self.option_vars.items()}

    def commit_settings(self):
        try:
            self.engine.configure(self.option_values())
        except ValueError as exc:
            self.show_error_popup("Invalid Setting", str(exc))
            return
        self.save_config()

    def on_theme_change(self, event):
        self.theme_selection = event.widget.get()
        self.update_ui_theme()
        self.save_config()

    def on_backend_change(self, event):
        self.backend_selection = event.widget.get()
        self.engine.set_backend(self.backend_selection)
        self.save_config()

    def on_state_change(self, event):
        self.hotkeys[self.state_selection] = self.entries["hotkey"].get()
        self.state_selection = event.widget.get()
        self.set_entry("hotkey", self.hotkeys[self.state_selection])
        self.update_hotkey_title()
        self.save_config()
        if self.engine.listening:
            hotkey = parse_hotkeys(self.entries["hotkey"].get())
            if hotkey is None:
                self.show_error_popup("Invalid Hotkey", "Unknown key in the Hotkey field.")
            else:
                self.engine.set_hotkey(hotkey)

    def on_device_change(self, event):
        self.device_selection = event.widget.get()
        self.save_config()

    def _fmt_pos(self, x, y):
        return f"{'+' if x >= 0 else ''}{x}{'+' if y >= 0 else ''}{y}"

    def _parse_geom_pos(self, geom):
        m = re.match(r"^(?:\d+x\d+)?([+-]\d+)([+-]\d+)$", geom or "")
        if not m:
            return None
        return int(m.group(1)), int(m.group(2))

    def _kick_pos_tracker(self):
        self._pos_dx = None
        self._pos_dy = None
        self._pos_stable = 0
        self._pos_locked = False
        self._geo_guard = 0
        self._restore_done = False
        self._pos_t0 = time.time()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after(50, self._pos_tick)

    def _on_root_configure(self, event):
        if event.widget is not self.root:
            return
        self._sample_pos_delta()

    def _sample_pos_delta(self):
        if self._pos_locked:
            return
        if self.root.winfo_width() < 2 or self.root.winfo_height() < 2:
            return
        gp = self._parse_geom_pos(self.root.geometry())
        if not gp:
            return
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
        except tk.TclError:
            return
        dx = gp[0] - rx
        dy = gp[1] - ry
        if abs(dx) > 200 or abs(dy) > 200:
            return
        if self._pos_dx is None:
            self._pos_dx = dx
            self._pos_dy = dy
            self._pos_stable = 1
            return
        close = abs(dx - self._pos_dx) <= 3 and abs(dy - self._pos_dy) <= 3
        if close:
            self._pos_dx = dx
            self._pos_dy = dy
            self._pos_stable += 1
            return
        if self._geo_guard or (time.time() - self._pos_t0) < 0.6:
            self._pos_dx = dx
            self._pos_dy = dy
            self._pos_stable = 1
            return
        if self._pos_stable >= 1:
            self._pos_locked = True
            return
        self._pos_dx = dx
        self._pos_dy = dy
        self._pos_stable = 1

    def _pos_tick(self, n=0):
        self._sample_pos_delta()
        if (
            self._restore_pos and not self._restore_done and self._pos_dx is not None
            and not self._pos_locked and (time.time() - self._pos_t0) >= 0.65
            and (self._pos_stable >= 1 or n >= 8)
        ):
            self._correct_restored_position()
        if not self._pos_locked and n < 18:
            self.root.after(70, lambda: self._pos_tick(n + 1))
            return
        if not self._pos_locked and self._pos_dx is not None:
            self._pos_locked = True

    def _current_setter_pos(self):
        gp = self._parse_geom_pos(self.root.geometry())
        if self._pos_dx is None:
            return gp
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
        except tk.TclError:
            return gp
        if gp is not None:
            adx = gp[0] - rx
            ady = gp[1] - ry
            if abs(adx - self._pos_dx) > 180 or abs(ady - self._pos_dy) > 180:
                return gp
        return rx + self._pos_dx, ry + self._pos_dy

    def _set_geometry_pos(self, x, y):
        self._geo_guard += 1
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        if w < 2 or h < 2:
            w, h = self._geom_size
        try:
            self.root.geometry(f"{w}x{h}{self._fmt_pos(x, y)}")
        except tk.TclError:
            pass

        def release():
            self._geo_guard = max(0, self._geo_guard - 1)

        self.root.after(350, release)

    def _correct_restored_position(self):
        if self._restore_done or self._pos_locked or not self._restore_pos or self._pos_dx is None:
            if self._pos_locked:
                self._restore_done = True
            return
        if (time.time() - self._pos_t0) < 0.65:
            return
        req = self._parse_geom_pos(self._restore_pos)
        cur = self._current_setter_pos()
        if not req or not cur:
            return
        dx = cur[0] - req[0]
        dy = cur[1] - req[1]
        if abs(dx) <= 2 and abs(dy) <= 2:
            self._restore_done = True
            return
        if abs(dx) > 80 or abs(dy) > 160:
            return
        self._restore_done = True
        self._pending_fix = (req, (dx, dy))
        self._set_geometry_pos(req[0] - dx, req[1] - dy)
        self.root.after(200, self._verify_restored_position)

    def _verify_restored_position(self):
        req, (odx, ody) = self._pending_fix
        cur = self._current_setter_pos()
        if not cur:
            return
        ndx = cur[0] - req[0]
        ndy = cur[1] - req[1]
        if abs(ndx) + abs(ndy) > abs(odx) + abs(ody):
            self._set_geometry_pos(req[0], req[1])

    def _position_to_save(self):
        if not hasattr(self, "_pos_dx"):
            return self.saved_geometry
        pos = self._current_setter_pos()
        if not pos:
            return ""
        return self._fmt_pos(pos[0], pos[1])

    def _size_to_save(self):
        if self.root.winfo_width() < 2 or self.root.winfo_height() < 2:
            return self.saved_size
        return f"{self.root.winfo_width()}x{self.root.winfo_height()}"

    def _apply_saved_geometry(self):
        self.root.update_idletasks()
        req_w = self.root.winfo_reqwidth()
        req_h = self.root.winfo_reqheight()
        self.root.minsize(req_w, req_h)
        width, height = req_w, req_h
        size = re.match(r"^(\d+)x(\d+)$", self.saved_size or "")
        if size:
            width, height = max(req_w, int(size.group(1))), max(req_h, int(size.group(2)))
        self._geom_size = (width, height)
        position = self.saved_geometry if self.remember_pos_var.get() else ""
        self._restore_pos = position or ""
        try:
            if position:
                self.root.geometry(f"{width}x{height}{position}")
            else:
                self.root.geometry(f"{width}x{height}")
        except tk.TclError:
            self.root.geometry(f"{width}x{height}")
        self._kick_pos_tracker()

    def resolve_and_apply_theme(self):
        resolved = detect_system_theme() if self.theme_selection == "Auto" else self.theme_selection
        t = THEMES.get(resolved, THEMES["Dark Slate"])
        self.bg_color = t["bg"]
        self.fg_color = t["fg"]
        self.entry_bg = t["entry_bg"]
        self.border_color = t["border"]
        self.btn_bg = t["btn"]
        self.btn_active = t["btn_active"]
        self.arrow_color = t["arrow"]
        self.arrow_active_color = t["arrow_active"]
        self.arrow_accent = t["arrow_accent"]
        self.status_idle = t["idle"]
        self.status_listening = t["listening"]
        self.status_running = t["running"]
        self.status_error = t["error"]
        self.root.configure(bg=self.bg_color)
        self.setup_ttk_style()

    def setup_ttk_style(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self.style.configure(
            "TCombobox", fieldbackground=self.entry_bg, background=self.btn_bg,
            foreground=self.fg_color, darkcolor=self.entry_bg, lightcolor=self.entry_bg,
            bordercolor=self.border_color, arrowcolor=self.arrow_color, borderwidth=0, relief="flat",
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", "focus", self.entry_bg), ("readonly", self.entry_bg), ("focus", self.entry_bg), ("", self.entry_bg)],
            foreground=[("readonly", "focus", self.fg_color), ("readonly", self.fg_color), ("focus", self.fg_color), ("", self.fg_color)],
            selectbackground=[("readonly", self.btn_bg), ("focus", self.btn_bg), ("", self.btn_bg)],
            selectforeground=[("readonly", self.fg_color), ("focus", self.fg_color), ("", self.fg_color)],
            background=[("pressed", self.arrow_accent), ("active", self.arrow_accent), ("readonly", self.btn_bg), ("", self.btn_bg)],
            arrowcolor=[("pressed", self.arrow_active_color), ("active", self.arrow_active_color), ("disabled", self.arrow_color), ("readonly", self.arrow_color), ("", self.arrow_color)],
            bordercolor=[("focus", self.border_color), ("active", self.border_color), ("pressed", self.border_color), ("", self.border_color)],
            darkcolor=[("pressed", self.arrow_accent), ("active", self.arrow_accent), ("", self.entry_bg)],
            lightcolor=[("pressed", self.arrow_accent), ("active", self.arrow_accent), ("", self.entry_bg)],
        )
        self.style.configure(
            "ComboboxPopdownFrame", background=self.entry_bg, bordercolor=self.border_color,
            lightcolor=self.border_color, darkcolor=self.border_color, relief="solid", borderwidth=1,
        )
        option = self.root.option_add
        option("*TCombobox*Listbox.background", self.entry_bg)
        option("*TCombobox*Listbox.foreground", self.fg_color)
        option("*TCombobox*Listbox.selectBackground", self.btn_active)
        option("*TCombobox*Listbox.selectForeground", self.fg_color)
        option("*TCombobox*Listbox.highlightBackground", self.border_color)
        option("*TCombobox*Listbox.highlightColor", self.border_color)
        option("*TCombobox*Listbox.highlightThickness", "0")
        option("*TCombobox*Listbox.borderWidth", "0")
        option("*TCombobox*Listbox.relief", "flat")
        option("*ComboboxPopdown.background", self.border_color)
        option("*ComboboxPopdown.highlightBackground", self.border_color)
        option("*ComboboxPopdown.highlightColor", self.border_color)
        option("*ComboboxPopdown.highlightThickness", "0")
        option("*ComboboxPopdown.borderWidth", "0")
        self._combos = [c for c in self._combos if self._alive(c)]
        for combo in self._combos:
            self._style_combobox_popdown(combo)

    @staticmethod
    def _alive(widget):
        try:
            return bool(widget.winfo_exists())
        except tk.TclError:
            return False

    def _style_combobox_popdown(self, combo):
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", str(combo))
        except tk.TclError:
            return
        try:
            combo.tk.call(
                popdown, "configure", "-background", self.border_color,
                "-highlightthickness", 0, "-borderwidth", 0, "-relief", "flat",
                "-highlightbackground", self.border_color, "-highlightcolor", self.border_color,
            )
        except tk.TclError:
            pass
        try:
            combo.tk.call(
                f"{popdown}.f.l", "configure", "-background", self.entry_bg, "-foreground", self.fg_color,
                "-selectbackground", self.btn_active, "-selectforeground", self.fg_color,
                "-highlightthickness", 0, "-borderwidth", 0, "-relief", "flat",
                "-highlightbackground", self.border_color, "-highlightcolor", self.border_color,
                "-activestyle", "none",
            )
        except tk.TclError:
            pass

    def update_ui_theme(self):
        self.resolve_and_apply_theme()
        alive = []
        for widget, kind in self._themed:
            try:
                self._apply_widget_theme(widget, kind)
                alive.append((widget, kind))
            except tk.TclError:
                pass
        self._themed = alive
        self.refresh_status()

    def save_config(self):
        self.hotkeys[self.state_selection] = self.entries["hotkey"].get()
        data = {
            "theme": self.theme_selection,
            "backend": self.backend_selection,
            "remember_pos": self.remember_pos_var.get(),
            "geometry_pos": self._position_to_save() if self.remember_pos_var.get() else "",
            "size": self._size_to_save(),
            "state": self.state_selection,
            "device": self.device_selection,
            "interval": self.interval_var.get(),
            "value": self.value_var.get(),
            "immediate": self.immediate_var.get(),
            "hotkeys": dict(self.hotkeys),
        }
        data.update(self.option_values())
        data.update({key: entry.get() for key, entry in self.entries.items() if key != "hotkey"})
        write_config(self.configs, data)

    def open_config_dir(self):
        try:
            os.makedirs(self.cfgdir, exist_ok=True)
            if IS_WIN:
                os.startfile(self.cfgdir)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.cfgdir])
            else:
                subprocess.run(["xdg-open", self.cfgdir])
        except Exception as exc:
            self.show_error_popup("Error", f"Failed to open dir ({exc})")

    def delete_config(self):
        try:
            if os.path.exists(self.configs):
                os.remove(self.configs)
        except OSError:
            pass
        for key, value in DEFAULTS.items():
            self.set_entry(key, value)
        self.state_selection = OPTION_DEFAULTS["state"]
        self.device_selection = OPTION_DEFAULTS["device"]
        self.interval_var.set(OPTION_DEFAULTS["interval"])
        self.value_var.set(OPTION_DEFAULTS["value"])
        self.immediate_var.set(False)
        for option in OPTIONS:
            self.option_vars[option.key].set(option.clean(None))
        self.engine.configure(self.option_values())
        self.hotkeys = dict(DEFAULT_HOTKEYS)
        self.set_entry("hotkey", self.hotkeys[self.state_selection])
        self.update_hotkey_title()
        self.theme_selection = "Auto"
        self.backend_selection = "Auto"
        self.remember_pos_var.set(True)
        self.saved_geometry = ""
        self.engine.set_backend("Auto")
        self.update_ui_theme()

    def on_closing(self):
        self.save_config()
        self.engine.shutdown()
        self.root.destroy()

    def current_fields(self):
        fields = {key: entry.get() for key, entry in self.entries.items()}
        fields.update({
            "state": self.state_selection,
            "device": self.device_selection,
            "interval": self.interval_var.get(),
            "value": self.value_var.get(),
            "immediate": self.immediate_var.get(),
        })
        return fields

    def build_job(self):
        try:
            return job_from_fields(self.current_fields(), need_hotkey=False)
        except ValueError as exc:
            self.show_error_popup("Invalid Input", str(exc))
            return None

    def start_clicking(self):
        if self.engine.running or self.engine.recording:
            return
        job = self.build_job()
        if job is None:
            return
        self.save_config()
        self.engine.start(job)

    def toggle_recording(self):
        if self.engine.recording:
            self.engine.stop_recording(by_click=True)
        else:
            self.root.focus_set()
            self.engine.start_recording()

    def apply_recording(self, text):
        if not text:
            self.show_message("Recording", "No keys or mouse buttons were recorded.")
            return
        self.set_entry("action", text)
        self.save_config()

    def toggle_clicking(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.start_clicking()

    def toggle_listener(self):
        if self.engine.listening:
            self.engine.stop_listener()
            return
        hotkey = parse_hotkeys(self.entries["hotkey"].get())
        if hotkey is None:
            self.show_error_popup("Invalid Hotkey", "Unknown key in the Hotkey field.")
            return
        self.save_config()
        self.root.focus_set()
        self.engine.start_listener(hotkey)


def log(message, err=False):
    stream = sys.stderr if err else sys.stdout
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=stream, flush=True)


def run_headless(fields, backend, listen, start, quiet):
    try:
        job = job_from_fields(fields, need_hotkey=listen)
        resolve_options(fields)
    except ValueError as exc:
        print(f"{APP_NAME.lower()}: error: {exc}", file=sys.stderr)
        return 2

    done = threading.Event()
    result = {"code": 0}
    seen = {"running": False, "listening": False}

    def say(message):
        if not quiet:
            log(message)

    def notify(event, *args):
        if event == "backend":
            say(f"backend: {args[0]}")
        elif event == "error":
            result["code"] = 1
            log(f"{args[0]}: {args[1]}", err=True)
        elif event == "finished":
            say("finished")
        elif event == "state":
            if engine.running != seen["running"]:
                seen["running"] = engine.running
                say("running" if engine.running else "stopped")
            if engine.listening != seen["listening"]:
                seen["listening"] = engine.listening
                say(f"listening for '{fields['hotkey']}'" if engine.listening else "listener stopped")
            if not engine.running and not engine.listening:
                done.set()

    engine = Engine(notify, backend)
    engine.configure(fields)
    engine.job = job

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: done.set())
        except (ValueError, OSError):
            pass

    say(f"action '{fields['action']}' {job.describe()}")
    if listen:
        say(f"press '{fields['hotkey']}' to start/stop, Ctrl+C to quit")
        engine.start_listener(job.hotkey)
    if start:
        engine.start(job)

    while not done.wait(0.25):
        pass
    engine.shutdown()
    return result["code"]


def run_record(fields, backend, duration, quiet):
    done = threading.Event()
    outcome = {"code": 0, "text": ""}

    def notify(event, *args):
        if event == "recorded":
            outcome["text"] = args[0]
            done.set()
        elif event == "backend" and not quiet:
            log(f"backend: {args[0]}", err=True)
        elif event == "error":
            outcome["code"] = 1
            log(f"{args[0]}: {args[1]}", err=True)
            done.set()

    engine = Engine(notify, backend)
    try:
        engine.configure(fields)
    except ValueError as exc:
        print(f"{APP_NAME.lower()}: error: {exc}", file=sys.stderr)
        return 2, ""

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: done.set())
        except (ValueError, OSError):
            pass

    if engine.start_recording():
        hotkey = (fields.get("record_hotkey") or "").strip()
        if not quiet:
            stop = f"press '{hotkey}' or Ctrl+C" if hotkey else "press Ctrl+C"
            log(f"recording; {stop} to stop", err=True)
        done.wait(duration or None)
        outcome["text"] = engine.stop_recording() or outcome["text"]
    engine.shutdown()
    return outcome["code"], outcome["text"]


def run_check(include_helper):
    if not IS_LINUX:
        print("native backend: ok" if IS_WIN else "unsupported platform")
        return 0 if IS_WIN else 1
    probes = [
        ("injector", "uinput", UInputDevice),
        ("injector", "xtest", XTestInput),
        ("listener", "evdev", EvdevSource),
        ("listener", "x11-poll", XKeyPoller),
    ]
    if include_helper:
        probes.append(("both", "root helper", PrivilegedHelper))
    roles = set()
    for role, name, factory in probes:
        try:
            factory().close()
            print(f"{name:12} ok          ({role})")
            roles.update(("injector", "listener") if role == "both" else (role,))
        except Exception as exc:
            print(f"{name:12} unavailable ({exc})")
    return 0 if roles >= {"injector", "listener"} else 1


def list_keys():
    print("keys:    " + " ".join(sorted(KEYS)))
    print("mouse:   " + " ".join(sorted(MOUSE_MAP)))
    print("scroll:  " + " ".join(sorted(SCROLL_ACTIONS)))
    print("states:  " + " | ".join(STATES))
    print("devices: " + " ".join(DEVICES))
    print("combine: ctrl+shift+f6, ctrl+z, alt+tab, ...")
    print("steps:   separate with commas; tap by default, or down:KEY, up:KEY, wait:500ms,")
    print("         move:DX:DY (relative pointer), pos:X:Y (exact pointer position)")
    print("hotkeys: separate chords with commas for a sequence: ctrl+k, ctrl+c")
    print("record:  -R records into an action (printed; --save keeps it); -s -r 1 -I plays it once")


def build_parser():
    def duration(text):
        try:
            return parse_duration(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc))

    def number(minimum):
        def convert(text):
            try:
                value = int(text)
            except ValueError:
                raise argparse.ArgumentTypeError(f"'{text}' is not an integer")
            if value < minimum:
                raise argparse.ArgumentTypeError(f"must be >= {minimum}")
            return value
        return convert

    def action(text):
        if parse_sequence(text) is None:
            raise argparse.ArgumentTypeError(f"invalid action '{text}' (see --list-keys)")
        return text

    def hotkey(text):
        if parse_hotkeys(text) is None:
            raise argparse.ArgumentTypeError(f"unknown key in '{text}' (see --list-keys)")
        return text

    def option_text(option):
        def convert(text):
            try:
                option.value(text)
            except ValueError as exc:
                raise argparse.ArgumentTypeError(str(exc))
            return text
        return convert

    def choice(choices):
        def convert(text):
            found = match_choice(text, choices)
            if found is None:
                raise argparse.ArgumentTypeError(f"choose from: {', '.join(choices)}")
            return found
        return convert

    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]) or "intool.pyw",
        description="Intool (Input Tool) – hotkey-triggered input pulser, usable with or without GUI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  intool.pyw -n -k f6 -a mouse0 -d 100ms           terminal only, F6 toggles left clicking\n"
            "  intool.pyw -n -s -a space -d 1s -r 10            press space 10 times, then exit\n"
            "  intool.pyw -n -l -s -a ctrl+z -d 1m30s           start now and let F6 toggle it\n"
            "  intool.pyw -n -S press -a shift -s               hold shift until stopped\n"
            "  intool.pyw -n -S \"press & release\" -i 30ms -a space -s -r 5\n"
            "  intool.pyw -n -a scrollup -D pinch -v 240 -d 200ms -s -r 20   zoom in with ctrl+wheel\n"
            "  intool.pyw -n -a \"pos:400:300, mouse0, wait:1s, move:50:0, mouse0\" -s -r 1\n"
            "  intool.pyw -n -k \"ctrl+k, ctrl+c\" -a mouse0 -I   hotkey sequence, first click at once\n"
            "  intool.pyw -R --save                             record until F10, keep it as the action\n"
            "  intool.pyw -n -s -r 1 -I                         play the saved action once\n"
            "  intool.pyw -a e -d 250ms -l                      open the GUI prefilled, listener on\n"
            "  intool.pyw --check                               test which Linux backends work here\n"
        ),
    )
    mode = parser.add_argument_group("mode")
    mode.add_argument("-n", "--nogui", "--headless", action="store_true",
                      help="run entirely in the terminal (no window)")
    mode.add_argument("-l", "--listen", action="store_true",
                      help="start the hotkey listener (default in --nogui unless --start is given)")
    mode.add_argument("-s", "--start", "--now", action="store_true",
                      help="start the action immediately; with --nogui exits when the cycles are done")
    mode.add_argument("-R", "--record", nargs="?", const=0.0, default=None, type=duration, metavar="TIME",
                      help="record keys, buttons and pointer into an action, print it, then exit; "
                           "stops at the recording hotkey, Ctrl+C or after TIME (use with --save to keep it)")
    mode.add_argument("-q", "--quiet", action="store_true", help="only print errors in --nogui")

    job = parser.add_argument_group("job (override the saved config)")
    job.add_argument("-d", "--delay", type=duration, metavar="TIME",
                     help="delay between triggers: 500ms, 2s, 1m30s, 1h, or plain seconds (0.5)")
    job.add_argument("-a", "--action", type=action, metavar="ACTION",
                     help="key, mouse button, scroll or combo: mouse0, space, ctrl+z, scrollup; "
                          "comma-separated steps form a sequence (see --list-keys)")
    job.add_argument("-S", "--state", type=choice(STATES), metavar="STATE",
                     help="key state: " + ", ".join(STATES))
    job.add_argument("-i", "--interval", type=duration, metavar="TIME",
                     help="hold time between press and release (press & release only, default 100ms)")
    job.add_argument("-D", "--device", type=choice(DEVICES), metavar="DEVICE",
                     help="scroll device: " + ", ".join(DEVICES))
    job.add_argument("-v", "--value", type=number(1), metavar="N",
                     help="scroll amount, 120 = one wheel notch")
    job.add_argument("-k", "--hotkey", type=hotkey, metavar="HOTKEY",
                     help="hotkey of the selected key state: f6, ctrl+f6, up; "
                          "comma-separated chords form a sequence")
    job.add_argument("--hotkey-press", type=hotkey, metavar="HOTKEY", help="hotkey of the press state")
    job.add_argument("--hotkey-release", type=hotkey, metavar="HOTKEY", help="hotkey of the release state")
    job.add_argument("--hotkey-press-release", type=hotkey, metavar="HOTKEY",
                     help="hotkey of the press & release state")
    job.add_argument("-I", "--immediate", action="store_true",
                     help="perform the action right when the job starts instead of after the first delay")
    job.add_argument("-c", "--clicks", type=number(1), metavar="N", help="actions per trigger")
    job.add_argument("-r", "--repeat", type=number(0), metavar="N", help="number of cycles (0 = forever)")

    options = parser.add_argument_group("options (saved in the config file)")
    for option in OPTIONS:
        flag = "--" + option.key.replace("_", "-")
        if option.kind == "bool":
            options.add_argument(flag, action=argparse.BooleanOptionalAction, default=None, help=option.label)
        elif option.kind == "choice":
            options.add_argument(flag, dest=option.key, type=choice(option.choices), metavar="NAME",
                                 help=f"{option.label}: " + ", ".join(option.choices))
        else:
            options.add_argument(flag, dest=option.key, type=option_text(option), metavar="VALUE",
                                 help=option.label)

    setup = parser.add_argument_group("setup")
    setup.add_argument("-b", "--backend", type=choice(LinuxInput.MODES), metavar="NAME",
                       help="Linux input backend: " + ", ".join(LinuxInput.MODES))
    setup.add_argument("-t", "--theme", type=choice(["Auto"] + list(THEMES)), metavar="NAME",
                       help="GUI theme: Auto, " + ", ".join(THEMES))
    setup.add_argument("--config", metavar="FILE", help="use this config file instead of the default")
    setup.add_argument("--save", action="store_true", help="write the resolved options to the config file")
    setup.add_argument("--reset-config", action="store_true", help="delete the config file before starting")

    tools = parser.add_argument_group("tools")
    tools.add_argument("--check", action="store_true",
                       help="test which input backends work on this machine, then exit "
                            "(add -b 'root helper' to include the root helper)")
    tools.add_argument("--list-keys", action="store_true", help="print all supported key names, then exit")
    tools.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--helper", action="store_true", help=argparse.SUPPRESS)
    return parser


def collect_options(args, data):
    fields = {key: str(data.get(key, default)) for key, default in DEFAULTS.items()}
    fields.update({key: str(data.get(key, default)) for key, default in OPTION_DEFAULTS.items()})
    fields["state"] = pick(fields["state"], STATES, OPTION_DEFAULTS["state"])
    fields["device"] = pick(fields["device"], DEVICES, OPTION_DEFAULTS["device"])
    if args.delay is not None:
        fields.update(zip(("h", "m", "s", "ms"), map(str, split_duration(args.delay))))
    for key in ("clicks", "repeat", "action", "state", "device", "value"):
        value = getattr(args, key)
        if value is not None:
            fields[key] = str(value)
    if args.interval is not None:
        fields["interval"] = f"{args.interval * 1000:g}ms"
    fields["immediate"] = args.immediate or to_bool(data.get("immediate", False))
    given = {option.key: getattr(args, option.key) for option in OPTIONS}
    fields.update(valid_options({**data, **{key: value for key, value in given.items() if value is not None}}))

    hotkeys = resolve_hotkeys(data, fields["state"])
    overrides = {
        "press": args.hotkey_press,
        "release": args.hotkey_release,
        "press & release": args.hotkey_press_release,
    }
    hotkeys.update({state: text for state, text in overrides.items() if text is not None})
    if args.hotkey is not None:
        hotkeys[fields["state"]] = args.hotkey
    fields["hotkey"] = hotkeys[fields["state"]]

    backend = args.backend or pick(data.get("backend", "Auto"), LinuxInput.MODES, "Auto")
    theme = args.theme or pick(data.get("theme", "Auto"), ["Auto"] + list(THEMES), "Auto")
    return fields, hotkeys, backend, theme


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.helper:
        return helper_main()
    if args.list_keys:
        list_keys()
        return 0
    if args.check:
        return run_check(args.backend == "root helper")

    config_path = os.path.abspath(args.config) if args.config else default_config_path()
    if args.reset_config:
        try:
            os.remove(config_path)
        except OSError:
            pass
    data = read_config(config_path)
    fields, hotkeys, backend, theme = collect_options(args, data)
    stored = {key: value for key, value in fields.items() if key != "hotkey"}

    if args.save:
        saved = {**data, **stored, "backend": backend, "theme": theme, "hotkeys": hotkeys}
        saved.pop("hotkey", None)
        if not write_config(config_path, saved):
            print(f"{parser.prog}: warning: cannot write {config_path}", file=sys.stderr)

    if args.record is not None:
        args.nogui = True

    if not args.nogui:
        try:
            if tk is None:
                raise RuntimeError("tkinter is not installed")
            root = tk.Tk()
        except Exception as exc:
            print(f"{parser.prog}: GUI unavailable ({exc}); running in terminal mode", file=sys.stderr)
            args.nogui = True
        else:
            overrides = {**stored, "backend": backend, "theme": theme, "hotkeys": hotkeys}
            App(root, config_path, overrides, auto_listen=args.listen, auto_start=args.start)
            root.mainloop()
            return 0

    if args.record is not None:
        code, text = run_record(fields, backend, args.record, args.quiet)
        if text:
            print(text)
            if args.save and not write_config(config_path, {**read_config(config_path), "action": text}):
                print(f"{parser.prog}: warning: cannot write {config_path}", file=sys.stderr)
        return code

    listen = args.listen or not args.start
    return run_headless(fields, backend, listen, args.start, args.quiet)


if __name__ == "__main__":
    sys.exit(main())

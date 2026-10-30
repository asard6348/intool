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
SCROLL_NOTCH = 120

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES = 0, 1, 6, 8, 11
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114

EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_DEV_SETUP = 0x405C5503
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
SCROLL_ACTIONS = {"scrollup": 1, "scrolldown": -1}

STATES = ("press", "release", "press & release")
DEVICES = ("wheel", "touchpad", "pinch")
OPTION_DEFAULTS = {"state": "press & release", "device": "wheel", "interval": "100ms", "value": "120"}
DEFAULT_HOTKEYS = {"press": "f7", "release": "f9", "press & release": "f8"}


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
    for alias, name in {
        "escape": "esc", "return": "enter", "del": "delete", "pgup": "pageup",
        "pgdn": "pagedown", "arrowup": "up", "arrowdown": "down",
        "leftarrow": "left", "rightarrow": "right", "super": "win", "meta": "win",
    }.items():
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
    action: tuple
    hotkey: tuple = None
    state: str = "press & release"
    interval: float = 0.1
    value: int = 120
    device: str = "wheel"

    def spec(self):
        return {
            "kind": self.action[0], "payload": self.action[1], "state": self.state,
            "interval": self.interval, "value": self.value, "device": self.device,
        }

    def describe(self):
        cycles = self.repeats if self.repeats else "unlimited"
        parts = [f"every {self.delay:g}s", f"{self.per_trigger}x per trigger", f"state: {self.state}"]
        if self.state == "press & release":
            parts.append(f"interval {self.interval * 1000:g}ms")
        if self.action[0] == "scroll":
            parts.append(f"{self.device} {self.value}")
        parts.append(f"cycles: {cycles}")
        return ", ".join(parts)


def plan(spec):
    kind, payload = spec["kind"], spec["payload"]
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


def parse_action(text):
    s = (text or "").strip().lower()
    if s in SCROLL_ACTIONS:
        return ("scroll", SCROLL_ACTIONS[s])
    parts = [p.strip() for p in s.split("+") if p.strip()]
    if not parts:
        return None
    codes = []
    for part in parts:
        if part in MOUSE_MAP:
            codes.append(MOUSE_MAP[part])
        elif part in KEYS:
            codes.append(KEYS[part])
        else:
            return None
    return ("press", codes)


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
    action = parse_action(fields["action"])
    if action is None:
        raise ValueError(f"Unknown key or button in the action '{fields['action']}'.")
    hotkey = parse_hotkey(fields["hotkey"])
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
    return Job(delay, max(0, repeats), max(1, per_trigger), action, hotkey, state, interval, value, device)


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
    def __init__(self):
        self.held = []

    def pulse(self, spec):
        for step in plan(spec):
            if step[0] == "key":
                self.key(step[1], step[2])
                self._track(step[1], step[2])
            elif step[0] == "wheel":
                self.wheel(step[1], step[2])
            else:
                self.flush()
                time.sleep(step[1])
        self.flush()

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

    def close(self):
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


class X11Connection:
    def __init__(self):
        if not x11_session():
            raise BackendError("not an X11 session")
        name = ctypes.util.find_library("X11")
        if not name:
            raise BackendError("libX11 not found")
        self.lib = ctypes.CDLL(name)
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.dpy = self.lib.XOpenDisplay(None)
        if not self.dpy:
            raise BackendError("cannot open X display")

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

    def flush(self):
        self.lib.XSync(self.dpy, 0)


class XKeyPoller(X11Connection):
    label = "x11-poll"

    def __init__(self):
        super().__init__()
        self._buf = (ctypes.c_ubyte * 32)()
        self._prev = set()

    def poll(self, timeout):
        time.sleep(min(timeout, 0.015))
        self.lib.XQueryKeymap(self.dpy, self._buf)
        now = set()
        for index, byte in enumerate(self._buf):
            for bit in range(8):
                keycode = index * 8 + bit
                if keycode >= 8 and byte >> bit & 1:
                    now.add(keycode - 8)
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


class EvdevSource:
    label = "evdev"

    def __init__(self):
        self._fds = {}
        self._next_scan = 0.0
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
        events = []
        for fd in ready:
            try:
                data = os.read(fd, EVENT_SIZE * 64)
            except BlockingIOError:
                continue
            except OSError:
                self._drop(fd)
                continue
            for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, offset)
                if etype == EV_KEY and value in (0, 1):
                    events.append((code, value))
        return events

    def close(self):
        for fd in list(self._fds):
            self._drop(fd)


class HelperServer:
    def __init__(self, device):
        self.device = device
        self.source = None
        self.thread = None
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
        self._lock = threading.RLock()
        self._injector = None
        self._helper = None

    def set_mode(self, mode):
        with self._lock:
            self.mode = mode if mode in self.ORDER else "Auto"
            self.reset_injector()

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


class Engine:
    def __init__(self, notify, backend="Auto", on_hotkey=None):
        self.notify = notify
        self.on_hotkey = on_hotkey
        self.linux = LinuxInput(backend) if IS_LINUX else None
        self.windows = WindowsInput() if IS_WIN else None
        self.job = None
        self.running = False
        self.listening = False
        self._hotkey = None
        self._stop = threading.Event()
        self._listen_gen = 0
        self._lock = threading.RLock()

    def _emit(self, event, *args):
        try:
            self.notify(event, *args)
        except Exception:
            pass

    def set_backend(self, mode):
        if self.linux:
            self.linux.set_mode(mode)

    def _pulse(self, spec):
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
            if self.running:
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

    def _run(self, stop, job):
        completed = False
        spec = job.spec()
        try:
            if self.linux is not None:
                self._emit("backend", self.linux.injector().label)
            deadline = time.monotonic() + job.delay
            count = 0
            while not stop.is_set() and (job.repeats == 0 or count < job.repeats):
                if stop.wait(max(0.0, deadline - time.monotonic())):
                    break
                for _ in range(job.per_trigger):
                    self._pulse(spec)
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
            self._listen_gen += 1
            gen = self._listen_gen
        threading.Thread(target=self._listen, args=(gen,), daemon=True).start()
        self._emit("state")
        return True

    def set_hotkey(self, hotkey):
        self._hotkey = hotkey

    def stop_listener(self):
        with self._lock:
            was_listening = self.listening
            self.listening = False
            self._listen_gen += 1
        self.stop()
        if was_listening:
            self._emit("state")

    def _listen_active(self, gen):
        return self.listening and self._listen_gen == gen

    def _fire(self):
        (self.on_hotkey or self.toggle)()

    def _listen_linux(self, gen):
        source = self.linux.open_source()
        self._emit("backend", source.label)
        pressed = set()
        try:
            while self._listen_active(gen):
                mod_sets, target = self._hotkey
                for code, value in source.poll(0.25):
                    if value:
                        pressed.add(code)
                    else:
                        pressed.discard(code)
                    if value and code == target and all(pressed & mods for mods in mod_sets):
                        self._fire()
        finally:
            source.close()

    def _listen_windows(self, gen):
        state = ctypes.windll.user32.GetAsyncKeyState
        was_down = False
        while self._listen_active(gen):
            time.sleep(0.02)
            mod_sets, target = self._hotkey
            down = bool(state(target) & 0x8000)
            mods_ok = all(any(state(m) & 0x8000 for m in mods) for mods in mod_sets)
            if down and mods_ok:
                if not was_down:
                    was_down = True
                    self._fire()
            else:
                was_down = False

    def _listen(self, gen):
        try:
            if IS_LINUX:
                self._listen_linux(gen)
            elif IS_WIN:
                self._listen_windows(gen)
            else:
                raise BackendError("Listener not supported on this platform.")
        except Exception as exc:
            if self._listen_active(gen):
                self._emit("error", "Listener Error", str(exc))
        finally:
            with self._lock:
                owner = self._listen_active(gen)
                if owner:
                    self.listening = False
            if owner:
                self._emit("state")

    def shutdown(self):
        with self._lock:
            self.listening = False
            self._listen_gen += 1
        self.stop()
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
        self.hotkeys = resolve_hotkeys(data, self.state_selection)

        self.engine = Engine(
            self._engine_event, self.backend_selection,
            on_hotkey=lambda: self._engine_event("hotkey"),
        )

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
            parent, textvariable=variable, width=14, justify=tk.CENTER, font=("Arial", 10),
            relief=tk.FLAT, highlightthickness=1,
        )
        entry.bind("<FocusOut>", lambda _: self.save_config())
        entry.bind("<Return>", lambda _: self.save_config())
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
        combo = ttk.Combobox(parent, values=list(values), state="readonly", width=18)
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
            main, "Start Listener", self.toggle_listener, font=("Arial", 9, "bold"), width=16,
        )
        self.listener_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        btn_frame = self._frame(main)
        btn_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        btn_frame.columnconfigure((0, 1), weight=1, uniform="run")
        self.start_btn = self._button(btn_frame, "Start", self.start_clicking, width=10)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = self._button(btn_frame, "Stop", self.engine.stop, width=10, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew")

        status_row = self._frame(main)
        status_row.grid(row=5, column=0, columnspan=2, sticky="ew")
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
        elif self.engine.listening:
            text, color = "Status: Listening...", self.status_listening
        else:
            text, color = "Status: Idle", self.status_idle
        self.status_label.configure(text=text, fg=color)

    def sync_state(self):
        running = self.engine.running
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        self.listener_btn.config(text="Stop Listener" if self.engine.listening else "Start Listener")
        self.entries["hotkey"].configure(state="disabled" if self.engine.listening else "normal")
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
            "• Hotkey: starts/stops the job for the selected key state (f6, ctrl+f6, up).\n"
            "• Action: key, mouse button, combo or scroll (mouse0=left, mouse1=right, "
            "mouse2=middle, mouse3=back, mouse4=forward; scrollup/scrolldown; "
            "e, space, enter, tab, f1-f12, ctrl+z, ctrl+left …).\n"
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

    def open_settings(self):
        win = self._window("Settings")
        frame = self._frame(win, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        row = 0

        def add_row(title, widget):
            nonlocal row
            self._label(frame, title, bold=True).grid(row=row, column=0, sticky="w", pady=(0, 4))
            widget.grid(row=row + 1, column=0, sticky="ew", pady=(0, 12))
            row += 2

        add_row("Theme:", self._combo(frame, ["Auto"] + list(THEMES), self.theme_selection, self.on_theme_change))
        add_row("Key state:", self._combo(frame, STATES, self.state_selection, self.on_state_change))
        add_row("Scroll device:", self._combo(frame, DEVICES, self.device_selection, self.on_device_change))
        add_row("Interval (press & release):", self._settings_entry(frame, self.interval_var))
        add_row("Scroll value (120 = one notch):", self._settings_entry(frame, self.value_var))
        if IS_LINUX:
            add_row("Input backend:", self._combo(frame, LinuxInput.MODES, self.backend_selection, self.on_backend_change))

        check = tk.Checkbutton(
            frame, text="Remember window position", variable=self.remember_pos_var,
            highlightthickness=0, bd=0, font=("Arial", 9), command=self.save_config,
        )
        self._themed_widget(check, "check")
        check.grid(row=row, column=0, sticky="w", pady=(0, 14))
        row += 1

        self._button(
            frame, "Open Config Dir", self.open_config_dir, font=("Arial", 9, "bold"), width=18,
        ).grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1
        self._button(
            frame, "Delete Config", self.delete_config, font=("Arial", 9, "bold"), width=18,
        ).grid(row=row, column=0, sticky="ew")
        self._set_minsize(win)

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
            hotkey = parse_hotkey(self.entries["hotkey"].get())
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
            "hotkeys": dict(self.hotkeys),
        }
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
        })
        return fields

    def start_clicking(self):
        if self.engine.running:
            return
        try:
            job = job_from_fields(self.current_fields(), need_hotkey=False)
        except ValueError as exc:
            self.show_error_popup("Invalid Input", str(exc))
            return
        self.save_config()
        self.engine.start(job)

    def toggle_clicking(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.start_clicking()

    def toggle_listener(self):
        if self.engine.listening:
            self.engine.stop_listener()
            return
        hotkey = parse_hotkey(self.entries["hotkey"].get())
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
        if parse_action(text) is None:
            raise argparse.ArgumentTypeError(f"unknown key or button in '{text}' (see --list-keys)")
        return text

    def hotkey(text):
        if parse_hotkey(text) is None:
            raise argparse.ArgumentTypeError(f"unknown key in '{text}' (see --list-keys)")
        return text

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
    mode.add_argument("-q", "--quiet", action="store_true", help="only print errors in --nogui")

    job = parser.add_argument_group("job (override the saved config)")
    job.add_argument("-d", "--delay", type=duration, metavar="TIME",
                     help="delay between triggers: 500ms, 2s, 1m30s, 1h, or plain seconds (0.5)")
    job.add_argument("-a", "--action", type=action, metavar="ACTION",
                     help="key, mouse button, scroll or combo: mouse0, space, ctrl+z, scrollup")
    job.add_argument("-S", "--state", type=choice(STATES), metavar="STATE",
                     help="key state: " + ", ".join(STATES))
    job.add_argument("-i", "--interval", type=duration, metavar="TIME",
                     help="hold time between press and release (press & release only, default 100ms)")
    job.add_argument("-D", "--device", type=choice(DEVICES), metavar="DEVICE",
                     help="scroll device: " + ", ".join(DEVICES))
    job.add_argument("-v", "--value", type=number(1), metavar="N",
                     help="scroll amount, 120 = one wheel notch")
    job.add_argument("-k", "--hotkey", type=hotkey, metavar="HOTKEY",
                     help="hotkey of the selected key state: f6, ctrl+f6, up")
    job.add_argument("--hotkey-press", type=hotkey, metavar="HOTKEY", help="hotkey of the press state")
    job.add_argument("--hotkey-release", type=hotkey, metavar="HOTKEY", help="hotkey of the release state")
    job.add_argument("--hotkey-press-release", type=hotkey, metavar="HOTKEY",
                     help="hotkey of the press & release state")
    job.add_argument("-c", "--clicks", type=number(1), metavar="N", help="actions per trigger")
    job.add_argument("-r", "--repeat", type=number(0), metavar="N", help="number of cycles (0 = forever)")

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

    listen = args.listen or not args.start
    return run_headless(fields, backend, listen, args.start, args.quiet)


if __name__ == "__main__":
    sys.exit(main())

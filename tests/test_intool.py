import importlib.machinery
import importlib.util
import io
import json
import os
import struct
import tempfile
import threading
import time
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "intool.pyw"


def load_module():
    loader = importlib.machinery.SourceFileLoader("intool_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


intool = load_module()


class RecordingInjector(intool.Injector):
    def __init__(self):
        super().__init__()
        self.steps = []

    def key(self, code, down):
        self.steps.append(("key", code, down))

    def wheel(self, hires, notch):
        self.steps.append(("wheel", hires, notch))

    def move(self, dx, dy):
        self.steps.append(("move", dx, dy))

    def warp(self, x, y):
        self.steps.append(("warp", x, y))

    def flush(self):
        self.steps.append(("flush",))


def spec(kind="press", payload=None, state="press & release", interval=0.012,
         value=120, device="wheel"):
    if payload is None:
        payload = [intool.KEYS["a"]] if kind == "press" else 1
    return {"kind": kind, "payload": payload, "state": state,
            "interval": interval, "value": value, "device": device}


def fields(**overrides):
    base = {
        "h": "0", "m": "0", "s": "1", "ms": "0", "clicks": "1", "repeat": "0",
        "action": "mouse0", "hotkey": "f8", "state": "press & release",
        "device": "wheel", "interval": "100ms", "value": "120",
    }
    base.update(overrides)
    return base


class DurationTests(unittest.TestCase):
    def test_plain_seconds(self):
        self.assertEqual(intool.parse_duration("0.5"), 0.5)

    def test_units(self):
        self.assertAlmostEqual(intool.parse_duration("500ms"), 0.5)
        self.assertAlmostEqual(intool.parse_duration("2s"), 2.0)
        self.assertAlmostEqual(intool.parse_duration("1m30s"), 90.0)
        self.assertAlmostEqual(intool.parse_duration("1h"), 3600.0)

    def test_spaces_and_case_are_ignored(self):
        self.assertAlmostEqual(intool.parse_duration(" 1M 30S "), 90.0)

    def test_invalid_values_raise(self):
        for text in ("", "abc", "-1", "nan", "inf", "5x", "ms"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    intool.parse_duration(text)

    def test_split_duration_roundtrip(self):
        self.assertEqual(intool.split_duration(3661.5), (1, 1, 1, 500))
        self.assertEqual(intool.split_duration(0), (0, 0, 0, 0))


class ParsingTests(unittest.TestCase):
    def test_parse_action_mouse_and_scroll(self):
        self.assertEqual(intool.parse_action("mouse0"), ("press", [intool.BTN_LEFT]))
        self.assertEqual(intool.parse_action("scrollup"), ("scroll", 1))
        self.assertEqual(intool.parse_action("scrolldown"), ("scroll", -1))

    def test_parse_action_combo(self):
        self.assertEqual(
            intool.parse_action("ctrl+z"),
            ("press", [intool.KEYS["ctrl"], intool.KEYS["z"]]),
        )

    def test_parse_action_unknown(self):
        self.assertIsNone(intool.parse_action("notakey"))
        self.assertIsNone(intool.parse_action(""))
        self.assertIsNone(intool.parse_action(None))

    def test_parse_action_directives(self):
        a = intool.KEYS["a"]
        self.assertEqual(intool.parse_action("down:a"), ("down", [a]))
        self.assertEqual(intool.parse_action("up:ctrl+a"), ("up", [intool.KEYS["ctrl"], a]))
        self.assertEqual(intool.parse_action("wait:250ms"), ("wait", 0.25))
        self.assertEqual(intool.parse_action("move:10:-5"), ("move", (10, -5)))
        self.assertEqual(intool.parse_action("pos: 800 : 600"), ("pos", (800, 600)))

    def test_parse_action_rejects_malformed_directives(self):
        for text in ("down:", "down:nope", "wait:", "wait:soon", "move:1", "pos:1:x", "jump:1:2"):
            with self.subTest(text=text):
                self.assertIsNone(intool.parse_action(text))

    def test_parse_sequence(self):
        steps = intool.parse_sequence("pos:10:20, mouse0, wait:1s, scrollup")
        self.assertEqual(steps, (
            ("pos", (10, 20)), ("press", [intool.BTN_LEFT]), ("wait", 1.0), ("scroll", 1),
        ))
        self.assertEqual(intool.parse_sequence("a"), (("press", [intool.KEYS["a"]]),))

    def test_parse_sequence_rejects_bad_or_empty_input(self):
        for text in ("", " , ", "a, nope", None):
            with self.subTest(text=text):
                self.assertIsNone(intool.parse_sequence(text))

    def test_parse_hotkeys(self):
        chords = intool.parse_hotkeys("ctrl+k, ctrl+c")
        self.assertEqual([target for _, target in chords], [intool.KEYS["k"], intool.KEYS["c"]])
        self.assertEqual(len(intool.parse_hotkeys("f8")), 1)

    def test_parse_hotkeys_rejects_bad_input(self):
        for text in ("", "f8, bogus", "bogus+f6", None):
            with self.subTest(text=text):
                self.assertIsNone(intool.parse_hotkeys(text))

    def test_to_bool(self):
        self.assertTrue(intool.to_bool(True))
        self.assertTrue(intool.to_bool("True"))
        self.assertFalse(intool.to_bool("False"))
        self.assertFalse(intool.to_bool(None))

    def test_parse_hotkey(self):
        mods, target = intool.parse_hotkey("ctrl+f6")
        self.assertEqual(target, intool.KEYS["f6"])
        self.assertEqual(len(mods), 1)
        self.assertIn(intool.KEYS["leftctrl"], intool.MODS["ctrl"])

    def test_parse_hotkey_rejects_bad_input(self):
        self.assertIsNone(intool.parse_hotkey("bogus"))
        self.assertIsNone(intool.parse_hotkey("bogus+f6"))
        self.assertIsNone(intool.parse_hotkey(""))

    def test_aliases(self):
        self.assertEqual(intool.match_choice("both", intool.STATES), "press & release")
        self.assertEqual(intool.match_choice("helper", intool.LinuxInput.MODES), "root helper")
        self.assertEqual(intool.match_choice("OLED", ["Auto", "OLED Black"]), "OLED Black")

    def test_match_choice_exact_beats_prefix(self):
        self.assertEqual(intool.match_choice("press", intool.STATES), "press")

    def test_match_choice_ambiguous_prefix_is_rejected(self):
        self.assertIsNone(intool.match_choice("p", intool.STATES))
        self.assertIsNone(intool.match_choice("", intool.STATES))


class PlanTests(unittest.TestCase):
    def test_press_only_holds_keys(self):
        steps = intool.plan(spec("press", [30], state="press"))
        self.assertEqual(steps, [("key", 30, True)])

    def test_release_only_releases_in_reverse_order(self):
        steps = intool.plan(spec("press", [29, 30], state="release"))
        self.assertEqual(steps, [("key", 30, False), ("key", 29, False)])

    def test_press_and_release_waits_between_down_and_up(self):
        steps = intool.plan(spec("press", [29, 30], interval=0.05))
        self.assertEqual(steps, [
            ("key", 29, True), ("key", 30, True),
            ("wait", 0.05),
            ("key", 30, False), ("key", 29, False),
        ])

    def test_wheel_uses_whole_notches(self):
        steps = intool.plan(spec("scroll", 1, state="press & release", value=240, device="wheel"))
        self.assertEqual(steps, [("wheel", 240, 2)])

    def test_wheel_value_below_one_notch_rounds_up_to_one(self):
        steps = intool.plan(spec("scroll", -1, value=10, device="wheel", state="press"))
        self.assertEqual(steps, [("wheel", -120, -1)])

    def test_touchpad_sends_smooth_steps(self):
        steps = intool.plan(spec("scroll", 1, value=30, device="touchpad", state="press"))
        self.assertEqual(steps, [("wheel", 30, 0)])

    def test_wheel_and_touchpad_ignore_release(self):
        for device in ("wheel", "touchpad"):
            with self.subTest(device=device):
                self.assertEqual(
                    intool.plan(spec("scroll", 1, device=device, state="release")), []
                )

    def test_pinch_holds_ctrl_around_the_wheel_step(self):
        ctrl = intool.KEYS["leftctrl"]
        steps = intool.plan(spec("scroll", 1, value=120, device="pinch",
                                 state="press & release", interval=0.02))
        self.assertEqual(steps, [
            ("key", ctrl, True), ("wheel", 120, 1),
            ("wait", 0.02), ("key", ctrl, False),
        ])

    def test_pinch_press_keeps_ctrl_held_and_release_frees_it(self):
        ctrl = intool.KEYS["leftctrl"]
        self.assertEqual(intool.plan(spec("scroll", 1, device="pinch", state="press"))[0],
                         ("key", ctrl, True))
        self.assertEqual(intool.plan(spec("scroll", 1, device="pinch", state="release")),
                         [("key", ctrl, False)])


class SequencePlanTests(unittest.TestCase):
    def test_down_presses_in_order_and_up_releases_in_reverse(self):
        self.assertEqual(intool.plan(spec("down", [29, 30])), [("key", 29, True), ("key", 30, True)])
        self.assertEqual(intool.plan(spec("up", [29, 30])), [("key", 30, False), ("key", 29, False)])

    def test_down_and_up_ignore_the_key_state(self):
        self.assertEqual(intool.plan(spec("down", [30], state="release")), [("key", 30, True)])
        self.assertEqual(intool.plan(spec("up", [30], state="press")), [("key", 30, False)])

    def test_wait_move_and_pos(self):
        self.assertEqual(intool.plan(spec("wait", 0.5)), [("wait", 0.5)])
        self.assertEqual(intool.plan(spec("move", [3, -4])), [("move", 3, -4)])
        self.assertEqual(intool.plan(spec("pos", [800, 600])), [("warp", 800, 600)])


class InjectorTests(unittest.TestCase):
    def test_pulse_moves_and_warps_the_pointer(self):
        injector = RecordingInjector()
        injector.pulse(spec("move", [5, 6]))
        injector.pulse(spec("pos", [7, 8]))
        self.assertEqual([s for s in injector.steps if s[0] != "flush"],
                         [("move", 5, 6), ("warp", 7, 8)])

    def test_backends_without_pointer_support_raise(self):
        injector = intool.Injector()
        with self.assertRaises(intool.BackendError):
            injector.move(1, 1)
        with self.assertRaises(intool.BackendError):
            injector.warp(1, 1)

    def test_pulse_executes_plan_in_order(self):
        injector = RecordingInjector()
        injector.pulse(spec("press", [30], interval=0.0))
        self.assertEqual(injector.steps[0], ("key", 30, True))
        self.assertIn(("key", 30, False), injector.steps)
        self.assertEqual(injector.held, [])

    def test_press_state_keeps_key_held_until_release_all(self):
        injector = RecordingInjector()
        injector.pulse(spec("press", [30], state="press"))
        self.assertEqual(injector.held, [30])
        injector.release_all()
        self.assertEqual(injector.held, [])
        self.assertIn(("key", 30, False), injector.steps)

    def test_release_all_releases_in_reverse_order(self):
        injector = RecordingInjector()
        injector.pulse(spec("press", [29, 30], state="press"))
        injector.steps.clear()
        injector.release_all()
        keys = [s for s in injector.steps if s[0] == "key"]
        self.assertEqual(keys, [("key", 30, False), ("key", 29, False)])

    def test_repeated_press_does_not_duplicate_held_entry(self):
        injector = RecordingInjector()
        injector.pulse(spec("press", [30], state="press"))
        injector.pulse(spec("press", [30], state="press"))
        self.assertEqual(injector.held, [30])


class JobTests(unittest.TestCase):
    def test_job_from_fields_defaults(self):
        job = intool.job_from_fields(fields())
        self.assertEqual(job.delay, 1.0)
        self.assertEqual(job.state, "press & release")
        self.assertAlmostEqual(job.interval, 0.1)
        self.assertEqual(job.value, 120)
        self.assertEqual(job.device, "wheel")
        self.assertIsNotNone(job.hotkey)

    def test_job_spec_is_json_serializable(self):
        job = intool.job_from_fields(fields(action="ctrl+z"))
        self.assertEqual(json.loads(json.dumps(job.specs()))[0]["state"], "press & release")

    def test_minimum_delay_and_clamps(self):
        job = intool.job_from_fields(fields(s="0", ms="0", clicks="0", repeat="-3"))
        self.assertGreaterEqual(job.delay, 0.001)
        self.assertEqual(job.per_trigger, 1)
        self.assertEqual(job.repeats, 0)

    def test_validation_errors(self):
        cases = {
            "bad number": fields(s="x"),
            "bad action": fields(action="nope"),
            "bad hotkey": fields(hotkey="nope"),
            "bad state": fields(state="sideways"),
            "bad device": fields(device="joystick"),
            "bad interval": fields(interval="soon"),
            "zero value": fields(value="0"),
        }
        for name, case in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    intool.job_from_fields(case)

    def test_hotkey_optional_when_not_listening(self):
        job = intool.job_from_fields(fields(hotkey="nope"), need_hotkey=False)
        self.assertIsNone(job.hotkey)

    def test_job_holds_the_sequence_and_builds_one_spec_per_step(self):
        job = intool.job_from_fields(fields(action="pos:1:2, mouse0, wait:1s"))
        self.assertEqual([kind for kind, _ in job.steps], ["pos", "press", "wait"])
        self.assertEqual([item["kind"] for item in job.specs()], ["pos", "press", "wait"])
        self.assertTrue(all(item["state"] == job.state for item in job.specs()))

    def test_hotkey_sequence_is_parsed(self):
        job = intool.job_from_fields(fields(hotkey="ctrl+k, ctrl+c"))
        self.assertEqual(len(job.hotkey), 2)

    def test_immediate_defaults_off_and_follows_the_field(self):
        self.assertFalse(intool.job_from_fields(fields()).immediate)
        self.assertTrue(intool.job_from_fields(fields(immediate=True)).immediate)
        self.assertTrue(intool.job_from_fields(fields(immediate="True")).immediate)
        self.assertFalse(intool.job_from_fields(fields(immediate="False")).immediate)

    def test_invalid_sequence_step_is_rejected(self):
        with self.assertRaises(ValueError):
            intool.job_from_fields(fields(action="mouse0, pos:1"))

    def test_describe_mentions_steps_and_immediate(self):
        text = intool.job_from_fields(fields(action="a, b", immediate=True)).describe()
        self.assertIn("2 steps", text)
        self.assertIn("immediate", text)

    def test_describe_mentions_state_and_device(self):
        job = intool.job_from_fields(fields(action="scrollup", device="pinch", value="240"))
        text = job.describe()
        self.assertIn("state: press & release", text)
        self.assertIn("pinch 240", text)


class HotkeyResolutionTests(unittest.TestCase):
    def test_defaults_when_nothing_stored(self):
        self.assertEqual(intool.resolve_hotkeys({}, "press"), intool.DEFAULT_HOTKEYS)

    def test_stored_hotkeys_override_defaults(self):
        hotkeys = intool.resolve_hotkeys({"hotkeys": {"press": "f1"}}, "press")
        self.assertEqual(hotkeys["press"], "f1")
        self.assertEqual(hotkeys["release"], intool.DEFAULT_HOTKEYS["release"])

    def test_legacy_single_hotkey_migrates_to_active_state(self):
        hotkeys = intool.resolve_hotkeys({"hotkey": "f2"}, "release")
        self.assertEqual(hotkeys["release"], "f2")

    def test_default_hotkeys_are_distinct_and_valid(self):
        values = list(intool.DEFAULT_HOTKEYS.values())
        self.assertEqual(len(values), len(set(values)))
        for value in values:
            self.assertIsNotNone(intool.parse_hotkey(value))

    def test_default_state_uses_f8(self):
        self.assertEqual(intool.DEFAULT_HOTKEYS["press & release"], "f8")


class ConfigTests(unittest.TestCase):
    def test_write_then_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "config.json")
            self.assertTrue(intool.write_config(path, {"state": "press", "value": "120"}))
            self.assertEqual(intool.read_config(path), {"state": "press", "value": "120"})

    def test_missing_or_corrupt_config_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(intool.read_config(os.path.join(tmp, "missing.json")), {})
            bad = os.path.join(tmp, "bad.json")
            Path(bad).write_text("{not json")
            self.assertEqual(intool.read_config(bad), {})
            listy = os.path.join(tmp, "list.json")
            Path(listy).write_text("[1, 2]")
            self.assertEqual(intool.read_config(listy), {})


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.parser = intool.build_parser()

    def test_state_and_device_choices(self):
        args = self.parser.parse_args(["-S", "both", "-D", "pin"])
        self.assertEqual(args.state, "press & release")
        self.assertEqual(args.device, "pinch")

    def test_invalid_state_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["-S", "sideways"])

    def test_interval_and_value(self):
        args = self.parser.parse_args(["-i", "30ms", "-v", "240"])
        self.assertAlmostEqual(args.interval, 0.03)
        self.assertEqual(args.value, 240)

    def test_value_must_be_positive(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["-v", "0"])

    def test_per_state_hotkey_options(self):
        args = self.parser.parse_args(["--hotkey-press", "f1", "--hotkey-release", "f2",
                                       "--hotkey-press-release", "f3"])
        self.assertEqual((args.hotkey_press, args.hotkey_release, args.hotkey_press_release),
                         ("f1", "f2", "f3"))

    def test_collect_options_applies_cli_over_config(self):
        args = self.parser.parse_args(["-S", "press", "-k", "f9", "-i", "50ms"])
        data = {"hotkeys": {"press & release": "f8"}, "state": "press & release"}
        fields_, hotkeys, backend, theme = intool.collect_options(args, data)
        self.assertEqual(fields_["state"], "press")
        self.assertEqual(fields_["hotkey"], "f9")
        self.assertEqual(hotkeys["press"], "f9")
        self.assertEqual(hotkeys["press & release"], "f8")
        self.assertEqual(fields_["interval"], "50ms")
        self.assertEqual(backend, "Auto")
        self.assertEqual(theme, "Auto")

    def test_collect_options_uses_config_when_no_cli(self):
        args = self.parser.parse_args([])
        data = {"state": "release", "device": "touchpad", "value": "30"}
        fields_, hotkeys, _, _ = intool.collect_options(args, data)
        self.assertEqual(fields_["state"], "release")
        self.assertEqual(fields_["device"], "touchpad")
        self.assertEqual(fields_["value"], "30")
        self.assertEqual(fields_["hotkey"], hotkeys["release"])

    def test_immediate_flag(self):
        self.assertTrue(self.parser.parse_args(["-I"]).immediate)
        self.assertFalse(self.parser.parse_args([]).immediate)

    def test_collect_options_carries_immediate_from_cli_or_config(self):
        cli, *_ = intool.collect_options(self.parser.parse_args(["--immediate"]), {})
        self.assertTrue(cli["immediate"])
        stored, *_ = intool.collect_options(self.parser.parse_args([]), {"immediate": True})
        self.assertTrue(stored["immediate"])
        default, *_ = intool.collect_options(self.parser.parse_args([]), {})
        self.assertFalse(default["immediate"])

    def test_action_and_hotkey_accept_sequences(self):
        args = self.parser.parse_args(["-a", "pos:1:2, mouse0", "-k", "ctrl+k, ctrl+c"])
        self.assertEqual(args.action, "pos:1:2, mouse0")
        self.assertEqual(args.hotkey, "ctrl+k, ctrl+c")

    def test_invalid_sequences_are_rejected(self):
        for argv in (["-a", "mouse0, nope"], ["-a", "pos:1"], ["-k", "f8, nope"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.parser.parse_args(argv)

    def test_pointer_command_flag(self):
        args = self.parser.parse_args(["--pointer-command", "hyprctl cursorpos"])
        self.assertEqual(args.pointer_command, "hyprctl cursorpos")
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["--pointer-command", "bad 'quote"])

    def test_option_flags_are_generated_from_the_registry(self):
        args = self.parser.parse_args([
            "--screen", "1920x1080", "--hotkey-order", "any", "--record-pointer", "path",
            "--no-record-waits", "--record-hotkey", "ctrl+f10",
        ])
        self.assertEqual(args.screen, "1920x1080")
        self.assertEqual(args.hotkey_order, "any order")
        self.assertEqual(args.record_pointer, "path")
        self.assertFalse(args.record_waits)
        self.assertIsNone(self.parser.parse_args([]).record_trim_click)

    def test_invalid_option_values_are_rejected(self):
        for argv in (["--screen", "huge"], ["--move-mode", "sideways"], ["--record-min-wait", "soon"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.parser.parse_args(argv)

    def test_collect_options_merges_cli_over_config_over_defaults(self):
        args = self.parser.parse_args(["--screen", "800x600", "--no-record-trim-click"])
        data = {"screen": "1x1", "record_hotkey": "f9", "move_mode": "exact"}
        fields_, *_ = intool.collect_options(args, data)
        self.assertEqual(fields_["screen"], "800x600")
        self.assertEqual(fields_["record_hotkey"], "f9")
        self.assertEqual(fields_["move_mode"], "exact")
        self.assertFalse(fields_["record_trim_click"])
        self.assertTrue(fields_["record_drop_held"])

    def test_record_flag(self):
        self.assertIsNone(self.parser.parse_args([]).record)
        self.assertEqual(self.parser.parse_args(["-R"]).record, 0.0)
        self.assertAlmostEqual(self.parser.parse_args(["-R", "5s"]).record, 5.0)

    class FakeRecordingEngine:
        instances = []

        def __init__(self, notify, backend="Auto", on_hotkey=None):
            self.notify = notify
            self.running = False
            self.listening = False
            self.jobs = []
            self.configured = None
            type(self).instances.append(self)

        def configure(self, values):
            self.configured = values

        def start_recording(self):
            return True

        def stop_recording(self, by_click=False, chord=()):
            return "down:a, up:a"

        def start(self, job):
            self.jobs.append(job)
            self.notify("state")

        def start_listener(self, hotkey):
            pass

        def shutdown(self):
            pass

    def test_record_mode_prints_the_action_and_can_save_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = os.path.join(tmp, "config.json")
            buffer = io.StringIO()
            with mock.patch.object(intool, "Engine", self.FakeRecordingEngine), redirect_stdout(buffer):
                code = intool.main(["-R", "10ms", "--save", "--quiet", "--config", config])
            self.assertEqual(code, 0)
            self.assertEqual(buffer.getvalue().strip(), "down:a, up:a")
            self.assertEqual(intool.read_config(config)["action"], "down:a, up:a")

    def test_version_flag_prints_version_and_exits(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(SystemExit) as exit_info:
            intool.main(["--version"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn(intool.VERSION, buffer.getvalue())

    def test_version_is_semver_like(self):
        parts = intool.VERSION.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_list_keys_prints_states_and_devices(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            intool.list_keys()
        output = buffer.getvalue()
        self.assertIn("press & release", output)
        self.assertIn("pinch", output)
        self.assertIn("pos:X:Y", output)


class HotkeyMatcherTests(unittest.TestCase):
    CTRL = intool.KEYS["leftctrl"]
    K, C, X = intool.KEYS["k"], intool.KEYS["c"], intool.KEYS["x"]

    def matcher(self, text):
        return intool.HotkeyMatcher(intool.parse_hotkeys(text))

    def test_single_chord_fires_every_time(self):
        matcher = self.matcher("f8")
        f8 = intool.KEYS["f8"]
        self.assertTrue(matcher.feed(f8, {f8}, 0.0))
        self.assertTrue(matcher.feed(f8, {f8}, 0.1))

    def test_chord_needs_its_modifiers(self):
        matcher = self.matcher("ctrl+k")
        self.assertFalse(matcher.feed(self.K, {self.K}, 0.0))
        self.assertTrue(matcher.feed(self.K, {self.CTRL, self.K}, 0.1))

    def test_sequence_fires_after_the_last_chord(self):
        matcher = self.matcher("ctrl+k, ctrl+c")
        self.assertFalse(matcher.feed(self.CTRL, {self.CTRL}, 0.0))
        self.assertFalse(matcher.feed(self.K, {self.CTRL, self.K}, 0.1))
        self.assertTrue(matcher.feed(self.C, {self.CTRL, self.C}, 0.5))
        self.assertFalse(matcher.feed(self.C, {self.CTRL, self.C}, 0.6))

    def test_sequence_resets_after_the_timeout(self):
        matcher = self.matcher("ctrl+k, ctrl+c")
        matcher.feed(self.K, {self.CTRL, self.K}, 0.0)
        self.assertFalse(matcher.feed(self.C, {self.CTRL, self.C}, intool.CHORD_TIMEOUT + 0.1))

    def test_unrelated_key_resets_the_sequence(self):
        matcher = self.matcher("ctrl+k, ctrl+c")
        matcher.feed(self.K, {self.CTRL, self.K}, 0.0)
        matcher.feed(self.X, {self.CTRL, self.X}, 0.1)
        self.assertFalse(matcher.feed(self.C, {self.CTRL, self.C}, 0.2))

    def test_repeating_the_first_chord_restarts_the_sequence(self):
        matcher = self.matcher("ctrl+k, ctrl+c")
        matcher.feed(self.K, {self.CTRL, self.K}, 0.0)
        matcher.feed(self.K, {self.CTRL, self.K}, 0.1)
        self.assertTrue(matcher.feed(self.C, {self.CTRL, self.C}, 0.2))


class FakePointer:
    def __init__(self):
        self.current = (0, 0)
        self.closed = False

    def position(self):
        return self.current

    def close(self):
        self.closed = True


class RecorderTests(unittest.TestCase):
    A = intool.KEYS["a"]
    LEFT = intool.BTN_LEFT

    def recorder(self, times, pointer=None):
        ticks = iter(times)
        return intool.Recorder(pointer, clock=lambda: next(ticks))

    def test_keys_are_recorded_with_waits_between_events(self):
        recorder = self.recorder([0.0, 0.08, 0.58, 0.66])
        b = intool.KEYS["b"]
        for code, value in ((self.A, 1), (self.A, 0), (b, 1), (b, 0)):
            recorder.feed(code, value)
        self.assertEqual(recorder.finish(), "down:a, wait:80ms, up:a, wait:500ms, down:b, wait:80ms, up:b")

    def test_text_round_trips_through_the_parser(self):
        pointer = FakePointer()
        pointer.current = (10, 20)
        recorder = self.recorder([0.0, 0.1], pointer)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        steps = intool.parse_sequence(recorder.finish())
        self.assertEqual(steps, (("pos", (10, 20)), ("down", [self.A]), ("wait", 0.1), ("up", [self.A])))

    def test_pointer_position_is_recorded_only_when_it_changes(self):
        pointer = FakePointer()
        recorder = self.recorder([0.0, 0.1, 0.2, 0.3], pointer)
        pointer.current = (5, 6)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        pointer.current = (7, 8)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        text = recorder.finish()
        self.assertEqual(text.count("pos:"), 2)
        self.assertIn("pos:7:8", text)

    def test_unknown_codes_and_orphan_releases_are_ignored(self):
        recorder = self.recorder([0.0, 0.1])
        recorder.feed(0x145, 1)
        recorder.feed(self.LEFT, 0)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        self.assertEqual(recorder.finish(), "down:a, wait:100ms, up:a")

    def test_the_final_mouse_click_is_dropped(self):
        pointer = FakePointer()
        recorder = self.recorder([0.0, 0.1, 0.5, 0.6], pointer)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        pointer.current = (300, 400)
        recorder.feed(self.LEFT, 1)
        recorder.feed(self.LEFT, 0)
        self.assertEqual(recorder.finish(by_click=True), "pos:0:0, down:a, wait:100ms, up:a")

    def test_unreleased_keys_are_dropped(self):
        recorder = self.recorder([0.0, 0.1, 0.5])
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        recorder.feed(self.LEFT, 1)
        self.assertEqual(recorder.finish(), "down:a, wait:100ms, up:a")

    def test_a_legitimate_click_before_an_unreleased_click_is_kept(self):
        recorder = self.recorder([0.0, 0.1, 0.5])
        recorder.feed(self.LEFT, 1)
        recorder.feed(self.LEFT, 0)
        recorder.feed(self.LEFT, 1)
        self.assertEqual(recorder.finish(), "down:mouse0, wait:100ms, up:mouse0")

    def test_empty_recording_has_no_text(self):
        self.assertEqual(self.recorder([]).finish(), "")

    def test_short_gaps_are_not_recorded_as_waits(self):
        recorder = self.recorder([0.0, 0.001])
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        self.assertEqual(recorder.finish(), "down:a, up:a")


class CodeNameTests(unittest.TestCase):
    def test_every_recordable_name_parses_back_to_its_code(self):
        for code, name in intool.CODE_NAMES.items():
            with self.subTest(name=name):
                self.assertEqual(intool.parse_action(f"down:{name}"), ("down", [code]))

    def test_generic_modifiers_and_aliases_are_not_recorded(self):
        for name in ("shift", "escape", "return", "meta"):
            self.assertNotIn(name, intool.CODE_NAMES.values())


class WindowsBackendTests(unittest.TestCase):
    class FakeUser32:
        def __init__(self):
            self.down = set()
            self.cursor = (0, 0)

        def GetAsyncKeyState(self, vk):
            return 0x8000 if vk in self.down else 0

        def GetCursorPos(self, ref):
            ref._obj.x, ref._obj.y = self.cursor

    @staticmethod
    def relevant(events):
        return sorted(e for e in events if e[0] in (intool.KEYS["a"], intool.BTN_LEFT))

    def test_poller_reports_key_and_mouse_changes(self):
        user32 = self.FakeUser32()
        poller = intool.WindowsKeyPoller(user32)
        user32.down = {intool.KEYS["a"], intool.WIN_MOUSE_VK[intool.BTN_LEFT]}
        self.assertEqual(self.relevant(poller.poll(0)), sorted([(intool.KEYS["a"], 1), (intool.BTN_LEFT, 1)]))
        self.assertEqual(poller.poll(0), [])
        user32.down = set()
        self.assertEqual(self.relevant(poller.poll(0)), sorted([(intool.KEYS["a"], 0), (intool.BTN_LEFT, 0)]))

    def test_poller_ignores_keys_already_held_when_it_starts(self):
        user32 = self.FakeUser32()
        user32.down = {intool.WIN_MOUSE_VK[intool.BTN_LEFT]}
        poller = intool.WindowsKeyPoller(user32)
        self.assertEqual(poller.poll(0), [])
        user32.down = set()
        self.assertEqual(self.relevant(poller.poll(0)), [(intool.BTN_LEFT, 0)])

    def test_pointer_reads_the_cursor_position(self):
        user32 = self.FakeUser32()
        user32.cursor = (12, 34)
        self.assertEqual(intool.WindowsPointer(user32).position(), (12, 34))


class UInputPointerTests(unittest.TestCase):
    def pointer(self, size):
        pointer = intool.UInputPointer.__new__(intool.UInputPointer)
        pointer.size = size
        pointer.sent = []
        pointer._emit = pointer.sent.append
        return pointer

    def test_move_to_emits_absolute_axes_and_clamps_to_the_screen(self):
        pointer = self.pointer((100, 50))
        pointer.move_to(40, 20)
        pointer.move_to(-5, 999)
        self.assertEqual(pointer.sent, [
            [(intool.EV_ABS, intool.ABS_X, 40), (intool.EV_ABS, intool.ABS_Y, 20), (intool.EV_SYN, 0, 0)],
            [(intool.EV_ABS, intool.ABS_X, 0), (intool.EV_ABS, intool.ABS_Y, 49), (intool.EV_SYN, 0, 0)],
        ])

    @unittest.skipUnless(intool.IS_LINUX, "uinput is Linux only")
    def test_create_declares_both_axes_with_the_screen_extent(self):
        pointer = self.pointer((1920, 1080))
        pointer.fd = 99
        with mock.patch.object(intool.fcntl, "ioctl") as ioctl, mock.patch.object(intool.time, "sleep"):
            pointer._create()
        calls = [call.args[1:] for call in ioctl.call_args_list]
        self.assertIn((intool.UI_ABS_SETUP, struct.pack("H2x6i", intool.ABS_X, 0, 0, 1919, 0, 0, 0)), calls)
        self.assertIn((intool.UI_ABS_SETUP, struct.pack("H2x6i", intool.ABS_Y, 0, 0, 1079, 0, 0, 0)), calls)
        self.assertEqual(calls[-1], (intool.UI_DEV_CREATE,))


class OptionTests(unittest.TestCase):
    def test_defaults_resolve_to_typed_values(self):
        options = intool.resolve_options({})
        self.assertIsNone(options["screen"])
        self.assertAlmostEqual(options["record_min_wait"], 0.01)
        self.assertEqual(options["record_hotkey"], intool.parse_hotkeys("f10"))
        self.assertTrue(options["record_waits"])
        self.assertEqual(options["hotkey_order"], "modifiers first")

    def test_screen_parsing(self):
        self.assertEqual(intool.parse_screen("1920x1080"), (1920, 1080))
        self.assertEqual(intool.parse_screen(" 800 X 600 "), (800, 600))
        self.assertIsNone(intool.parse_screen("auto"))
        for text in ("0x600", "wide", "800"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                intool.parse_screen(text)

    def test_empty_record_hotkey_disables_it(self):
        self.assertIsNone(intool.resolve_options({"record_hotkey": ""})["record_hotkey"])
        with self.assertRaises(ValueError):
            intool.resolve_options({"record_hotkey": "nope"})

    def test_errors_name_the_setting(self):
        with self.assertRaises(ValueError) as caught:
            intool.resolve_options({"record_min_wait": "soon"})
        self.assertIn("Minimum wait", str(caught.exception))

    def test_clean_keeps_choices_and_booleans_valid(self):
        cleaned = intool.clean_options({"move_mode": "sideways", "record_waits": "False", "record_pointer": "path"})
        self.assertEqual(cleaned["move_mode"], "auto")
        self.assertFalse(cleaned["record_waits"])
        self.assertEqual(cleaned["record_pointer"], "path")

    def test_valid_options_replaces_only_invalid_text(self):
        cleaned = intool.valid_options({"screen": "huge", "record_hotkey": "f9"})
        self.assertEqual(cleaned["screen"], "auto")
        self.assertEqual(cleaned["record_hotkey"], "f9")

    def test_every_option_has_a_unique_key_and_a_known_group(self):
        keys = [option.key for option in intool.OPTIONS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue({option.group for option in intool.OPTIONS} <= {"input", "recording"})


class RecorderModeTests(unittest.TestCase):
    A = intool.KEYS["a"]

    def recorder(self, times, device=None, **options):
        ticks = iter(times)
        return intool.Recorder(device, clock=lambda: next(ticks), options=intool.RecordOptions(**options))

    def test_relative_coordinates_use_pointer_deltas(self):
        pointer = FakePointer()
        recorder = self.recorder([0.0, 0.1, 0.2, 0.3], pointer, coordinates="relative")
        pointer.current = (10, 10)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        pointer.current = (15, 7)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        self.assertEqual(recorder.finish(),
                         "down:a, wait:100ms, up:a, wait:100ms, move:5:-3, down:a, wait:100ms, up:a")

    def test_motion_events_become_relative_moves_without_a_pointer(self):
        recorder = self.recorder([0.0, 0.1])
        self.assertTrue(recorder.wants_motion)
        recorder.motion(intool.POINTER_DX, 4)
        recorder.motion(intool.POINTER_DX, 3)
        recorder.motion(intool.POINTER_DY, -2)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        self.assertEqual(recorder.finish(), "move:7:-2, down:a, wait:100ms, up:a")

    def test_pointer_off_records_only_keys_and_ignores_motion(self):
        pointer = FakePointer()
        pointer.current = (9, 9)
        recorder = self.recorder([0.0, 0.1], pointer, pointer="off")
        self.assertFalse(recorder.wants_motion)
        recorder.motion(intool.POINTER_DX, 4)
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        self.assertEqual(recorder.finish(), "down:a, wait:100ms, up:a")

    def test_path_mode_samples_the_pointer_between_events(self):
        pointer = FakePointer()
        recorder = self.recorder([0.0, 0.06, 0.12, 0.2, 0.3], pointer, pointer="path", path_interval=0.05)
        recorder.tick()
        pointer.current = (5, 5)
        recorder.tick()
        pointer.current = (9, 9)
        recorder.tick()
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        text = recorder.finish()
        self.assertTrue(text.startswith("pos:0:0, wait:60ms, pos:5:5, wait:60ms, pos:9:9"))
        self.assertTrue(text.endswith("down:a, wait:100ms, up:a"))

    def test_path_samples_respect_the_interval(self):
        pointer = FakePointer()
        recorder = self.recorder([0.0, 0.01, 0.02], pointer, pointer="path", path_interval=0.05)
        recorder.tick()
        pointer.current = (1, 1)
        recorder.tick()
        pointer.current = (2, 2)
        recorder.tick()
        self.assertEqual(recorder.steps, [("pos", (0, 0))])

    def test_at_events_mode_ignores_ticks(self):
        pointer = FakePointer()
        recorder = self.recorder([], pointer)
        recorder.tick()
        self.assertEqual(recorder.steps, [])

    def test_waits_can_be_turned_off_and_the_minimum_changed(self):
        quiet = self.recorder([0.0, 0.5], waits=False)
        quiet.feed(self.A, 1)
        quiet.feed(self.A, 0)
        self.assertEqual(quiet.finish(), "down:a, up:a")
        coarse = self.recorder([0.0, 0.05, 0.4], min_wait=0.1, drop_held=False)
        coarse.feed(self.A, 1)
        coarse.feed(self.A, 0)
        coarse.feed(self.A, 1)
        self.assertEqual(coarse.finish(), "down:a, up:a, wait:350ms, down:a")

    def test_stop_click_is_kept_when_trimming_is_off(self):
        recorder = self.recorder([0.0, 0.1], trim_click=False)
        recorder.feed(intool.BTN_LEFT, 1)
        recorder.feed(intool.BTN_LEFT, 0)
        self.assertEqual(recorder.finish(by_click=True), "down:mouse0, wait:100ms, up:mouse0")

    def test_held_keys_are_kept_when_dropping_is_off(self):
        recorder = self.recorder([0.0, 0.1, 0.2], drop_held=False)
        recorder.feed(self.A, 1)
        recorder.feed(intool.KEYS["b"], 1)
        recorder.feed(intool.KEYS["b"], 0)
        self.assertEqual(recorder.finish(), "down:a, wait:100ms, down:b, wait:100ms, up:b")

    def test_recording_hotkey_chord_is_trimmed(self):
        f10 = intool.KEYS["f10"]
        recorder = self.recorder([0.0, 0.1, 0.5, 0.6])
        recorder.feed(self.A, 1)
        recorder.feed(self.A, 0)
        recorder.feed(f10, 1)
        self.assertEqual(recorder.finish(chord={f10}), "down:a, wait:100ms, up:a")

    def test_events_after_finishing_are_ignored(self):
        recorder = self.recorder([0.0])
        recorder.finish()
        recorder.feed(self.A, 1)
        recorder.motion(intool.POINTER_DX, 3)
        self.assertEqual(recorder.steps, [])

    def test_finishing_closes_the_pointer(self):
        pointer = FakePointer()
        self.recorder([], pointer).finish()
        self.assertTrue(pointer.closed)

    def test_options_come_from_resolved_settings(self):
        options = intool.RecordOptions.from_options(intool.resolve_options({
            "record_pointer": "path", "record_coordinates": "relative", "record_waits": False,
            "record_min_wait": "20ms", "record_path_interval": "0ms",
        }))
        self.assertEqual((options.pointer, options.coordinates, options.waits), ("path", "relative", False))
        self.assertAlmostEqual(options.min_wait, 0.02)
        self.assertGreater(options.path_interval, 0)


class HotkeyOrderTests(unittest.TestCase):
    CTRL, K = intool.KEYS["leftctrl"], intool.KEYS["k"]

    def test_modifiers_first_ignores_a_late_modifier(self):
        matcher = intool.HotkeyMatcher(intool.parse_hotkeys("ctrl+k"))
        self.assertFalse(matcher.feed(self.K, {self.K}, 0.0))
        self.assertFalse(matcher.feed(self.CTRL, {self.K, self.CTRL}, 0.1))

    def test_any_order_fires_when_the_modifier_arrives_last(self):
        matcher = intool.HotkeyMatcher(intool.parse_hotkeys("ctrl+k"), any_order=True)
        self.assertFalse(matcher.feed(self.K, {self.K}, 0.0))
        self.assertTrue(matcher.feed(self.CTRL, {self.K, self.CTRL}, 0.1))

    def test_any_order_still_fires_the_normal_way(self):
        matcher = intool.HotkeyMatcher(intool.parse_hotkeys("ctrl+k"), any_order=True)
        self.assertTrue(matcher.feed(self.K, {self.CTRL, self.K}, 0.0))

    def test_codes_lists_every_chord_key(self):
        matcher = intool.HotkeyMatcher(intool.parse_hotkeys("ctrl+k, f8"))
        self.assertIn(self.K, matcher.codes())
        self.assertIn(intool.KEYS["f8"], matcher.codes())
        self.assertIn(self.CTRL, matcher.codes())


class WaylandScreenTests(unittest.TestCase):
    MUTTER = (
        "(uint32 2, [(('eDP-1', 'BOE', '0x0747', '0x00000000'), "
        "[('1920x1080@60.000', 1920, 1080, 60.0, 1.0, [1.0, 2.0], {'is-preferred': <true>}), "
        "('1920x1080@48.000', 1920, 1080, 48.0, 1.0, [1.0], {'is-current': <true>})], {'is-builtin': <true>}), "
        "(('HDMI-1', 'DEL', '0x4141', '0x01'), "
        "[('2560x1440@60.000', 2560, 1440, 60.0, 1.0, [1.0], {'is-current': <true>})], {})], "
        "[(0, 0, 1.0, uint32 0, true, [('eDP-1', 'BOE', '0x0747', '0x00000000')], {}), "
        "(1920, 0, 1.0, uint32 0, false, [('HDMI-1', 'DEL', '0x4141', '0x01')], {})], "
        "{'layout-mode': <uint32 1>})"
    )

    def test_hyprland_uses_logical_sizes_and_both_monitors(self):
        text = json.dumps([
            {"name": "eDP-1", "width": 2880, "height": 1800, "x": 0, "y": 0, "scale": 1.5, "transform": 0},
            {"name": "HDMI-A-1", "width": 1920, "height": 1080, "x": 1920, "y": 0, "scale": 1.0, "transform": 0},
        ])
        self.assertEqual(intool.screen_extent(intool.boxes_hyprland(text)), (3840, 1200))

    def test_hyprland_rotation_swaps_the_axes(self):
        text = json.dumps([{"width": 1920, "height": 1080, "x": 0, "y": 0, "scale": 1, "transform": 1}])
        self.assertEqual(intool.screen_extent(intool.boxes_hyprland(text)), (1080, 1920))

    def test_sway_ignores_inactive_outputs(self):
        text = json.dumps([
            {"active": True, "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
            {"active": False, "rect": {"x": 1920, "y": 0, "width": 1280, "height": 720}},
        ])
        self.assertEqual(intool.screen_extent(intool.boxes_sway(text)), (1920, 1080))

    def test_kscreen_divides_by_scale_and_skips_disabled(self):
        text = json.dumps({"outputs": [
            {"enabled": True, "pos": {"x": 0, "y": 0}, "size": {"width": 3840, "height": 2160},
             "scale": 2.0, "rotation": 1},
            {"enabled": False, "pos": {"x": 1920, "y": 0}, "size": {"width": 1920, "height": 1080},
             "scale": 1.0, "rotation": 1},
        ]})
        self.assertEqual(intool.screen_extent(intool.boxes_kscreen(text)), (1920, 1080))

    def test_wlr_randr_uses_the_current_mode_and_transform(self):
        text = json.dumps([{
            "enabled": True, "position": {"x": 0, "y": 0}, "transform": "90", "scale": 1.0,
            "modes": [{"width": 1920, "height": 1080, "current": False},
                      {"width": 2560, "height": 1440, "current": True}],
        }])
        self.assertEqual(intool.screen_extent(intool.boxes_wlr(text)), (1440, 2560))

    def test_mutter_pairs_current_modes_with_logical_monitors(self):
        self.assertEqual(intool.screen_extent(intool.boxes_mutter(self.MUTTER)), (4480, 1440))

    def test_mutter_scale_and_physical_layout_mode(self):
        scaled = self.MUTTER.replace("1920, 0, 1.0", "1920, 0, 2.0")
        self.assertEqual(intool.screen_extent(intool.boxes_mutter(scaled)), (3200, 1080))
        physical = self.MUTTER.replace("<uint32 1>", "<uint32 2>").replace("1920, 0, 1.0", "1920, 0, 2.0")
        self.assertEqual(intool.screen_extent(intool.boxes_mutter(physical)), (4480, 1440))

    def test_mutter_rejects_a_layout_it_cannot_pair(self):
        mirrored = self.MUTTER.replace("(1920, 0, 1.0, uint32 0, false, [('HDMI-1', 'DEL', '0x4141', '0x01')], {})", "")
        with self.assertRaises(ValueError):
            intool.boxes_mutter(mirrored.replace("{}), ]", "{})]"))

    def test_extent_of_nothing_is_unknown(self):
        self.assertIsNone(intool.screen_extent([]))

    def test_probe_tries_each_tool_in_turn_and_survives_garbage(self):
        outputs = {
            "hyprctl": "not json",
            "swaymsg": json.dumps([{"active": True, "rect": {"x": 0, "y": 0, "width": 800, "height": 600}}]),
        }

        def fake_run(argv, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout=outputs.get(argv[0], ""))

        with mock.patch.object(intool.shutil, "which", side_effect=lambda name: name), \
                mock.patch.object(intool.subprocess, "run", side_effect=fake_run):
            self.assertEqual(intool.wayland_screen(), (800, 600))

    def test_probe_with_no_tools_is_unknown(self):
        with mock.patch.object(intool.shutil, "which", return_value=None):
            self.assertIsNone(intool.wayland_screen())

    def test_probe_prefers_wayland_tools_under_wayland(self):
        with mock.patch.object(intool, "IS_LINUX", True), \
                mock.patch.dict(intool.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
                mock.patch.object(intool, "wayland_screen", return_value=(1280, 720)), \
                mock.patch.object(intool, "X11Connection", side_effect=AssertionError("no X11 needed")):
            self.assertEqual(intool.probe_screen(), (1280, 720))


class CommandPointerTests(unittest.TestCase):
    @staticmethod
    def command(text):
        return [intool.sys.executable, "-c", f"print({text!r})"]

    def test_reads_the_first_two_integers(self):
        pointer = intool.CommandPointer(self.command("x:120 y:-45 screen:0"))
        self.assertEqual(pointer.position(), (120, -45))
        self.assertEqual(intool.CommandPointer(self.command("812, 303")).position(), (812, 303))

    def test_rejects_output_without_coordinates(self):
        with self.assertRaises(intool.BackendError):
            intool.CommandPointer(self.command("nothing here"))

    def test_rejects_a_missing_program(self):
        with self.assertRaises(intool.BackendError):
            intool.CommandPointer(["intool-no-such-program"])

    def test_explicit_command_takes_precedence(self):
        pointer = intool.open_pointer(self.command("3 4"))
        self.assertIsInstance(pointer, intool.CommandPointer)

    @unittest.skipUnless(intool.IS_LINUX, "Linux only")
    def test_wayland_falls_back_to_a_compositor_command(self):
        created = []

        def fake_pointer(argv):
            created.append(argv)
            return "pointer"

        with mock.patch.object(intool, "X11Connection", side_effect=intool.BackendError("wayland")), \
                mock.patch.object(intool.shutil, "which", side_effect=lambda name: name == "hyprctl" or None), \
                mock.patch.object(intool, "CommandPointer", side_effect=fake_pointer):
            self.assertEqual(intool.open_pointer(), "pointer")
        self.assertEqual(created, [["hyprctl", "cursorpos"]])

    @unittest.skipUnless(intool.IS_LINUX, "Linux only")
    def test_unknown_desktop_has_no_pointer_reader(self):
        with mock.patch.object(intool, "X11Connection", side_effect=intool.BackendError("wayland")), \
                mock.patch.object(intool.shutil, "which", return_value=None):
            self.assertIsNone(intool.open_pointer())

    def test_command_option_parsing(self):
        self.assertIsNone(intool.parse_command("auto"))
        self.assertIsNone(intool.parse_command(""))
        self.assertEqual(intool.parse_command("kdotool getmouselocation"), ["kdotool", "getmouselocation"])
        self.assertEqual(intool.parse_command("sh -c 'echo 1 2'"), ["sh", "-c", "echo 1 2"])
        with self.assertRaises(ValueError):
            intool.parse_command("unterminated 'quote")

    def test_engine_uses_the_configured_command_for_recording_and_moves(self):
        engine = EngineTests.FakeEngine()
        engine.configure({"pointer_command": "reader --pos"})
        self.assertEqual(engine.pointer_command, ["reader", "--pos"])
        with mock.patch.object(intool, "open_pointer", return_value=None) as opener:
            engine._locate()
        opener.assert_called_once_with(["reader", "--pos"])

    def test_a_failing_configured_command_reports_a_recording_error(self):
        engine = EngineTests.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append(event)
        engine.configure({"pointer_command": "intool-no-such-program"})
        self.assertFalse(engine.start_recording())
        self.assertIn("error", events)
        self.assertFalse(engine.recording)


class EvdevMotionTests(unittest.TestCase):
    def source(self):
        read, write = os.pipe()
        source = intool.EvdevSource.__new__(intool.EvdevSource)
        source._fds = {read: "pipe"}
        source._next_scan = float("inf")
        source.motion = False
        self.addCleanup(os.close, write)
        self.addCleanup(os.close, read)
        return source, write

    @staticmethod
    def packet(*events):
        return b"".join(struct.pack(intool.EVENT_FMT, 0, 0, t, c, v) for t, c, v in events)

    def test_motion_is_ignored_unless_enabled(self):
        source, write = self.source()
        os.write(write, self.packet((intool.EV_REL, intool.REL_X, 5), (intool.EV_KEY, 30, 1)))
        self.assertEqual(source.poll(0.1), [(30, 1)])

    def test_motion_is_summed_per_poll(self):
        source, write = self.source()
        source.set_motion(True)
        os.write(write, self.packet(
            (intool.EV_REL, intool.REL_X, 5), (intool.EV_REL, intool.REL_X, -2),
            (intool.EV_REL, intool.REL_Y, 7), (intool.EV_REL, intool.REL_WHEEL, 1),
        ))
        self.assertEqual(source.poll(0.1), [(intool.POINTER_DX, 3), (intool.POINTER_DY, 7)])

    def test_zero_net_motion_is_not_reported(self):
        source, write = self.source()
        source.set_motion(True)
        os.write(write, self.packet((intool.EV_REL, intool.REL_X, 4), (intool.EV_REL, intool.REL_X, -4)))
        self.assertEqual(source.poll(0.1), [])


class HelperProtocolTests(unittest.TestCase):
    class Device(RecordingInjector):
        closed = False

        def close(self):
            self.closed = True

    def run_helper(self, *messages):
        device = self.Device()
        server = intool.HelperServer(device)
        lines = "".join(json.dumps(message) + "\n" for message in messages)
        with mock.patch.object(intool.sys, "stdin", io.StringIO(lines)), \
                mock.patch.object(intool.sys, "stdout", io.StringIO()):
            server.run()
        return server, device

    def test_screen_op_reaches_the_device(self):
        _, device = self.run_helper({"op": "screen", "size": [1920, 1080]})
        self.assertEqual(device.screen, (1920, 1080))

    def test_motion_op_is_remembered_for_the_next_listener(self):
        server, _ = self.run_helper({"op": "motion", "on": True})
        self.assertTrue(server.motion)

    def test_client_sends_screen_and_motion_ops(self):
        sent = []
        helper = intool.PrivilegedHelper.__new__(intool.PrivilegedHelper)
        helper._send = sent.append
        helper.set_screen((800, 600))
        helper.set_screen(None)
        helper.set_motion(True)
        self.assertEqual(sent, [{"op": "screen", "size": [800, 600]}, {"op": "motion", "on": True}])


class ScreenSizeTests(unittest.TestCase):
    def test_warp_without_a_screen_size_explains_the_setting(self):
        device = intool.UInputDevice.__new__(intool.UInputDevice)
        device.pointer = None
        with self.assertRaises(intool.BackendError) as caught:
            device.warp(1, 2)
        self.assertIn("Screen size", str(caught.exception))

    def test_warp_builds_the_pointer_from_the_configured_screen(self):
        device = intool.UInputDevice.__new__(intool.UInputDevice)
        device.pointer = None
        device.set_screen((640, 480))
        fake = mock.Mock()
        with mock.patch.object(intool, "UInputPointer", return_value=fake) as factory:
            device.warp(3, 4)
            device.warp(5, 6)
        factory.assert_called_once_with(640, 480)
        self.assertEqual(fake.move_to.call_args_list, [mock.call(3, 4), mock.call(5, 6)])

    @unittest.skipUnless(intool.IS_LINUX, "LinuxInput is Linux only")
    def test_linux_input_pushes_the_screen_to_the_injector(self):
        linux = intool.LinuxInput()
        linux._injector = RecordingInjector()
        linux.set_screen((10, 20))
        self.assertEqual(linux._injector.screen, (10, 20))
        with mock.patch.object(intool, "probe_screen", return_value=(30, 40)):
            linux.set_screen(None)
        self.assertEqual(linux._injector.screen, (30, 40))


class EngineTests(unittest.TestCase):
    class FakeEngine(intool.Engine):
        def __init__(self):
            super().__init__(lambda *args: None, "Auto")
            self.linux = None
            self.pulses = []
            self.released = threading.Event()

        def _pulse(self, spec):
            self.pulses.append(spec)

        def _release(self):
            self.released.set()

    def test_finite_job_runs_every_cycle_and_finishes(self):
        events = []
        engine = self.FakeEngine()
        engine.notify = lambda event, *args: events.append(event)
        job = intool.job_from_fields(fields(s="0", ms="1", repeat="3", clicks="2"), need_hotkey=False)
        engine.start(job)
        self._wait_until(lambda: not engine.running)
        self.assertEqual(len(engine.pulses), 6)
        self.assertIn("finished", events)
        self.assertTrue(engine.released.is_set())

    def test_stop_releases_held_keys(self):
        engine = self.FakeEngine()
        job = intool.job_from_fields(fields(action="space", state="press", s="0", ms="5"),
                                     need_hotkey=False)
        engine.start(job)
        self._wait_until(lambda: len(engine.pulses) >= 1)
        engine.stop()
        self._wait_until(lambda: engine.released.is_set())
        self.assertFalse(engine.running)

    def test_immediate_job_acts_before_the_first_delay(self):
        engine = self.FakeEngine()
        engine.start(intool.job_from_fields(fields(s="30", immediate=True), need_hotkey=False))
        self._wait_until(lambda: len(engine.pulses) >= 1)
        engine.stop()

    def test_job_without_immediate_waits_for_the_delay(self):
        engine = self.FakeEngine()
        engine.start(intool.job_from_fields(fields(s="30"), need_hotkey=False))
        time.sleep(0.1)
        self.assertEqual(engine.pulses, [])
        engine.stop()

    def test_stop_interrupts_a_long_wait_step(self):
        engine = self.FakeEngine()
        job = intool.job_from_fields(fields(action="a, wait:30s, b", s="0", ms="1"), need_hotkey=False)
        engine.start(job)
        self._wait_until(lambda: len(engine.pulses) == 1)
        engine.stop()
        self._wait_until(lambda: engine.released.is_set())
        self.assertEqual(len(engine.pulses), 1)

    def test_every_step_of_the_sequence_runs_each_cycle(self):
        engine = self.FakeEngine()
        job = intool.job_from_fields(
            fields(action="pos:1:2, mouse0", s="0", ms="1", repeat="2"), need_hotkey=False,
        )
        engine.start(job)
        self._wait_until(lambda: not engine.running and engine.released.is_set())
        self.assertEqual([item["kind"] for item in engine.pulses], ["pos", "press"] * 2)

    class FakeSource:
        label = "fake"

        def __init__(self, batches):
            self.batches = list(batches)
            self.closed = False

        motion = False

        def set_motion(self, enabled):
            self.motion = enabled

        def poll(self, timeout):
            if self.batches:
                return self.batches.pop(0)
            time.sleep(0.005)
            return []

        def close(self):
            self.closed = True

    def test_recording_captures_events_and_reports_the_text(self):
        engine = self.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append((event, args))
        source = self.FakeSource([[(intool.KEYS["a"], 1)], [(intool.KEYS["a"], 0)]])
        engine._open_source = lambda: source
        with mock.patch.object(intool, "open_pointer", return_value=None):
            self.assertTrue(engine.start_recording())
            self.assertTrue(engine.recording)
            self._wait_until(lambda: not source.batches)
            engine.stop_recording()
            self._wait_until(lambda: not engine.recording)
        text = next(args[0] for event, args in events if event == "recorded")
        steps = [step for step in intool.parse_sequence(text) if step[0] != "wait"]
        self.assertEqual(steps, [("down", [intool.KEYS["a"]]), ("up", [intool.KEYS["a"]])])
        self._wait_until(lambda: source.closed)

    def test_recording_failure_is_reported_and_clears_the_flag(self):
        engine = self.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append(event)

        def fail():
            raise intool.BackendError("no source")

        engine._open_source = fail
        engine.start_recording()
        self._wait_until(lambda: not engine.recording)
        self.assertIn("error", events)
        self.assertNotIn("recorded", events)

    def test_recording_is_blocked_while_running_and_blocks_starting(self):
        engine = self.FakeEngine()
        engine.running = True
        self.assertFalse(engine.start_recording())
        engine.running = False
        engine.recording = True
        self.assertFalse(engine.start(intool.job_from_fields(fields(), need_hotkey=False)))

    def test_listening_and_recording_share_one_source(self):
        engine = self.FakeEngine()
        opened = []
        source = self.FakeSource([])

        def open_source():
            opened.append(source)
            return source

        engine._open_source = open_source
        with mock.patch.object(intool, "open_pointer", return_value=None):
            self.assertTrue(engine.start_listener(intool.parse_hotkeys("f8")))
            self.assertTrue(engine.start_recording())
            self._wait_until(lambda: opened)
            engine.stop_recording()
            self.assertTrue(engine.listening)
            engine.stop_listener()
            self._wait_until(lambda: source.closed)
        self.assertEqual(len(opened), 1)

    def test_configure_applies_and_validates_options(self):
        engine = self.FakeEngine()
        engine.configure({"hotkey_order": "any order", "move_mode": "exact", "record_waits": False})
        self.assertTrue(engine.any_order)
        self.assertEqual(engine.move_mode, "exact")
        self.assertFalse(engine.record_options.waits)
        with self.assertRaises(ValueError):
            engine.configure({"screen": "huge"})
        self.assertEqual(engine.move_mode, "exact")

    class FakeLocator:
        def __init__(self, position):
            self.current = position

        def position(self):
            return self.current

        def close(self):
            pass

    def test_exact_moves_become_absolute_positions(self):
        engine = self.FakeEngine()
        engine.configure({"move_mode": "exact"})
        engine._locator = self.FakeLocator((100, 200))
        resolved = engine._resolve_move(spec("move", [5, -10]))
        self.assertEqual((resolved["kind"], resolved["payload"]), ("pos", (105, 190)))

    def test_consecutive_moves_chain_from_the_tracked_cursor(self):
        engine = self.FakeEngine()
        engine._locator = self.FakeLocator((0, 0))
        engine._resolve_move(spec("move", [10, 10]))
        engine._locator.current = (0, 0)
        resolved = engine._resolve_move(spec("move", [1, 1]))
        self.assertEqual(resolved["payload"], (11, 11))

    def test_pos_steps_set_the_cursor_for_later_moves(self):
        engine = self.FakeEngine()
        engine._resolve_move(spec("pos", [50, 60]))
        resolved = engine._resolve_move(spec("move", [1, 2]))
        self.assertEqual(resolved["payload"], (51, 62))

    def test_device_mode_keeps_relative_moves(self):
        engine = self.FakeEngine()
        engine.configure({"move_mode": "device"})
        engine._locator = self.FakeLocator((1, 1))
        step = spec("move", [3, 4])
        self.assertIs(engine._resolve_move(step), step)

    def test_auto_mode_falls_back_to_device_moves_without_a_locator(self):
        engine = self.FakeEngine()
        step = spec("move", [3, 4])
        with mock.patch.object(intool, "open_pointer", return_value=None):
            self.assertIs(engine._resolve_move(step), step)

    def test_exact_mode_without_a_locator_is_an_error(self):
        engine = self.FakeEngine()
        engine.configure({"move_mode": "exact"})
        with mock.patch.object(intool, "open_pointer", return_value=None):
            with self.assertRaises(intool.BackendError):
                engine._resolve_move(spec("move", [3, 4]))

    def test_other_steps_pass_through_untouched(self):
        engine = self.FakeEngine()
        step = spec("press", [30])
        self.assertIs(engine._resolve_move(step), step)

    def test_recording_hotkey_starts_and_stops_a_recording(self):
        engine = self.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append((event, args))
        f10, a = intool.KEYS["f10"], intool.KEYS["a"]
        source = self.FakeSource([[(f10, 1)], [(f10, 0)], [(a, 1)], [(a, 0)], [(f10, 1)]])
        engine._open_source = lambda: source
        with mock.patch.object(intool, "open_pointer", return_value=None):
            engine.start_listener(intool.parse_hotkeys("f8"))
            self._wait_until(lambda: any(event == "recorded" for event, _ in events))
        text = next(args[0] for event, args in events if event == "recorded")
        steps = [step for step in intool.parse_sequence(text) if step[0] != "wait"]
        self.assertEqual(steps, [("down", [a]), ("up", [a])])
        self.assertFalse(engine.recording)
        self.assertTrue(engine.listening)
        engine.stop_listener()

    def test_job_hotkey_is_ignored_while_recording(self):
        engine = self.FakeEngine()
        fired = []
        engine.on_hotkey = lambda: fired.append(True)
        f8 = intool.KEYS["f8"]
        source = self.FakeSource([[(f8, 1)], [(f8, 0)]])
        engine._open_source = lambda: source
        with mock.patch.object(intool, "open_pointer", return_value=None):
            engine.start_recording()
            engine.start_listener(intool.parse_hotkeys("f8"))
            self._wait_until(lambda: not source.batches)
            time.sleep(0.05)
            engine.stop_recording()
        self.assertEqual(fired, [])
        engine.stop_listener()

    def test_job_hotkey_fires_when_not_recording(self):
        engine = self.FakeEngine()
        fired = []
        engine.on_hotkey = lambda: fired.append(True)
        f8 = intool.KEYS["f8"]
        source = self.FakeSource([[(f8, 1)]])
        engine._open_source = lambda: source
        engine.start_listener(intool.parse_hotkeys("f8"))
        self._wait_until(lambda: fired)
        engine.stop_listener()

    def test_motion_flag_follows_the_recording(self):
        engine = self.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append((event, args))
        a = intool.KEYS["a"]
        source = self.FakeSource([[(intool.POINTER_DX, 5), (intool.POINTER_DY, -3)], [(a, 1)], [(a, 0)]])
        engine._open_source = lambda: source
        with mock.patch.object(intool, "open_pointer", return_value=None):
            engine.start_recording()
            self._wait_until(lambda: not source.batches)
            self.assertTrue(source.motion)
            time.sleep(0.05)
            text = engine.stop_recording()
        self.assertTrue(text.startswith("move:5:-3, down:a"))

    def test_absolute_coordinates_without_a_pointer_are_an_error(self):
        engine = self.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append(event)
        engine.configure({"record_coordinates": "absolute"})
        with mock.patch.object(intool, "open_pointer", return_value=None):
            self.assertFalse(engine.start_recording())
        self.assertIn("error", events)
        self.assertFalse(engine.recording)

    def test_capture_failure_stops_listening_and_recording(self):
        engine = self.FakeEngine()
        events = []
        engine.notify = lambda event, *args: events.append(event)

        def fail():
            raise intool.BackendError("no source")

        engine._open_source = fail
        with mock.patch.object(intool, "open_pointer", return_value=None):
            engine.start_listener(intool.parse_hotkeys("f8"))
            engine.start_recording()
            self._wait_until(lambda: not engine.recording and not engine.listening)
        self.assertIn("error", events)

    def test_listener_hotkey_can_be_swapped_without_restart(self):
        engine = self.FakeEngine()
        first = intool.parse_hotkey("f7")
        second = intool.parse_hotkey("f9")
        engine._hotkey = first
        engine.set_hotkey(second)
        self.assertIs(engine._hotkey, second)

    @staticmethod
    def _wait_until(predicate, timeout=5.0):
        deadline = threading.Event()
        for _ in range(int(timeout / 0.01)):
            if predicate():
                return
            deadline.wait(0.01)
        raise AssertionError("condition not met in time")


class ScriptHygieneTests(unittest.TestCase):
    def test_script_has_no_comments(self):
        for number, line in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                self.fail(f"comment on line {number}")

    def test_script_compiles(self):
        compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")


@unittest.skipIf(intool.tk is None, "tkinter is not installed")
class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = intool.tk.Tk()
        except intool.tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "config.json")
        self.app = intool.App(self.root, self.config)
        self.root.update()

    def tearDown(self):
        self.app.engine.shutdown()
        self.root.destroy()
        self.tmp.cleanup()

    @staticmethod
    def event_from(value):
        return types.SimpleNamespace(widget=types.SimpleNamespace(get=lambda: value))

    def test_window_is_resizable_with_minimum_size(self):
        self.assertEqual(self.root.resizable(), (True, True))
        self.assertGreater(self.root.minsize()[0], 0)

    def test_state_change_loads_that_states_hotkey(self):
        self.app.on_state_change(self.event_from("press"))
        self.assertEqual(self.app.entries["hotkey"].get(), intool.DEFAULT_HOTKEYS["press"])
        self.assertEqual(self.app.labels["hotkey"].cget("text"), "Hotkey (press):")

    def test_hotkey_entry_is_disabled_while_listening(self):
        self.app.engine.listening = True
        self.app.sync_state()
        self.assertEqual(str(self.app.entries["hotkey"].cget("state")), "disabled")
        self.app.engine.listening = False
        self.app.sync_state()
        self.assertEqual(str(self.app.entries["hotkey"].cget("state")), "normal")

    def test_set_entry_works_on_disabled_entry(self):
        self.app.entries["hotkey"].configure(state="disabled")
        self.app.set_entry("hotkey", "f5")
        self.assertEqual(self.app.entries["hotkey"].get(), "f5")
        self.assertEqual(str(self.app.entries["hotkey"].cget("state")), "disabled")

    def test_settings_and_popups_open_without_errors(self):
        self.app.open_settings()
        self.app.open_recording_settings()
        self.app.open_info()
        self.app.show_message("Test", "message body")
        self.root.update()

    def test_listener_button_says_listening(self):
        self.assertEqual(self.app.listener_btn.cget("text"), "Start Listening")
        self.app.engine.listening = True
        self.app.sync_state()
        self.assertEqual(self.app.listener_btn.cget("text"), "Stop Listening")

    def test_record_button_exists_and_reproduce_is_gone(self):
        self.assertEqual(self.app.record_btn.cget("text"), "Start Recording")
        self.assertFalse(hasattr(self.app, "reproduce_btn"))

    def test_recording_state_updates_the_buttons_and_status(self):
        self.app.engine.recording = True
        self.app.sync_state()
        self.assertEqual(self.app.record_btn.cget("text"), "Stop Recording")
        self.assertEqual(str(self.app.record_btn.cget("state")), "normal")
        self.assertEqual(str(self.app.listener_btn.cget("state")), "normal")
        self.assertEqual(str(self.app.start_btn.cget("state")), "disabled")
        self.assertIn("Recording", self.app.status_label.cget("text"))
        self.app.engine.recording = False
        self.app.sync_state()
        self.assertEqual(self.app.record_btn.cget("text"), "Start Recording")
        self.assertEqual(str(self.app.start_btn.cget("state")), "normal")

    def test_only_a_running_job_disables_recording(self):
        self.app.engine.listening = True
        self.app.sync_state()
        self.assertEqual(str(self.app.record_btn.cget("state")), "normal")
        self.app.engine.listening = False
        self.app.engine.running = True
        self.app.sync_state()
        self.assertEqual(str(self.app.record_btn.cget("state")), "disabled")
        self.app.engine.running = False

    def test_stop_button_trims_the_stop_click(self):
        calls = []
        self.app.engine.recording = True
        self.app.engine.stop_recording = lambda **kwargs: calls.append(kwargs)
        self.app.toggle_recording()
        self.assertEqual(calls, [{"by_click": True}])
        self.app.engine.recording = False

    def test_every_option_has_a_variable_and_is_saved(self):
        self.assertEqual(set(self.app.option_vars), {option.key for option in intool.OPTIONS})
        self.app.save_config()
        saved = intool.read_config(self.config)
        for option in intool.OPTIONS:
            self.assertEqual(saved[option.key], option.clean(None))

    def test_changed_option_is_applied_and_saved(self):
        self.app.option_vars["screen"].set("1280x720")
        self.app.option_vars["record_waits"].set(False)
        self.app.commit_settings()
        saved = intool.read_config(self.config)
        self.assertEqual(saved["screen"], "1280x720")
        self.assertFalse(saved["record_waits"])
        self.assertFalse(self.app.engine.record_options.waits)

    def test_invalid_option_is_rejected_and_not_saved(self):
        self.app.option_vars["screen"].set("huge")
        self.app.commit_settings()
        self.root.update()
        self.assertFalse(os.path.exists(self.config))

    def test_invalid_stored_option_falls_back_to_the_default(self):
        data = {"screen": "huge", "record_min_wait": "soon", "hotkey_order": "any order"}
        intool.write_config(self.config, data)
        self.app.engine.shutdown()
        self.root.update()
        app = intool.App(self.root, self.config)
        self.assertEqual(app.option_vars["screen"].get(), "auto")
        self.assertEqual(app.option_vars["record_min_wait"].get(), "10ms")
        self.assertEqual(app.option_vars["hotkey_order"].get(), "any order")
        app.engine.shutdown()

    def test_delete_config_restores_option_defaults(self):
        self.app.option_vars["move_mode"].set("exact")
        self.app.delete_config()
        self.assertEqual(self.app.option_vars["move_mode"].get(), "auto")

    def test_recorded_text_fills_the_action_and_is_saved(self):
        self.app.apply_recording("pos:1:2, down:mouse0, up:mouse0")
        self.assertEqual(self.app.entries["action"].get(), "pos:1:2, down:mouse0, up:mouse0")
        self.assertEqual(intool.read_config(self.config)["action"], "pos:1:2, down:mouse0, up:mouse0")

    def test_empty_recording_keeps_the_action(self):
        before = self.app.entries["action"].get()
        self.app.apply_recording("")
        self.root.update()
        self.assertEqual(self.app.entries["action"].get(), before)

    def test_immediate_option_is_saved_and_reset(self):
        self.app.immediate_var.set(True)
        self.app.save_config()
        self.assertTrue(intool.read_config(self.config)["immediate"])
        self.assertTrue(intool.job_from_fields(self.app.current_fields(), need_hotkey=False).immediate)
        self.app.delete_config()
        self.assertFalse(self.app.immediate_var.get())

    def test_save_config_writes_hotkeys_and_options(self):
        self.app.save_config()
        saved = intool.read_config(self.config)
        self.assertIn("hotkeys", saved)
        self.assertEqual(saved["state"], self.app.state_selection)
        self.assertEqual(saved["device"], self.app.device_selection)
        self.assertNotIn("hotkey", saved)


if __name__ == "__main__":
    unittest.main()

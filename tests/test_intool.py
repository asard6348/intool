import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import threading
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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


class InjectorTests(unittest.TestCase):
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
        self.assertEqual(json.loads(json.dumps(job.spec()))["state"], "press & release")

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
        self.app.open_info()
        self.app.show_message("Test", "message body")
        self.root.update()

    def test_save_config_writes_hotkeys_and_options(self):
        self.app.save_config()
        saved = intool.read_config(self.config)
        self.assertIn("hotkeys", saved)
        self.assertEqual(saved["state"], self.app.state_selection)
        self.assertEqual(saved["device"], self.app.device_selection)
        self.assertNotIn("hotkey", saved)


if __name__ == "__main__":
    unittest.main()

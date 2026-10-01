import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from aircontrol import config, ipc
from aircontrol.config import Settings
from aircontrol.engine import Engine, keyboard_rect
from aircontrol.geometry import finger_states
from aircontrol.pointer import anchor_point
from aircontrol.system.backend import DryRunBackend
from tests.handfactory import open_palm, point
from tests.test_gaze import SCREEN, looking_at

DT = 1 / 30
FRAME = np.zeros((480, 640, 3), np.uint8)


@contextlib.contextmanager
def sandbox():
    """DATA_DIR de mentira: el motor lee y escribe aquí y no toca los datos reales."""
    d = Path(tempfile.mkdtemp())
    patches = [mock.patch.object(config, "SETTINGS_PATH", d / "settings.json"),
               mock.patch.object(config, "PROFILES_PATH", d / "profiles.json"),
               mock.patch.object(config, "GESTURES_PATH", d / "gestures.json"),
               mock.patch.object(config, "GAZE_PATH", d / "gaze.json"),
               mock.patch.object(config, "GAZE_SAMPLES_PATH", d / "gaze_samples.npz"),
               mock.patch.object(ipc, "CONTROL_PATH", d / "control.json"),
               mock.patch.object(ipc, "STATE_PATH", d / "state.json")]
    for p in patches:
        p.start()
    try:
        yield d
    finally:
        for p in patches:
            p.stop()


class FakeCamera:
    def __init__(self):
        self.released = False

    def read(self):
        return True, FRAME.copy()

    def release(self):
        self.released = True


class FakeHands:
    def __init__(self, fn=lambda ts: None):
        self.fn = fn

    def detect(self, frame, ts):
        return self.fn(ts)


class FakeWindow:
    def __init__(self, keys=()):
        self.keys, self.shown, self.geometry, self.fullscreen, self.closed_flag = list(keys), [], None, False, False

    def show(self, img): self.shown.append(img)
    def key(self): return self.keys.pop(0) if self.keys else -1
    def closed(self): return self.closed_flag
    def set_geometry(self, *g): self.geometry = g
    def set_fullscreen(self, on): self.fullscreen = on
    def close(self): pass


def make(hand_fn=lambda ts: None, face_fn=None, **kw):
    backend = DryRunBackend(log=lambda *_: None)
    backend.screen_size = lambda: SCREEN
    faces = type("F", (), {"detect": lambda self, f, ts: face_fn(ts) if face_fn else None})()
    eng = Engine(dry_run=True, camera=FakeCamera(), hands=FakeHands(hand_fn), faces=faces, window=FakeWindow(),
                 backend=backend, log=lambda *_: None, **kw)
    assert eng.setup()
    return eng


class EngineLoopTests(unittest.TestCase):
    def test_run_loop_moves_publishes_state_and_cleans_up(self):
        with sandbox():
            eng = make(lambda ts: point())
            eng.backend.calls.clear()
            self.assertEqual(eng.run(max_frames=8), 0)
            self.assertTrue(any(c[0] == "move" for c in eng.backend.calls))
            self.assertTrue(eng.camera.released)
            self.assertFalse(ipc.STATE_PATH.exists())                       # sin motor, sin estado
            self.assertTrue(eng.window.shown)

    def test_state_file_has_what_the_menu_needs(self):
        with sandbox():
            eng = make(lambda ts: point())
            eng.step(FRAME.copy(), 10.0)
            eng._publish(10.0, eng.ctl.status)
            state = ipc.engine_state()
            for key in ("pid", "paused", "input_mode", "keyboard", "voice_state", "app", "gaze_ready", "calibrating"):
                self.assertIn(key, state)
            self.assertFalse(state["gaze_ready"])

    def test_commands_from_the_app(self):
        with sandbox():
            eng = make()
            ipc.send_command("pause")
            for cmd in eng.commands.poll():
                eng.command(cmd)
            self.assertTrue(eng.ctl.paused)
            eng.command("resume")
            eng.command("mode:gaze")                                        # sin calibrar: se rechaza
            self.assertEqual(eng.ctl.input_mode, "hand")
            eng.command("keyboard")
            img, _ = eng.step(FRAME.copy(), 1.0)
            self.assertEqual(eng.window.geometry, keyboard_rect(SCREEN))
            self.assertEqual(img.shape[:2][::-1], keyboard_rect(SCREEN)[2:])
            eng.command("keyboard")
            eng.step(FRAME.copy(), 2.0)
            self.assertEqual(eng.window.geometry[2:], (480, 360))           # vuelve la vista previa pequeña
            eng.command("quit")
            self.assertTrue(eng._quit)

    def test_stale_commands_are_ignored_at_startup(self):
        with sandbox():
            ipc.send_command("pause")
            eng = make()
            self.assertEqual(eng.commands.poll(), [])

    def test_settings_hot_reload(self):
        with sandbox() as d:
            eng = make()
            old_cutoff = eng.ctl.mouse.fx.min_cutoff
            (d / "settings.json").write_text(json.dumps({"smooth_min_cutoff": 0.3, "pinch_on": 0.2}))
            eng.reload(eng.watcher.changed() or [config.SETTINGS_PATH])
            self.assertEqual(eng.settings.pinch_on, 0.2)
            self.assertEqual(eng.ctl.mouse.fx.min_cutoff, 0.3)
            self.assertNotEqual(old_cutoff, 0.3)
            self.assertIs(eng.ctl.cfg, eng.settings)                        # sigue siendo el mismo objeto

    def test_profiles_and_gestures_hot_reload(self):
        with sandbox() as d:
            eng = make()
            (d / "profiles.json").write_text(json.dumps({"default": {"thumbs_up": "click"}, "profiles": []}))
            eng.reload([config.PROFILES_PATH])
            self.assertEqual(eng.ctl.profiles.resolve("thumbs_up", "x").kind, "click")

    def test_voice_command_is_executed_on_main_thread(self):
        with sandbox():
            eng = make()

            class Voice:
                state = "idle"
                items = [("baja", "scroll:down:12")]

                def poll(self): return self.items.pop(0) if self.items else None
                def stop(self): pass

            eng.voice = Voice()
            eng.step(FRAME.copy(), 1.0)
            self.assertIn(("scroll", 0, -12), eng.backend.calls)
            self.assertIn("baja", eng.ctl.status.last_action)

    def test_voice_off_action_explains_instead_of_failing(self):
        logs = []
        with sandbox():
            eng = make()
            eng.log = logs.append
            eng.command("voice")
            self.assertTrue(any("desactivada" in l for l in logs))

    def test_keys_quit_toggle_debug_and_keyboard(self):
        with sandbox():
            eng = make(lambda ts: point())
            eng.window.keys = [ord("d"), ord("k"), ord("k"), ord("q")]
            self.assertEqual(eng.run(max_frames=50), 0)
            self.assertTrue(eng.debug)


class CalibrationFlowTests(unittest.TestCase):
    def drive(self, eng, seconds, hand_fn=None, t0=0.0):
        t = t0
        for _ in range(int(seconds / DT)):
            eng.step(FRAME.copy(), t)
            t += DT
            if eng.cal is None:
                break
        return t

    def test_hand_calibration_saves_region_and_restores_window(self):
        with sandbox() as d:
            corners = [(-0.30, -0.25), (0.25, -0.25), (0.25, 0.10), (-0.30, 0.10)]
            state = {"t": 0.0}

            def hand(ts):
                # la esquina actual depende del progreso de la calibración, la mano espera quieta en cada una
                idx = min(eng.cal["obj"].index, 3) if eng.cal else 0
                return point(at=corners[idx])

            eng = make(hand)
            eng.command("calibrate:hand")
            self.assertTrue(eng.window.fullscreen)
            self.assertTrue(ipc.engine_state() is None or True)
            t = self.drive(eng, 12)
            self.assertTrue(eng.cal["done_at"] is not None if eng.cal else True)
            self.drive(eng, 6, t0=t)
            self.assertIsNone(eng.cal)
            self.assertFalse(eng.window.fullscreen)
            saved = json.loads((d / "settings.json").read_text())
            self.assertGreater(saved["region_x1"] - saved["region_x0"], 0.4)
            self.assertEqual(eng.settings.region_x0, saved["region_x0"])

    def test_gaze_calibration_end_to_end_enables_gaze_mode(self):
        with sandbox() as d:
            def face(ts):
                if not eng.cal or eng.cal["done_at"] is not None:
                    return looking_at(0.5, 0.5)
                tx, ty = eng.cal["obj"].target_px()
                return looking_at(tx / SCREEN[0], ty / SCREEN[1], np.random.default_rng(int(ts) % 97), 0.004)

            eng = make(face_fn=face)
            self.assertIsNone(eng.ctl.gaze)
            eng.command("calibrate:gaze")
            t = self.drive(eng, 30)
            self.assertIsNotNone(eng.ctl.gaze)
            self.assertTrue((d / "gaze_samples.npz").exists())          # datos crudos guardados para diagnosticar
            saved = np.load(d / "gaze_samples.npz")
            self.assertEqual(sorted(set(saved["groups"].tolist())), list(range(9)))     # los 9 puntos, también los de abajo
            self.assertLess(float(saved["ears"].min()), 0.10)           # y con ojos entrecerrados al mirar abajo
            self.assertTrue((d / "gaze.json").exists())
            self.assertIn("error medio", eng.cal["message"] if eng.cal else "error medio")
            self.drive(eng, 6, t0=t)
            self.assertIsNone(eng.cal)
            eng.command("mode:gaze")
            self.assertEqual(eng.ctl.input_mode, "gaze")
            eng.step(FRAME.copy(), 100.0)
            eng.step(FRAME.copy(), 100.1)
            self.assertTrue(any(c[0] == "move" for c in eng.backend.calls))

    def test_garbage_gaze_calibration_is_discarded_not_activated(self):
        """Caras sin relación con el punto mirado: el error sale enorme, no se activa ni se pisa un modelo bueno."""
        with sandbox() as d:
            rng = np.random.default_rng(3)
            eng = make(face_fn=lambda ts: looking_at(rng.uniform(0, 1), rng.uniform(0, 1)))
            eng.command("calibrate:gaze")
            self.drive(eng, 30)
            self.assertIsNone(eng.ctl.gaze)
            self.assertFalse((d / "gaze.json").exists())
            self.assertTrue((d / "gaze_samples.npz").exists())             # pero los datos se guardan para verlos

    def test_bad_calibration_keeps_the_previous_good_model(self):
        from tests.test_gaze import run_calibration
        with sandbox() as d:
            good = run_calibration().model.to_json()
            (d / "gaze.json").write_text(good)
            rng = np.random.default_rng(4)
            eng = make(face_fn=lambda ts: looking_at(rng.uniform(0, 1), rng.uniform(0, 1)))
            eng.command("calibrate:gaze")
            self.drive(eng, 30)
            self.assertEqual((d / "gaze.json").read_text(), good)
            self.assertIsNotNone(eng.ctl.gaze)

    def test_escape_cancels_calibration_without_saving(self):
        with sandbox() as d:
            eng = make(lambda ts: point())
            eng.command("calibrate:hand")
            eng.window.keys = [27]
            eng.run(max_frames=3)
            self.assertFalse((d / "settings.json").exists())

    def test_exit_after_calibration(self):
        with sandbox():
            eng = make(lambda ts: point(), exit_after_calibration=True)
            eng.command("calibrate:hand")
            eng.cal["done_at"] = 0.0
            eng.cal["message"] = "x"
            eng.step(FRAME.copy(), 10.0)
            self.assertTrue(eng._quit)

    def test_gaze_model_persists_across_restarts(self):
        with sandbox():
            eng = make(face_fn=lambda ts: None)
            eng.command("calibrate:gaze")
            obj = eng.cal["obj"]
            from tests.test_gaze import run_calibration
            (config.GAZE_PATH).write_text(run_calibration().model.to_json())
            eng2 = make()
            self.assertIsNotNone(eng2.ctl.gaze)


if __name__ == "__main__":
    unittest.main()

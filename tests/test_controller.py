import unittest

from aircontrol.config import Settings
from aircontrol.controller import Controller
from aircontrol.geometry import gesture_features
from aircontrol.gestures import GestureStore
from aircontrol.profiles import Profiles
from aircontrol.system.backend import DryRunBackend
from tests.handfactory import fist, hand, open_palm, peace, point

DT = 1 / 30


class Bed:
    """Controller con backend de mentira y una app en primer plano configurable."""

    def __init__(self, app="Safari", store=None, data=None):
        self.backend = DryRunBackend(log=lambda *_: None)
        self.backend.screen_size = lambda: (1000, 800)
        self.app = app
        self.ctl = Controller(self.backend, Settings(), store or GestureStore(), Profiles(data),
                              log=lambda *_: None, app_provider=lambda: self.app)
        self.t = 0.0

    def feed(self, lm, frames):
        for _ in range(frames):
            self.status = self.ctl.process(lm, self.t)
            self.t += DT
        return self.status

    def swipe(self, dx, frames=8):
        for i in range(frames):
            self.status = self.ctl.process(open_palm(at=(dx * i / frames, 0)), self.t)
            self.t += DT

    @property
    def calls(self):
        return [c for c in self.backend.calls if c[0] != "move"]


class ControllerTests(unittest.TestCase):
    def test_pointing_moves_the_backend_cursor(self):
        bed = Bed()
        bed.feed(point(), 5)
        self.assertTrue(any(c[0] == "move" for c in bed.backend.calls))

    def test_same_swipe_does_different_things_per_app(self):
        safari, vlc, terminal = Bed("Safari"), Bed("VLC"), Bed("Terminal")
        for bed in (safari, vlc, terminal):
            bed.swipe(0.45)
        self.assertEqual(len(safari.calls), 1)
        self.assertEqual(safari.calls[0][0], "key")
        self.assertEqual(vlc.calls, [("key", "right")])
        self.assertEqual(terminal.calls, [])                # sin binding para swipe_right en 'default'

    def test_thumbs_up_uses_profile(self):
        bed = Bed("Terminal")
        bed.feed(hand(thumb_up=True), 30)
        self.assertEqual(bed.calls, [("media", "play_pause")])

    def test_fist_hold_pauses_and_silences_then_resumes(self):
        bed = Bed()
        status = bed.feed(fist(), 50)
        self.assertTrue(status.paused)
        before = len(bed.backend.calls)
        bed.feed(point(at=(0.1, 0)), 10)
        bed.swipe(0.45)
        self.assertEqual(len(bed.backend.calls), before)    # en pausa no pasa nada
        bed.feed(None, 20)
        status = bed.feed(fist(), 50)
        self.assertFalse(status.paused)

    def test_pausing_while_dragging_releases_button(self):
        bed = Bed()
        bed.feed(point(), 8)
        bed.feed(point(pinch="index"), 20)                   # arrastre en curso
        self.assertIn(("press", "left"), bed.calls)
        bed.feed(fist(), 50)
        self.assertEqual(bed.calls[-1], ("release", "left"))

    def test_custom_gesture_triggers_bound_action(self):
        store = GestureStore()
        for i in range(5):
            store.add("tres", gesture_features(hand(index=1, middle=1, ring=1) + i * 1e-4))
        data = {"default": {"tres": "key:mod+c"}, "profiles": []}
        bed = Bed(store=store, data=data)
        bed.feed(hand(index=1, middle=1, ring=1), 30)
        self.assertEqual(len(bed.calls), 1)
        self.assertEqual(bed.calls[0][0], "key")

    def test_custom_gesture_takes_priority_over_pointer(self):
        store = GestureStore()
        for i in range(5):
            store.add("peace", gesture_features(peace() + i * 1e-4))
        bed = Bed(store=store, data={"default": {}, "profiles": []})
        bed.feed(peace(), 30)
        self.assertEqual([c for c in bed.backend.calls if c[0] == "scroll"], [])

    def test_status_reports_app_and_profile(self):
        status = Bed("Google Chrome").feed(point(), 3)
        self.assertEqual((status.app, status.profile), ("Google Chrome", "Navegador"))


if __name__ == "__main__":
    unittest.main()


class RecordingTests(unittest.TestCase):
    def test_roundtrip_and_replay(self):
        import tempfile
        from pathlib import Path
        from aircontrol import recording
        times, hands = [], []
        for i in range(10):
            times.append(i * DT)
            hands.append(point())
        for i in range(4):
            times.append((10 + i) * DT)
            hands.append(point(pinch="index"))
        for i in range(6):
            times.append((14 + i) * DT)
            hands.append(point())
        times.append(20 * DT)
        hands.append(None)
        path = Path(tempfile.mkdtemp()) / "s.npz"
        recording.save(path, times, hands)
        t2, h2 = recording.load(path)
        self.assertEqual(len(t2), 21)
        self.assertIsNone(h2[-1])
        events, summary = recording.replay(t2, h2, Settings())
        self.assertEqual(summary.get("click"), 1)
        self.assertGreater(summary.get("modo_point", 0), 5)
        _, strict = recording.replay(t2, h2, Settings(pinch_on=0.001))
        self.assertNotIn("click", strict)              # cambiar un ajuste cambia el resultado

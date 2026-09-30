import unittest

from aircontrol.handcal import HandCalibration
from aircontrol.keyboard import KeyboardState, suggestions
from tests.handfactory import open_palm, point

DT = 1 / 30


def center(kb, key):
    for k, _, x0, y0, x1, y1 in kb.layout():
        if k == key:
            return (x0 + x1) / 2, (y0 + y1) / 2
    raise KeyError(key)


class KeyboardTests(unittest.TestCase):
    def test_every_key_is_reachable_and_layout_covers_the_board(self):
        for layer in ("letters", "symbols"):
            kb = KeyboardState()
            kb.layer = layer
            for key, _, x0, y0, x1, y1 in kb.layout():
                self.assertEqual(kb.key_at(*center(kb, key)), key)

    def test_pinch_types_the_key_under_the_pointer(self):
        kb = KeyboardState()
        self.assertEqual(kb.update(center(kb, "h"), 0.0, pinch=True), [("type", "h")])
        self.assertEqual(kb.update(center(kb, "space"), 0.1, pinch=True), [("type", " ")])
        self.assertEqual(kb.update(center(kb, "backspace"), 0.2, pinch=True), [("key", "backspace")])

    def test_dwell_types_once_after_dwell_time(self):
        kb, out = KeyboardState(dwell_s=0.9), []
        for i in range(90):
            out += kb.update(center(kb, "a"), i * DT)
        self.assertEqual(out, [("type", "a")])                   # solo una vez aunque siga mirando
        out2 = []
        for i in range(40):
            out2 += kb.update(center(kb, "s"), 3 + i * DT)
        self.assertEqual(out2, [("type", "s")])

    def test_moving_across_keys_types_nothing(self):
        kb, out = KeyboardState(dwell_s=0.9), []
        keys = "qwertyuiop"
        for i in range(60):
            out += kb.update(center(kb, keys[i // 6]), i * DT)
        self.assertEqual(out, [])

    def test_backspace_repeats_while_held(self):
        kb, out = KeyboardState(dwell_s=0.5), []
        for i in range(150):
            out += kb.update(center(kb, "backspace"), i * DT)
        self.assertGreaterEqual(len(out), 4)
        self.assertTrue(all(a == ("key", "backspace") for a in out))

    def test_shift_is_one_shot_and_uppercases(self):
        kb = KeyboardState()
        kb.press("shift")
        self.assertEqual(kb.press("h"), [("type", "H")])
        self.assertEqual(kb.press("o"), [("type", "o")])

    def test_layers_and_close(self):
        kb = KeyboardState()
        kb.press("symbols")
        self.assertEqual(kb.key_at(*center(kb, "1")), "1")
        kb.press("letters")
        self.assertEqual(kb.press("close"), [("close",)])

    def test_suggestions_complete_the_word(self):
        kb = KeyboardState()
        for ch in "hol":
            kb.press(ch)
        self.assertIn("hola", suggestions(kb.word))
        key = "suggest:hola"
        self.assertEqual(kb.press(key), [("type", "a ")])        # solo lo que falta + espacio
        self.assertEqual(kb.word, "")

    def test_pointer_outside_does_nothing(self):
        kb = KeyboardState()
        self.assertEqual(kb.update(None, 0.0, pinch=True), [])
        self.assertEqual(kb.update((1.5, 0.5), 0.1, pinch=True), [])


class HandCalibrationTests(unittest.TestCase):
    def run_corner(self, cal, t, at, frames=50):
        for _ in range(frames):
            cal.update(point(at=at), t)
            t += DT
        return t

    def test_four_corners_define_the_region(self):
        cal, t = HandCalibration(hold_s=1.0), 0.0
        for at in ((-0.30, -0.25), (0.25, -0.25), (0.25, 0.10), (-0.30, 0.10)):
            t = self.run_corner(cal, t, at)
        self.assertTrue(cal.done)
        self.assertIsNone(cal.error)
        r = cal.region
        self.assertLess(r["region_x0"], r["region_x1"])
        self.assertLess(r["region_y0"], r["region_y1"])
        self.assertGreater(r["region_x1"] - r["region_x0"], 0.4)

    def test_moving_finger_does_not_register(self):
        cal, t = HandCalibration(hold_s=1.0), 0.0
        for i in range(90):
            cal.update(point(at=(0.01 * i, 0)), t)
            t += DT
        self.assertEqual(cal.index, 0)

    def test_hand_not_pointing_resets_progress(self):
        cal, t = HandCalibration(hold_s=1.0), 0.0
        for i in range(25):
            cal.update(point(), t)
            t += DT
        cal.update(open_palm(), t)
        self.assertEqual(cal.progress(t), 0.0)

    def test_tiny_region_is_rejected(self):
        cal, t = HandCalibration(hold_s=0.5), 0.0
        for at in ((0, 0), (0.02, 0), (0.02, 0.02), (0, 0.02)):
            t = self.run_corner(cal, t, at, frames=25)
        self.assertIsNotNone(cal.error)
        self.assertIsNone(cal.region)


if __name__ == "__main__":
    unittest.main()

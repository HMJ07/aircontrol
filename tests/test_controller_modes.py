import unittest

from aircontrol.config import Settings
from aircontrol.controller import Controller
from aircontrol.gestures import GestureStore
from aircontrol.profiles import Profiles
from aircontrol.system.backend import DryRunBackend
from tests.handfactory import point
from tests.test_gaze import SCREEN, face, looking_at, run_calibration
from tests.test_keyboard_handcal import center

DT = 1 / 30


class Bed:
    def __init__(self, settings=None, gaze=True):
        self.backend = DryRunBackend(log=lambda *_: None)
        self.backend.screen_size = lambda: SCREEN
        self.model = run_calibration().model if gaze else None
        self.logs = []
        self.ctl = Controller(self.backend, settings or Settings(), GestureStore(), Profiles(), log=self.logs.append,
                              app_provider=lambda: "Terminal", gaze_model=self.model)
        self.t = 0.0

    def feed(self, lm=None, fc=None, frames=1):
        for _ in range(frames):
            self.status = self.ctl.process(lm, self.t, fc)
            self.t += DT
        return self.status

    def calls(self, *kinds):
        return [c for c in self.backend.calls if not kinds or c[0] in kinds]


class ModeTests(unittest.TestCase):
    def test_gaze_mode_moves_cursor_with_face_and_ignores_hand_pointing(self):
        bed = Bed()
        self.assertTrue(bed.ctl.set_mode("gaze"))
        bed.feed(point(), looking_at(0.2, 0.3), 20)
        moves = bed.calls("move")
        self.assertTrue(moves)
        self.assertLess(abs(moves[-1][1] - 0.2 * SCREEN[0]), 120)            # sigue la mirada, no la mano

    def test_gaze_needs_calibration(self):
        bed = Bed(gaze=False)
        self.assertFalse(bed.ctl.set_mode("gaze"))
        self.assertEqual(bed.ctl.input_mode, "hand")
        bed.feed(point(), None, 5)
        self.assertTrue(bed.calls("move"))                                   # sigue funcionando con la mano

    def test_gaze_dwell_click_through_controller(self):
        bed = Bed()
        bed.ctl.set_mode("gaze")
        status = bed.feed(None, looking_at(0.5, 0.5), 15)
        self.assertGreater(bed.feed(None, looking_at(0.5, 0.5), 15).dwell, 0.0)
        bed.feed(None, looking_at(0.5, 0.5), 40)
        self.assertEqual(len(bed.calls("click")), 1)

    def test_pinch_click_with_gaze_pointer(self):
        bed = Bed(Settings(gaze_click="pinch"))
        bed.ctl.set_mode("gaze")
        bed.feed(point(), looking_at(0.5, 0.5), 30)
        self.assertEqual(bed.calls("click"), [])                             # mirar no hace clic
        bed.feed(point(pinch="index"), looking_at(0.5, 0.5), 5)
        bed.feed(point(), looking_at(0.5, 0.5), 5)
        self.assertEqual(bed.calls("click"), [("click", "left", 1)])

    def test_mode_action_toggles(self):
        bed = Bed()
        bed.ctl._control("mode", "toggle")
        self.assertEqual(bed.ctl.input_mode, "gaze")
        bed.ctl._control("mode", "toggle")
        self.assertEqual(bed.ctl.input_mode, "hand")

    def test_voice_action_calls_callback(self):
        bed = Bed()
        called = []
        bed.ctl.on_voice = lambda: called.append(1)
        bed.ctl._control("voice")
        self.assertEqual(called, [1])


class KeyboardModeTests(unittest.TestCase):
    def test_hand_keyboard_types_with_pinch_and_does_not_move_system_cursor(self):
        bed = Bed()
        bed.ctl.set_keyboard(True)
        kb = bed.ctl.keyboard
        u, v = center(kb, "h")
        # mano apuntando al centro de la tecla 'h': se mueve la mano hasta que el cursor virtual cae encima
        # (la región de la mano cubre el teclado entero, como si fuera la pantalla)
        import numpy as np
        from aircontrol.pointer import anchor_point
        cfg = bed.ctl.cfg
        base = anchor_point(point())
        at = (cfg.region_x0 + u * (cfg.region_x1 - cfg.region_x0) - base[0],
              cfg.region_y0 + v * (cfg.region_y1 - cfg.region_y0) - base[1])
        bed.feed(point(at=at), None, 20)
        bed.feed(point(at=at, pinch="index"), None, 4)
        bed.feed(point(at=at), None, 5)
        self.assertEqual(bed.calls("text"), [("text", "'h'")])
        self.assertEqual(bed.calls("move", "click"), [])                     # nada llega al ratón del sistema

    def test_gaze_keyboard_types_by_dwell_using_keyboard_rect(self):
        bed = Bed(Settings(keyboard_dwell_s=0.6))
        bed.ctl.set_mode("gaze")
        bed.ctl.keyboard_rect = (0, int(SCREEN[1] * 0.6), SCREEN[0], int(SCREEN[1] * 0.4))
        bed.ctl.set_keyboard(True)
        # mirar a la tecla 'space' (abajo del todo): u,v del teclado -> píxel de pantalla -> mirada sintética
        u, v = center(bed.ctl.keyboard, "space")
        x, y, w, h = bed.ctl.keyboard_rect
        gx, gy = (x + u * w) / SCREEN[0], (y + v * h) / SCREEN[1]
        bed.feed(None, looking_at(gx, gy), 60)
        self.assertIn(("text", "' '"), bed.calls("text"))
        self.assertEqual(bed.calls("click"), [])                              # la permanencia del ratón no dispara

    def test_close_key_closes_keyboard(self):
        bed = Bed()
        bed.ctl.set_keyboard(True)
        self.assertEqual(bed.ctl.keyboard.press("close"), [("close",)])
        bed.ctl.set_keyboard(False)
        self.assertIsNone(bed.ctl.keyboard)
        self.assertFalse(bed.feed(point(), None, 1).keyboard)

    def test_keyboard_action_toggles(self):
        bed = Bed()
        bed.ctl._control("keyboard")
        self.assertTrue(bed.feed(None, None, 1).keyboard)
        bed.ctl._control("keyboard")
        self.assertFalse(bed.feed(None, None, 1).keyboard)


if __name__ == "__main__":
    unittest.main()

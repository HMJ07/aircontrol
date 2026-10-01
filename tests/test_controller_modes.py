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

    def test_opening_the_keyboard_returns_focus_to_the_app_you_type_in(self):
        bed = Bed()
        bed.ctl.set_keyboard(True)
        self.assertIn(("focus", "Terminal"), bed.backend.calls)       # app_provider de Bed devuelve "Terminal"
        self.assertEqual(bed.ctl.keyboard.target, "Terminal")
        before = len(bed.backend.calls)
        bed.ctl.set_keyboard(False)
        self.assertEqual(len(bed.backend.calls), before)              # al cerrar no se toca el foco

    def test_no_focus_call_when_target_is_unknown(self):
        bed = Bed()
        bed.ctl.app_provider = lambda: ""
        bed.ctl.set_keyboard(True)
        self.assertFalse([c for c in bed.backend.calls if c[0] == "focus"])

    def test_pinky_up_opens_the_keyboard_after_hold_but_does_not_close_it(self):
        from tests.handfactory import hand
        bed = Bed()
        bed.feed(hand(pinky=1), None, 15)                              # 0,5 s: aún no
        self.assertFalse(bed.status.keyboard)
        bed.feed(hand(pinky=1), None, 20)                              # >0,8 s
        self.assertTrue(bed.status.keyboard)
        bed.feed(None, None, 60)                                       # soltar y esperar al respiro
        bed.feed(hand(pinky=1), None, 40)
        self.assertTrue(bed.status.keyboard)                           # un 🤙 accidental al teclear NO lo cierra
        bed.ctl._control("keyboard")                                   # menú / voz / tecla k / ✕ sí
        self.assertFalse(bed.feed(None, None, 1).keyboard)

    def test_gestures_do_nothing_while_the_keyboard_is_open(self):
        from tests.handfactory import hand, open_palm
        bed = Bed()
        bed.ctl.set_keyboard(True)
        before = len(bed.backend.calls)
        bed.feed(hand(thumb_up=True), None, 40)                        # 👍 mantenido (en Safari iba a la barra de URL)
        for i in range(8):                                             # deslizar la palma (en Safari: atrás/adelante)
            bed.status = bed.ctl.process(open_palm(at=(0.06 * i, 0)), bed.t)
            bed.t += DT
        self.assertEqual([c for c in bed.backend.calls[before:] if c[0] in ("key", "media", "scroll")], [])
        self.assertTrue(bed.status.keyboard)

    def test_fist_still_pauses_with_the_keyboard_open(self):
        from tests.handfactory import fist
        bed = Bed()
        bed.ctl.set_keyboard(True)
        self.assertTrue(bed.feed(fist(), None, 50).paused)

    def test_holding_a_pinch_or_drag_never_counts_as_the_pause_fist(self):
        """Regresión: un arrastre largo (índice curvado + resto plegado) parecía un puño y pausaba/reanudaba el control."""
        from tests.handfactory import hand, point
        bed = Bed()
        bed.feed(point(), None, 10)
        curled_pinch = hand(index=0, pinch="index")                    # índice curvado tocando el pulgar, resto plegado
        status = bed.feed(curled_pinch, None, 90)                      # 3 s: mucho más que el puño (1,2 s)
        self.assertFalse(status.paused)
        self.assertIn(status.mode, ("drag", "pinch"))

    def test_pinky_up_does_not_move_the_cursor_or_click(self):
        from tests.handfactory import hand
        bed = Bed()
        bed.feed(hand(pinky=1), None, 40)
        self.assertEqual(bed.calls("move", "click", "press"), [])

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

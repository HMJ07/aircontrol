import unittest

from aircontrol.config import Settings
from aircontrol.pointer import AirMouse
from tests.handfactory import hand, open_palm, peace, point

SCREEN = (1000, 800)


def run(mouse, frames, t0=0.0, dt=1 / 30):
    """Reproduce (mano, n_fotogramas) a 30 fps y devuelve todos los eventos."""
    events, t = [], t0
    for lm, n in frames:
        for _ in range(n):
            events += mouse.update(lm, t)
            t += dt
    return events, t


def kinds(events):
    return [e[0] for e in events]


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.mouse = AirMouse(Settings(), SCREEN)

    def test_index_moves_cursor_across_screen(self):
        left, _ = run(self.mouse, [(point(at=(-0.25, 0)), 5)])
        right, _ = run(AirMouse(Settings(), SCREEN), [(point(at=(0.45, 0)), 5)])
        self.assertEqual(kinds(left)[0], "move")
        self.assertLess(left[-1][1], 100)                   # extremo izquierdo de la pantalla
        self.assertGreater(right[-1][1], SCREEN[0] - 100)   # extremo derecho

    def test_open_palm_and_fist_do_nothing(self):
        events, _ = run(self.mouse, [(open_palm(), 10)])
        self.assertEqual(events, [])

    def test_quick_pinch_is_single_click_without_moving(self):
        events, _ = run(self.mouse, [(point(), 10), (point(pinch="index"), 4), (point(), 6)])
        clicks = [e for e in events if e[0] == "click"]
        self.assertEqual(clicks, [("click", "left", 1)])
        self.assertNotIn("press", kinds(events))

    def test_two_quick_pinches_make_double_click(self):
        pinch, rest = point(pinch="index"), point()
        events, _ = run(self.mouse, [(rest, 8), (pinch, 4), (rest, 4), (pinch, 4), (rest, 4)])
        self.assertEqual([e for e in events if e[0] == "click"], [("click", "left", 1), ("click", "left", 2)])

    def test_holding_pinch_starts_drag_and_release_ends_it(self):
        events, _ = run(self.mouse, [(point(), 8), (point(pinch="index"), 25), (point(), 5)])
        ks = kinds(events)
        self.assertIn("press", ks)
        self.assertEqual(ks[-1], "release")
        self.assertNotIn("click", ks)

    def test_moving_while_pinched_drags(self):
        frames = [(point(), 8)] + [(point(pinch="index", at=(0.012 * i, 0)), 1) for i in range(12)] + [(point(), 5)]
        events, _ = run(self.mouse, frames)
        ks = kinds(events)
        self.assertLess(ks.index("press"), len(ks) - 1)
        self.assertIn("move", ks[ks.index("press"):])
        self.assertIn("release", ks)
        self.assertLess(ks.index("press"), ks.index("release"))

    def test_middle_pinch_is_right_click(self):
        events, _ = run(self.mouse, [(peace(), 8), (peace(pinch="middle"), 4), (peace(), 6)])
        self.assertIn(("click", "right", 1), events)

    def test_half_extended_middle_finger_keeps_pointing_and_never_scrolls(self):
        lm = point()
        lm[12] = (0.50, 0.50, 0)                   # corazón a medias: cuenta como "extendido" pero no claramente
        events, _ = run(self.mouse, [(point(), 5), (lm, 30)])
        self.assertEqual([e for e in events if e[0] in ("scroll", "click", "press")], [])
        self.assertEqual(self.mouse.mode, "point")                          # el cursor SIGUE moviéndose
        moved, _ = run(self.mouse, [(lm.copy() + [0.1, 0, 0], 10)], t0=2.0)
        self.assertTrue(any(e[0] == "move" for e in moved))

    def test_pinch_works_when_index_curls_and_other_fingers_relax_like_an_ok_sign(self):
        ok_sign = hand(index=0, middle=1, ring=1, pinky=1, pinch="index")   # índice curvo tocando el pulgar; resto abierto
        events, _ = run(self.mouse, [(point(), 8), (ok_sign, 4), (point(), 6)])
        self.assertIn(("click", "left", 1), events)

    def test_ok_sign_without_having_pointed_does_not_click(self):
        ok_sign = hand(index=0, middle=1, ring=1, pinky=1, pinch="index")
        events, _ = run(self.mouse, [(open_palm(), 30), (ok_sign, 5), (open_palm(), 5)])
        self.assertEqual([e for e in events if e[0] in ("click", "press")], [])

    def test_realistic_pinch_is_a_click_at_the_pre_curl_position_not_a_drag(self):
        """El índice se curva ~3 cm y el pulgar se acerca poco a poco: es un clic, en el sitio que se señalaba."""
        steps = []
        for i in range(8):
            lm = point()
            lm[8] = (0.44, 0.30 + 0.025 * i, 0)                     # la punta baja 0,18 del alto de imagen
            k = 1 - i / 7
            lm[4] = (0.44 + 0.25 * k + 0.01, lm[8][1] + 0.02 + 0.12 * k, 0)
            steps.append((lm, 1))
        events, t = run(self.mouse, [(point(), 15)])
        before = self.mouse.cursor
        more, t = run(self.mouse, steps + [(steps[-1][0], 3), (point(), 6)], t0=t)
        self.assertEqual([e for e in more if e[0] == "click"], [("click", "left", 1)])
        self.assertEqual([e for e in more if e[0] in ("press", "release")], [])
        click_at = [e for e in more if e[0] == "move"]
        landed = click_at[0][1:] if click_at else before                       # primer movimiento tras empezar el pellizco
        self.assertLess(abs(landed[0] - before[0]) + abs(landed[1] - before[1]), 25)

    def test_one_noisy_frame_does_not_release_a_held_pinch(self):
        pinch, rest = point(pinch="index"), point()
        frames = [(point(), 8), (pinch, 12), (rest, 1), (pinch, 12), (rest, 5)]      # un fotograma abierto en mitad
        events, _ = run(self.mouse, frames)
        ks = [e[0] for e in events]
        self.assertEqual(ks.count("press"), 1)
        self.assertEqual(ks.count("release"), 1)                                   # un solo arrastre, no dos
        self.assertEqual(ks.count("click"), 0)

    def test_two_open_frames_do_release(self):
        pinch, rest = point(pinch="index"), point()
        events, _ = run(self.mouse, [(point(), 8), (pinch, 4), (rest, 2), (rest, 6)])
        self.assertIn(("click", "left", 1), events)

    def test_pinch_latch_expires(self):
        ok_sign = hand(index=0, middle=1, ring=1, pinky=1, pinch="index")
        events, _ = run(self.mouse, [(point(), 5), (open_palm(), 20), (ok_sign, 5)])     # 0,7 s después de apuntar
        self.assertEqual([e for e in events if e[0] == "click"], [])

    def test_brief_peace_flash_does_not_scroll(self):
        events, _ = run(self.mouse, [(point(), 5), (peace(at=(0, 0.0)), 3), (point(), 5)])
        self.assertEqual([e for e in events if e[0] == "scroll"], [])

    def test_fist_with_thumb_over_fingers_never_clicks(self):
        from tests.handfactory import fist
        events, _ = run(self.mouse, [(fist(pinch="index"), 10), (fist(), 5)])
        self.assertEqual(events, [])

    def test_peace_sign_scrolls_with_hand_direction(self):
        down = [(peace(at=(0, 0.01 * i)), 1) for i in range(15)]
        up = [(peace(at=(0, -0.01 * i)), 1) for i in range(15)]
        ev_down, _ = run(AirMouse(Settings(), SCREEN), down)
        ev_up, _ = run(AirMouse(Settings(), SCREEN), up)
        self.assertTrue(ev_down and ev_up)
        self.assertTrue(all(e[0] == "scroll" for e in ev_down + ev_up))   # el cursor no se mueve
        self.assertGreater(sum(e[2] for e in ev_down), 0)
        self.assertLess(sum(e[2] for e in ev_up), 0)

    def test_losing_the_hand_releases_a_drag(self):
        events, t = run(self.mouse, [(point(), 8), (point(pinch="index"), 25)])
        events += self.mouse.update(None, t + 1.0)
        self.assertEqual(events[-1], ("release", "left"))

    def test_short_tracking_glitch_does_not_cancel_click(self):
        rest, pinch = point(), point(pinch="index")
        events, t = run(self.mouse, [(rest, 8), (pinch, 2)])
        events += self.mouse.update(None, t)                # un fotograma sin mano
        more, _ = run(self.mouse, [(rest, 3)], t0=t + 1 / 30)
        self.assertIn(("click", "left", 1), events + more)

    def test_pause_releases_and_silences(self):
        events, t = run(self.mouse, [(point(), 8), (point(pinch="index"), 25)])
        events += self.mouse.update(point(), t, active=False)
        self.assertEqual(events[-1], ("release", "left"))
        self.assertEqual(self.mouse.update(point(), t + 0.1, active=False), [])


if __name__ == "__main__":
    unittest.main()

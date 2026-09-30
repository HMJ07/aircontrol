import unittest

import numpy as np

from aircontrol.config import Settings
from aircontrol.gaze import (BlinkDetector, DwellClicker, GazeCalibration, GazeModel, GazePointer,
                             IRIS_L, IRIS_R, EYE_L, EYE_R, NOSE, CHIN, FOREHEAD, CHEEK_L, CHEEK_R, extract_features)

SCREEN = (1710, 1107)


def face(h=0.0, v=0.0, yaw=0.0, pitch=0.0, pos=(0.5, 0.5), eye_open=0.30):
    """Cara sintética: iris desplazado (h, v) en tamaños de ojo, cabeza girada y ojos con apertura `eye_open`."""
    class Face(np.ndarray):
        def __setitem__(self, i, v):                    # permite asignar (x, y) sin la z
            super().__setitem__(i, (*v, 0.0) if np.ndim(i) == 0 and len(v) == 2 else v)
    f = np.zeros((478, 3), dtype=np.float32).view(Face)
    cx, cy = pos
    fw, fh, ew = 0.30, 0.42, 0.06                       # ancho y alto de cara, ancho de ojo (normalizado)
    f[CHEEK_L], f[CHEEK_R] = (cx - fw / 2, cy), (cx + fw / 2, cy)
    f[FOREHEAD], f[CHIN] = (cx, cy - fh / 2), (cx, cy + fh / 2)
    f[NOSE] = (cx + yaw * fw, cy + pitch * fh)
    for eye, iris, ex in ((EYE_L, IRIS_L, cx - 0.07), (EYE_R, IRIS_R, cx + 0.07)):
        f[eye["left"]], f[eye["right"]] = (ex - ew / 2, cy - 0.05), (ex + ew / 2, cy - 0.05)
        f[eye["up"]], f[eye["down"]] = (ex, cy - 0.05 - eye_open * ew / 2), (ex, cy - 0.05 + eye_open * ew / 2)
        f[iris] = (ex + h * ew, cy - 0.05 + v * ew)
    return f


def looking_at(sx, sy, rng=None, noise=0.0):
    """Cara de alguien que mira al punto (sx, sy) 0..1: iris y cabeza siguen la mirada con ruido."""
    n = (lambda: rng.normal(0, noise)) if rng is not None else (lambda: 0.0)
    return face(h=(sx - 0.5) * 0.5 + n(), v=(sy - 0.5) * 0.3 + n(), yaw=(sx - 0.5) * 0.08 + n() * 0.2,
                pitch=(sy - 0.5) * 0.06 + n() * 0.2, pos=(0.5 + (sx - 0.5) * 0.04 + n() * 0.3, 0.5 + (sy - 0.5) * 0.03 + n() * 0.3))


def run_calibration(noise=0.004, seed=0):
    rng = np.random.default_rng(seed)
    cal, t = GazeCalibration(SCREEN), 0.0
    while not cal.done:
        sx, sy = cal.target_px()
        feats, ear = extract_features(looking_at(sx / SCREEN[0], sy / SCREEN[1], rng, noise))
        cal.update(feats, ear, t)
        t += 1 / 30
    return cal


class FeatureTests(unittest.TestCase):
    def test_iris_offset_shows_in_features(self):
        base, _ = extract_features(face())
        right, _ = extract_features(face(h=0.2))
        down, _ = extract_features(face(v=0.15))
        self.assertAlmostEqual(base[0], 0.0, places=6)
        self.assertGreater(right[0], 0.15)
        self.assertGreater(down[1], 0.1)

    def test_head_turn_and_position_show_in_features(self):
        turned, _ = extract_features(face(yaw=0.1))
        moved, _ = extract_features(face(pos=(0.6, 0.4)))
        self.assertGreater(turned[2], 0.05)
        self.assertAlmostEqual(moved[4], 0.6, places=5)

    def test_ear_drops_when_eyes_close(self):
        _, open_ear = extract_features(face(eye_open=0.30))
        _, closed_ear = extract_features(face(eye_open=0.05))
        self.assertLess(closed_ear, open_ear * 0.3)


class CalibrationTests(unittest.TestCase):
    def test_calibration_recovers_mapping(self):
        cal = run_calibration()
        self.assertIsNone(cal.error)
        self.assertLess(cal.model.rms, 60)                       # px (con ruido sintético pequeño)
        rng = np.random.default_rng(5)
        errs = []
        for _ in range(40):
            sx, sy = rng.uniform(0.1, 0.9, 2)
            feats, _ = extract_features(looking_at(sx, sy, rng, 0.004))
            px = cal.model.predict(feats)
            errs.append(np.hypot(px[0] - sx * SCREEN[0], px[1] - sy * SCREEN[1]))
        self.assertLess(np.mean(errs), 90)

    def test_noisier_data_reports_worse_rms(self):
        self.assertGreater(run_calibration(noise=0.02).model.rms, run_calibration(noise=0.002).model.rms)

    def test_no_face_gives_error_not_crash(self):
        cal, t = GazeCalibration(SCREEN), 0.0
        while not cal.done:
            cal.update(None, None, t)
            t += 1 / 30
        self.assertIsNotNone(cal.error)
        self.assertIsNone(cal.model)

    def test_closed_eyes_are_not_sampled(self):
        cal = GazeCalibration(SCREEN, points=[(0.5, 0.5)])
        feats, ear = extract_features(face())
        for i in range(60):
            cal.update(feats, ear, i / 30, eyes_open=False)
        self.assertEqual(len(cal.feats), 0)

    def test_model_json_roundtrip(self):
        model = run_calibration().model
        again = GazeModel.from_json(model.to_json())
        feats, _ = extract_features(looking_at(0.3, 0.6))
        self.assertEqual(model.predict(feats), again.predict(feats))

    def test_predictions_are_clamped_to_screen(self):
        model = run_calibration().model
        x, y = model.predict(extract_features(face(h=5, v=5))[0])
        self.assertTrue(0 <= x <= SCREEN[0] - 1 and 0 <= y <= SCREEN[1] - 1)


class DwellTests(unittest.TestCase):
    def test_clicks_after_dwell_then_needs_to_leave(self):
        d, fired = DwellClicker(1.0, 50), []
        for i in range(120):
            fired.append(d.update((100 + (i % 3), 100), i / 30))
        self.assertEqual(sum(fired), 1)                          # una sola vez aunque siga mirando
        fired2 = [d.update((400, 400), 4 + i / 30) for i in range(40)]
        self.assertEqual(sum(fired2), 1)                         # al moverse a otro sitio, vuelve a armarse

    def test_moving_resets(self):
        d = DwellClicker(1.0, 50)
        out = [d.update((i * 20, 0), i / 30) for i in range(90)]
        self.assertEqual(sum(out), 0)

    def test_progress(self):
        d = DwellClicker(1.0, 50)
        d.update((10, 10), 0.0)
        d.update((10, 10), 0.5)
        self.assertAlmostEqual(d.progress(0.5), 0.5, places=2)


class BlinkTests(unittest.TestCase):
    def feed(self, closed_for):
        b, fired, t = BlinkDetector(), [], 0.0
        for _ in range(30):
            fired.append(b.update(0.30, t))
            t += 1 / 30
        for _ in range(int(closed_for * 30)):
            fired.append(b.update(0.05, t))
            t += 1 / 30
        for _ in range(10):
            fired.append(b.update(0.30, t))
            t += 1 / 30
        return sum(fired)

    def test_normal_blink_ignored_long_blink_clicks(self):
        self.assertEqual(self.feed(0.15), 0)
        self.assertEqual(self.feed(0.9), 1)

    def test_eyes_shut_too_long_is_not_a_click(self):
        self.assertEqual(self.feed(4.0), 0)


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.model = run_calibration().model

    def drive(self, face_fn, seconds, settings=None, t0=0.0):
        gp, events = GazePointer(self.model, settings or Settings()), []
        for i in range(int(seconds * 30)):
            events += gp.update(face_fn(i), t0 + i / 30)
        return gp, events

    def test_cursor_follows_gaze_and_dwell_clicks(self):
        gp, events = self.drive(lambda i: looking_at(0.25, 0.75), 2.5)
        x, y = gp.cursor
        self.assertLess(abs(x - 0.25 * SCREEN[0]), 120)
        self.assertLess(abs(y - 0.75 * SCREEN[1]), 120)
        self.assertEqual(sum(1 for e in events if e[0] == "click"), 1)

    def test_blink_click_mode_does_not_dwell(self):
        s = Settings(gaze_click="blink")
        gp, events = self.drive(lambda i: looking_at(0.5, 0.5), 3, s)
        self.assertEqual(sum(1 for e in events if e[0] == "click"), 0)

    def test_blink_click_mode_clicks_on_long_blink(self):
        s = Settings(gaze_click="blink")
        def f(i):
            return looking_at(0.5, 0.5) if not 40 <= i < 70 else face(eye_open=0.04)
        gp, events = self.drive(f, 3, s)
        self.assertEqual(sum(1 for e in events if e[0] == "click"), 1)

    def test_cursor_frozen_while_eyes_close(self):
        s = Settings(gaze_click="off")
        seq = lambda i: looking_at(0.3, 0.3) if i < 40 else face(h=0.4, v=0.3, eye_open=0.03)
        gp, events = self.drive(seq, 2, s)
        x, y = gp.cursor
        self.assertLess(abs(x - 0.3 * SCREEN[0]), 150)           # no salta al mirar "hacia abajo" al cerrar

    def test_no_face_or_inactive_emits_nothing(self):
        gp = GazePointer(self.model, Settings())
        self.assertEqual(gp.update(None, 0.0), [])
        self.assertEqual(gp.update(looking_at(0.5, 0.5), 0.1, active=False), [])


if __name__ == "__main__":
    unittest.main()

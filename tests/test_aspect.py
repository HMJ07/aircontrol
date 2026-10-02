"""Los landmarks son coordenadas normalizadas al ancho y al alto del fotograma. Una cámara 16:9 (muchos portátiles con
Windows) comprime el eje vertical respecto a 4:3 y los umbrales de gestos, afinados con 4:3, se descalibraban."""
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

from aircontrol import geometry, recording
from aircontrol.config import Settings
from aircontrol.geometry import (dist, finger_extension, finger_states, gesture_features, hand_size, pinch_ratio,
                                 set_aspect)
from tests.handfactory import hand, peace, point

SIZES = {"4:3": (640, 480), "16:9": (640, 360), "16:10": (640, 400)}


def seen_by_camera(lm, size):
    """La MISMA mano física vista por un fotograma de `size` píxeles (ancho, alto). `lm` está en 'unidades de 4:3'."""
    phys = np.array(lm, dtype=np.float32)
    phys[:, 0] *= 640 * 0.7                       # píxeles físicos de la escena (reducida para caber en 16:9)
    phys[:, 1] *= 480 * 0.7
    out = phys.copy()
    out[:, 0] /= size[0]
    out[:, 1] /= size[1]
    return out


class AspectTests(unittest.TestCase):
    def tearDown(self):
        set_aspect(640, 480)

    def measure(self, lm, size):
        set_aspect(*size)
        cam = seen_by_camera(lm, size)
        return {"pinch_i": pinch_ratio(cam, 8), "pinch_m": pinch_ratio(cam, 12), "hand": hand_size(cam),
                "ext": finger_extension(cam), "states": finger_states(cam), "features": gesture_features(cam)}

    def test_same_physical_hand_gives_same_measures_in_any_frame_shape(self):
        for lm in (point(pinch="index"), peace(pinch="middle"), point(), hand(thumb=1, index=1, pinky=1)):
            ref = self.measure(lm, SIZES["4:3"])
            for name, size in SIZES.items():
                got = self.measure(lm, size)
                self.assertAlmostEqual(got["pinch_i"], ref["pinch_i"], places=5, msg=name)
                self.assertAlmostEqual(got["pinch_m"], ref["pinch_m"], places=5, msg=name)
                np.testing.assert_allclose(got["ext"], ref["ext"], atol=1e-5, err_msg=name)
                self.assertEqual(got["states"], ref["states"], name)
                np.testing.assert_allclose(got["features"], ref["features"], atol=1e-4, err_msg=name)

    def test_the_correction_is_needed_a_horizontal_pinch_gap_read_25_percent_tighter_in_16_9(self):
        lm = point()
        lm[4] = (lm[8][0] + 0.12, lm[8][1], 0)                       # pulgar separado en horizontal de la punta del índice
        raw = {}
        for name in ("4:3", "16:9"):
            cam = seen_by_camera(lm, SIZES[name])
            raw[name] = dist(cam[4], cam[8]) / dist(cam[0], cam[9])   # lo que se medía antes: sin corregir
        self.assertAlmostEqual(raw["16:9"] / raw["4:3"], 0.75, places=2)     # el defecto original
        fixed = {name: self.measure(lm, SIZES[name])["pinch_i"] for name in ("4:3", "16:9")}
        self.assertAlmostEqual(fixed["16:9"], fixed["4:3"], places=5)         # ya no depende de la forma del fotograma

    def test_4_3_is_unchanged_and_bad_sizes_are_ignored(self):
        set_aspect(640, 480)
        self.assertEqual(geometry.aspect_scale(), 1.0)
        set_aspect(0, 480)
        set_aspect(640, 0)
        set_aspect(-1, -1)
        self.assertEqual(geometry.aspect_scale(), 1.0)
        set_aspect(1280, 720)
        self.assertAlmostEqual(geometry.aspect_scale(), (16 / 9) / (4 / 3))

    def test_screen_pixel_distances_are_never_aspect_corrected(self):
        set_aspect(1280, 720)
        self.assertEqual(dist((0, 0), (30, 40)), 50.0)               # doble clic y zona muerta están en píxeles

    def test_hand_tracker_sets_the_frame_shape_on_every_detection(self):
        from aircontrol.hands import HandTracker
        tracker = HandTracker.__new__(HandTracker)
        tracker._last_ts = -1
        tracker.detector = NS(detect_for_video=lambda img, ts: NS(hand_landmarks=[]))
        set_aspect(640, 480)
        tracker.detect(np.zeros((360, 640, 3), np.uint8), 0)
        self.assertAlmostEqual(geometry.aspect_scale(), (16 / 9) / (4 / 3))
        tracker.detect(np.zeros((480, 640, 3), np.uint8), 40)
        self.assertEqual(geometry.aspect_scale(), 1.0)


class RecordingAspectTests(unittest.TestCase):
    def tearDown(self):
        set_aspect(640, 480)

    def make(self, size):
        times, hands = [], []
        steps = [(point(), 10), (point(pinch="index"), 4), (point(), 6)]
        t = 0.0
        for lm, n in steps:
            for _ in range(n):
                times.append(t)
                hands.append(seen_by_camera(lm, size))
                t += 1 / 30
        return times, hands

    def test_aspect_is_saved_loaded_and_old_files_default_to_4_3(self):
        d = Path(tempfile.mkdtemp())
        times, hands = self.make(SIZES["16:9"])
        recording.save(d / "a.npz", times, hands, aspect=16 / 9)
        self.assertAlmostEqual(recording.load_aspect(d / "a.npz"), 16 / 9)
        recording.save(d / "b.npz", times, hands)
        self.assertAlmostEqual(recording.load_aspect(d / "b.npz"), 4 / 3)
        np.savez_compressed(d / "old.npz", times=np.array(times), hands=np.stack(hands))     # sin clave `aspect`
        self.assertAlmostEqual(recording.load_aspect(d / "old.npz"), 4 / 3)

    def test_replay_gives_the_same_events_for_the_same_physical_session_in_16_9(self):
        results = {}
        for name in ("4:3", "16:9"):
            times, hands = self.make(SIZES[name])
            w, h = SIZES[name]
            _, summary = recording.replay(times, hands, Settings(), aspect=w / h)
            results[name] = summary.get("click", 0), summary.get("press", 0)
        self.assertEqual(results["16:9"], results["4:3"])
        self.assertEqual(results["4:3"][0], 1)

    def test_replay_restores_the_global_aspect(self):
        set_aspect(640, 480)
        times, hands = self.make(SIZES["16:9"])
        recording.replay(times, hands, Settings(), aspect=16 / 9)
        self.assertEqual(geometry.aspect_scale(), 1.0)


if __name__ == "__main__":
    unittest.main()

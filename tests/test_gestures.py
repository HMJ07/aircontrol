import random
import unittest

import numpy as np

from aircontrol.geometry import gesture_features
from aircontrol.gestures import (GestureRecognizer, GestureStore, HoldDetector, SwipeDetector, builtin_pose)
from tests.handfactory import fist, hand, open_palm, peace, point


def noisy(lm, scale=0.012, seed=0):
    rng = np.random.default_rng(seed)
    return lm + rng.normal(0, scale, lm.shape).astype(np.float32)


class RecognizerTests(unittest.TestCase):
    def setUp(self):
        self.store = GestureStore()
        for i in range(6):
            self.store.add("paz", gesture_features(noisy(peace(), seed=i)))
            self.store.add("tres", gesture_features(noisy(hand(index=1, middle=1, ring=1), seed=10 + i)))
        self.rec = GestureRecognizer(self.store)

    def test_recognizes_trained_gesture_anywhere_and_at_any_size(self):
        self.assertEqual(self.rec.classify(noisy(peace(at=(0.2, -0.15)), seed=99))[0], "paz")
        big = peace().copy()
        big[:, :2] = 0.5 + (big[:, :2] - 0.5) * 0.6        # mano más lejos de la cámara
        self.assertEqual(self.rec.classify(big)[0], "paz")
        self.assertEqual(self.rec.classify(noisy(hand(index=1, middle=1, ring=1), seed=77))[0], "tres")

    def test_unknown_pose_is_rejected(self):
        self.assertIsNone(self.rec.classify(fist())[0])
        self.assertIsNone(self.rec.classify(open_palm())[0])

    def test_needs_minimum_samples(self):
        store = GestureStore()
        store.add("x", gesture_features(peace()))
        self.assertIsNone(GestureRecognizer(store).classify(peace())[0])

    def test_reserved_names_rejected(self):
        with self.assertRaises(ValueError):
            GestureStore().add("fist", gesture_features(fist()))

    def test_store_roundtrip(self):
        import tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "g.json"
        self.store.path = path
        self.store.save()
        again = GestureStore(path)
        self.assertEqual(again.names(), ["paz", "tres"])
        self.assertEqual(GestureRecognizer(again).classify(peace())[0], "paz")


class HoldTests(unittest.TestCase):
    def test_fires_after_hold_once(self):
        h = HoldDetector(default_hold=0.5)
        fired = [h.update("a", t / 30) for t in range(45)]
        self.assertEqual(fired.count("a"), 1)
        self.assertIsNone(fired[5])

    def test_flicker_does_not_reset(self):
        h = HoldDetector(default_hold=0.5, grace=0.15)
        out = []
        for t in range(30):
            out.append(h.update(None if t == 8 else "a", t / 30))
        self.assertIn("a", out)

    def test_switching_gesture_resets(self):
        h = HoldDetector(default_hold=0.5)
        out = [h.update("a" if t % 10 < 5 else "b", t / 30) for t in range(60)]
        self.assertNotIn("a", out)

    def test_per_gesture_hold(self):
        h = HoldDetector(default_hold=0.5, holds={"fist": 1.2})
        self.assertNotIn("fist", [h.update("fist", t / 30) for t in range(30)])
        self.assertIn("fist", [h.update("fist", 1 + t / 30) for t in range(30)])


class SwipeTests(unittest.TestCase):
    def sweep(self, dx, dy, frames=8):
        det, out = SwipeDetector(), []
        for i in range(frames):
            out.append(det.update(open_palm(at=(dx * i / frames, dy * i / frames)), i / 30))
        return [o for o in out if o]

    def test_directions(self):
        self.assertEqual(self.sweep(0.45, 0), ["swipe_right"])
        self.assertEqual(self.sweep(-0.45, 0), ["swipe_left"])
        self.assertEqual(self.sweep(0, -0.45), ["swipe_up"])
        self.assertEqual(self.sweep(0, 0.45), ["swipe_down"])

    def test_small_or_diagonal_motion_ignored(self):
        self.assertEqual(self.sweep(0.1, 0), [])
        self.assertEqual(self.sweep(0.4, 0.35), [])

    def test_requires_open_palm(self):
        det = SwipeDetector()
        out = [det.update(point(at=(0.06 * i, 0)), i / 30) for i in range(8)]
        self.assertEqual([o for o in out if o], [])


class BuiltinPoseTests(unittest.TestCase):
    def test_poses(self):
        self.assertEqual(builtin_pose(fist()), "fist")
        self.assertEqual(builtin_pose(hand(thumb_up=True)), "thumbs_up")
        self.assertEqual(builtin_pose(hand(pinky=1)), "pinky_up")
        self.assertEqual(builtin_pose(hand(thumb=1, pinky=1)), "pinky_up")      # 🤙 con el pulgar fuera también
        self.assertIsNone(builtin_pose(hand(index=1, pinky=1)))                 # otro dedo extendido: no es el gesto
        self.assertIsNone(builtin_pose(open_palm()))
        self.assertIsNone(builtin_pose(point()))


if __name__ == "__main__":
    unittest.main()


class SimilarityTests(unittest.TestCase):
    def test_warns_about_confusable_gestures(self):
        from aircontrol.gestures import too_similar
        store = GestureStore()
        for i in range(5):
            store.add("a", gesture_features(noisy(peace(), seed=i)))
            store.add("b", gesture_features(noisy(peace(), seed=20 + i)))        # casi idéntico a 'a'
            store.add("c", gesture_features(noisy(open_palm(), seed=40 + i)))
        self.assertEqual(too_similar(store, "a"), "b")
        self.assertIsNone(too_similar(store, "c"))

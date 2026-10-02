import unittest

import numpy as np

from aircontrol.camera import CameraHealth
from aircontrol.engine import Engine
from tests.test_engine import FakeHands, FakeWindow, make, sandbox

BLACK = np.zeros((480, 640, 3), np.uint8)
LIT = np.full((480, 640, 3), 90, np.uint8)


class CameraHealthTests(unittest.TestCase):
    def test_black_start_is_tolerated_while_the_camera_warms_up(self):
        h = CameraHealth(grace=3.0)
        self.assertIsNone(h.update(True, BLACK, 0.0))
        self.assertIsNone(h.update(True, BLACK, 2.9))

    def test_black_frames_after_grace_are_reported(self):
        h = CameraHealth(grace=3.0)
        h.update(True, BLACK, 0.0)
        self.assertEqual(h.update(True, BLACK, 3.5), CameraHealth.BLACK)

    def test_no_frames_at_all_are_reported_differently(self):
        h = CameraHealth(grace=3.0)
        h.update(False, None, 0.0)
        self.assertEqual(h.update(False, None, 3.5), CameraHealth.NO_FRAMES)

    def test_recovers_as_soon_as_a_real_image_arrives(self):
        h = CameraHealth(grace=3.0)
        h.update(True, BLACK, 0.0)
        self.assertIsNotNone(h.update(True, BLACK, 4.0))
        self.assertIsNone(h.update(True, LIT, 4.1))

    def test_a_dim_but_real_image_is_not_flagged(self):
        h = CameraHealth(grace=3.0)
        dim = np.full((480, 640, 3), 12, np.uint8)
        h.update(True, dim, 0.0)
        self.assertIsNone(h.update(True, dim, 10.0))

    def test_black_frame_with_a_stray_pixel_counts_as_black(self):
        h = CameraHealth(grace=3.0)
        sparse = BLACK.copy()
        sparse[0, 0] = 255
        h.update(True, sparse, 0.0)
        self.assertEqual(h.update(True, sparse, 3.5), CameraHealth.BLACK)

    def test_notice_lines_are_ascii_for_opencv_font(self):
        for lines in (CameraHealth.BLACK, CameraHealth.NO_FRAMES):
            for line in lines:
                line.encode("ascii")


class DeadCamera:
    """Abre pero nunca entrega fotogramas (como una cámara bloqueada)."""
    released = False

    def read(self):
        return False, None

    def release(self):
        self.released = True


class EngineWithBrokenCameraTests(unittest.TestCase):
    def test_window_keeps_being_serviced_and_shows_a_notice_without_frames(self):
        with sandbox():
            eng = make()
            eng.camera = DeadCamera()
            eng._camera_health = CameraHealth(grace=0.0)
            eng.window = FakeWindow()
            eng.window.keys = [-1] * 4 + [ord("q")]                      # el usuario cierra con 'q'
            self.assertEqual(eng.run(max_frames=1000), 0)
            self.assertEqual(eng._camera_issue, CameraHealth.NO_FRAMES)
            self.assertTrue(eng.window.shown, "la ventana debe mostrar el aviso, no quedarse en negro")
            self.assertEqual(eng.window.keys, [])                          # se atendieron los eventos
            self.assertTrue(eng.camera.released)

    def test_black_frames_get_a_notice_drawn_on_the_preview(self):
        with sandbox():
            eng = make(lambda ts: None)
            eng._camera_health = CameraHealth(grace=0.0)
            eng.window.keys = [-1] * 3 + [ord("q")]
            eng.run(max_frames=100)
            self.assertEqual(eng._camera_issue, CameraHealth.BLACK)
            self.assertTrue(any(img[..., 2].max() > 100 for img in eng.window.shown))   # franja de aviso roja visible

    def test_closing_the_window_logs_why_the_engine_stopped(self):
        with sandbox():
            logs = []
            eng = make(lambda ts: None)
            eng.log = logs.append
            eng.window.closed_flag = True
            eng.run(max_frames=10)
            self.assertTrue(any("cerrada" in m for m in logs), logs)


if __name__ == "__main__":
    unittest.main()

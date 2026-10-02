import unittest
from unittest import mock

import numpy as np

from aircontrol.camera import Camera
from aircontrol.config import Settings


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeCap:
    """Webcam simulada: cada modo (ancho, alto) es 'lit' (imagen), 'black' (negro) o 'dead' (sin fotogramas)."""

    def __init__(self, clock, modes, kind="native", opened=True, default=(640, 480), round_to=None):
        self.clock, self.modes, self.opened, self.kind = clock, modes, opened, kind
        self.size, self.round_to = default, round_to or {}
        self.released = False
        self.log = []

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        import cv2
        w, h = self.size
        self.size = (int(value), h) if prop == cv2.CAP_PROP_FRAME_WIDTH else (w, int(value))
        self.size = self.round_to.get(self.size, self.size)

    def get(self, prop):
        import cv2
        return self.size[0] if prop == cv2.CAP_PROP_FRAME_WIDTH else self.size[1]

    def read(self):
        self.clock.t += 0.5                                          # cada lectura "tarda" medio segundo
        kind = self.modes.get((self.kind, self.size), self.modes.get(self.size, "dead"))
        self.log.append(self.size)
        if kind == "dead":
            return False, None
        w, h = self.size
        return True, np.full((h, w, 3), 120 if kind == "lit" else 0, np.uint8)

    def release(self):
        self.released = True


def make_camera(modes, platform="linux", **kw):
    clock = Clock()
    caps = []

    def opener(index, kind="native"):
        cap = FakeCap(clock, modes, kind=kind, **kw)
        caps.append(cap)
        return cap

    with mock.patch("aircontrol.camera.sys.platform", platform):
        return Camera(Settings(), open_capture=opener, clock=clock), caps


class CameraModeTests(unittest.TestCase):
    def test_requested_mode_is_kept_when_it_works(self):
        cam, caps = make_camera({(640, 480): "lit", (1280, 720): "lit"})
        self.assertEqual(cam.mode, (640, 480))
        self.assertIsNone(cam.note)
        self.assertEqual(len(caps), 1)

    def test_falls_back_to_a_mode_that_gives_image(self):
        cam, _ = make_camera({(640, 480): "black", (1280, 720): "lit"})        # la webcam del usuario
        self.assertEqual(cam.mode, (1280, 720))
        self.assertIn("1280x720", cam.note)
        ok, frame = cam.read()
        self.assertTrue(ok)
        self.assertEqual(frame.shape, (360, 640, 3))                              # reducida a 640 de ancho, 16:9
        self.assertGreater(int(frame.max()), 3)

    def test_falls_back_when_the_requested_mode_returns_no_frames(self):
        cam, _ = make_camera({(640, 480): "dead", (1280, 720): "lit"})
        self.assertEqual(cam.mode, (1280, 720))

    def test_small_frames_are_not_resized(self):
        cam, _ = make_camera({(640, 480): "lit"})
        ok, frame = cam.read()
        self.assertEqual(frame.shape, (480, 640, 3))

    def test_bad_modes_are_released_and_not_retried_when_the_camera_rounds_them(self):
        # pide 1024x768 pero la webcam lo redondea a 1280x720, que ya se probó
        cam, caps = make_camera({(640, 480): "black", (1280, 720): "black", (800, 600): "lit"},
                                round_to={(1024, 768): (1280, 720)})
        self.assertEqual(cam.mode, (800, 600))
        self.assertTrue(all(c.released for c in caps[:-1]))
        self.assertFalse(caps[-1].released)

    def test_when_nothing_gives_image_it_returns_to_the_requested_mode(self):
        cam, _ = make_camera({})
        self.assertEqual(cam.mode, (640, 480))
        self.assertIsNone(cam.note)
        self.assertEqual(cam.read(), (False, None))                               # CameraHealth avisará al usuario

    def test_on_windows_media_foundation_at_720p_is_tried_before_other_modes(self):
        # la HP del usuario: 640x480 negro con DirectShow, pero MSMF a 1280x720 va bien (y DirectShow 720p a 8 fps)
        modes = {("native", (640, 480)): "black", ("msmf", (1280, 720)): "lit", ("native", (1280, 720)): "lit"}
        cam, caps = make_camera(modes, platform="win32")
        self.assertEqual(cam.mode, (1280, 720))
        self.assertEqual(caps[-1].kind, "msmf")
        self.assertIn("Media Foundation", cam.note)
        self.assertEqual(len(caps), 2)

    def test_media_foundation_is_not_used_outside_windows(self):
        modes = {("native", (640, 480)): "black", ("msmf", (1280, 720)): "lit", ("native", (1280, 720)): "lit"}
        cam, caps = make_camera(modes, platform="darwin")
        self.assertTrue(all(c.kind == "native" for c in caps))

    def test_windows_falls_through_to_native_modes_when_msmf_fails_too(self):
        modes = {("native", (640, 480)): "black", ("msmf", (1280, 720)): "dead", ("native", (1280, 720)): "lit"}
        cam, caps = make_camera(modes, platform="win32")
        self.assertEqual((cam.mode, caps[-1].kind), ((1280, 720), "native"))

    def test_unopenable_camera_raises_a_helpful_error(self):
        with self.assertRaises(RuntimeError) as cm:
            make_camera({}, opened=False)
        self.assertIn("Privacidad", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

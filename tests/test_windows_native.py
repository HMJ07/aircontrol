"""Pruebas contra la API real de Windows. Solo se ejecutan en Windows (el CI de GitHub Actions las corre en
windows-latest): aquí se verifica lo que no se puede comprobar desde macOS."""
import ctypes
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

WIN = sys.platform == "win32"
if WIN:
    import ctypes.wintypes


@unittest.skipUnless(WIN, "solo Windows")
class WindowsInputTests(unittest.TestCase):
    def setUp(self):
        from aircontrol import system
        self.system = system
        self.user32 = ctypes.windll.user32

    def cursor(self):
        pt = ctypes.wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def test_screen_size_is_physical_pixels(self):
        w, h = self.system.screen_size()
        self.assertGreater(w, 300)
        self.assertGreater(h, 200)
        self.assertEqual(ctypes.windll.user32.GetSystemMetrics(0), w)        # con DPI awareness = píxeles físicos

    def test_backend_moves_the_real_cursor(self):
        from aircontrol.system.backend import PynputBackend
        backend = PynputBackend()
        for target in ((200, 300), (50, 60), (400, 120)):
            backend.move(*target)
            time.sleep(0.05)
            got = self.cursor()
            if got == (0, 0):
                self.skipTest("el runner no tiene sesión de escritorio interactiva")
            self.assertEqual(got, target)

    def test_active_app_is_a_string_and_never_raises(self):
        name = self.system.active_app()
        self.assertIsInstance(name, str)

    def test_key_combo_does_not_leave_modifiers_pressed(self):
        from aircontrol.system.backend import PynputBackend
        backend = PynputBackend()
        backend.key_combo(["ctrl", "shift", "f13"])
        time.sleep(0.05)
        for vk in (0x11, 0x10):                                              # VK_CONTROL, VK_SHIFT
            self.assertEqual(self.user32.GetAsyncKeyState(vk) & 0x8000, 0)

    def test_focus_app_returns_false_for_a_program_that_is_not_running(self):
        self.assertFalse(self.system.focus_app("no_existe_este_programa.exe"))
        self.assertFalse(self.system.focus_app(""))

    def test_beep_runs_in_another_thread_and_does_not_wait_for_it(self):
        """Sin relojes (un servidor cargado hacía fallar un umbral de 0,2 s): el pitido (simulado, bloqueado hasta que
        lo soltamos) se ejecuta en OTRO hilo y beep() vuelve sin esperar a que termine."""
        import threading
        import winsound
        started, release, finished, thread_ids = threading.Event(), threading.Event(), threading.Event(), []

        def slow_beep(freq, ms):
            thread_ids.append(threading.get_ident())
            started.set()
            release.wait(10)
            finished.set()

        with mock.patch.object(winsound, "Beep", slow_beep):
            self.system.beep()
            self.assertTrue(started.wait(10), "el pitido nunca arrancó")
            self.assertFalse(finished.is_set())                       # beep() ya volvió y el pitido sigue en curso
            self.assertNotEqual(thread_ids[0], threading.get_ident())
            release.set()
            self.assertTrue(finished.wait(10))

    def test_opencv_has_topmost_property_for_the_floating_window(self):
        import cv2
        self.assertTrue(hasattr(cv2, "WND_PROP_TOPMOST"))


class ProcessTests(unittest.TestCase):
    """Valen en todos los sistemas; en Windows ejercitan la rama de ctypes."""

    def test_pid_alive_tracks_a_real_child(self):
        from aircontrol.launcher import pid_alive
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(0.3)
            self.assertTrue(pid_alive(child.pid))
        finally:
            child.terminate()
            child.wait(5)
        self.assertFalse(pid_alive(child.pid))


if __name__ == "__main__":
    unittest.main()

"""Puente Python de la app Android probado con un móvil simulado (bridge falso): los eventos del motor llegan como
llamadas a PhoneBridge (toques, arrastres, deslizamientos, Atrás...). El Kotlin solo se comprueba compilando."""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import numpy as np

from tests.handfactory import fist, hand, open_palm, peace, point
from tests.test_aspect import seen_by_camera
from tests.test_engine import sandbox

ROOT = Path(__file__).resolve().parent.parent
ANDROID_PY = ROOT / "android" / "app" / "src" / "main" / "python"
FILES = [line.strip() for line in (ROOT / "android" / "aircontrol-python-files.txt").read_text().splitlines() if line.strip()]
SIZE = (480, 640)                    # imagen vertical de la cámara frontal (ancho, alto), ya girada a "derecha arriba"
SCREEN = (1080, 2400)
DT = 1 / 30


class FakeBridge:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))

    def of(self, *names):
        return [c for c in self.calls if c[0] in names]


def phone_flat(lm):
    """Lo que recibiría Python: la imagen SIN espejar, 63 números. `lm` son coordenadas tal y como se quieren ver (espejadas)."""
    cam = seen_by_camera(lm, SIZE)
    raw = cam.copy()
    raw[:, 0] = 1 - raw[:, 0]
    return raw.flatten().tolist()


class AndroidTestCase(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ANDROID_PY))
        self.addCleanup(lambda: sys.path.remove(str(ANDROID_PY)))
        self._sb = sandbox()
        self.dir = self._sb.__enter__()
        self.addCleanup(self._sb.__exit__, None, None, None)
        import android_main
        self.am = android_main
        self.bridge = FakeBridge()
        self.assertEqual(android_main.start(self.bridge, str(self.dir), *SCREEN), "ok")
        self.addCleanup(lambda: android_main.stop(0))
        self.t = 0.0

    def feed(self, lm, frames=1, raw=None):
        out = None
        for _ in range(frames):
            out = self.am.process(phone_flat(lm) if raw is None and lm is not None else raw, *SIZE, self.t * 1000)
            self.t += DT
        return out


class SessionTests(AndroidTestCase):
    def test_no_hand_does_nothing(self):
        status = self.feed(None, 10)
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(status["mode"], "idle")

    def test_pointing_moves_the_cursor_inside_the_screen(self):
        self.feed(point(), 10)
        moves = self.bridge.of("move")
        self.assertTrue(moves)
        for _, x, y in moves:
            self.assertTrue(isinstance(x, int) and isinstance(y, int))
            self.assertTrue(0 <= x < SCREEN[0] and 0 <= y < SCREEN[1])

    def test_moving_the_hand_right_moves_the_cursor_right_like_a_mirror(self):
        self.feed(point(at=(-0.2, 0)), 12)
        left = self.bridge.of("move")[-1][1]
        self.feed(point(at=(0.2, 0)), 20)
        right = self.bridge.of("move")[-1][1]
        self.assertGreater(right, left + 200)

    def test_quick_pinch_is_a_tap_at_the_cursor(self):
        self.feed(point(), 12)
        self.feed(point(pinch="index"), 4)
        self.feed(point(), 6)
        taps = self.bridge.of("tap")
        self.assertEqual(len(taps), 1)
        self.assertEqual(taps[0][3], 1)
        last_move = [c for c in self.bridge.of("move")][-1]
        self.assertLess(abs(taps[0][1] - last_move[1]) + abs(taps[0][2] - last_move[2]), 120)

    def test_two_quick_pinches_are_a_double_tap(self):
        self.feed(point(), 10)
        for _ in range(2):
            self.feed(point(pinch="index"), 4)
            self.feed(point(), 4)
        self.assertEqual([c[3] for c in self.bridge.of("tap")], [1, 2])

    def test_holding_the_pinch_is_a_drag_with_start_moves_and_end(self):
        self.feed(point(), 10)
        self.feed(point(pinch="index"), 20)
        for i in range(10):
            self.feed(point(pinch="index", at=(0.01 * i, 0)), 1)
        self.feed(point(), 4)
        names = [c[0] for c in self.bridge.calls if c[0] in ("dragStart", "dragMove", "dragEnd", "tap")]
        self.assertEqual(names[0], "dragStart")
        self.assertIn("dragMove", names)
        self.assertEqual(names[-1], "dragEnd")
        self.assertNotIn("tap", names)

    def test_thumb_and_middle_pinch_is_a_long_press(self):
        self.feed(peace(), 10)
        self.feed(peace(pinch="middle"), 4)
        self.feed(peace(), 6)
        self.assertEqual(len(self.bridge.of("longPress")), 1)
        self.assertEqual(self.bridge.of("tap"), [])

    def test_peace_sign_scroll_becomes_one_swipe_in_the_hand_direction(self):
        for i in range(30):
            self.feed(peace(at=(0, 0.012 * i)), 1)                     # la mano baja
        self.feed(peace(at=(0, 0.36)), 12)                             # y se queda: vacía el scroll pendiente
        swipes = self.bridge.of("swipe")
        self.assertTrue(swipes)
        self.assertTrue(all(s[4] > 0 for s in swipes))                 # el contenido sigue a la mano: hacia abajo
        self.assertLessEqual(len(swipes), 8)                           # agrupados: no un gesto por fotograma
        times = [c for c in swipes]
        self.assertGreaterEqual(len(times), 2)

    def test_palm_swipes_are_system_navigation(self):
        def sweep(dx, dy):
            for i in range(8):
                self.feed(open_palm(at=(dx * i / 8, dy * i / 8)), 1)
            self.feed(None, 40)                                        # respiro antes del siguiente gesto
        sweep(-0.5, 0)
        sweep(0.5, 0)
        sweep(0, -1.0)                                                 # (el formato vertical comprime la altura: más recorrido)
        sweep(0, 1.0)
        self.assertEqual([c[1] for c in self.bridge.of("nav")], ["back", "recents", "home", "notifications"])

    def test_held_fist_pauses_and_the_phone_can_resume(self):
        status = self.feed(fist(), 50)
        self.assertTrue(status["paused"])
        before = len(self.bridge.calls)
        self.feed(point(at=(0.1, 0)), 20)
        self.assertEqual(len(self.bridge.calls), before)                # en pausa no toca nada
        self.am.command("resume")
        self.assertFalse(self.feed(point(), 1)["paused"])

    def test_garbage_input_is_treated_as_no_hand(self):
        for bad in ([0.1] * 62, [float("nan")] * 63, [], [1e9] * 63):
            self.assertIn(self.am.process(bad, *SIZE, self.t * 1000)["mode"], ("idle", "point"))
            self.t += DT

    def test_release_on_stop_never_leaves_a_drag_pressed(self):
        self.feed(point(), 10)
        self.feed(point(pinch="index"), 20)
        self.assertTrue(self.bridge.of("dragStart"))
        self.am.stop(self.t * 1000)
        self.assertTrue(self.bridge.of("dragEnd"))


class BackendTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ANDROID_PY))
        self.addCleanup(lambda: sys.path.remove(str(ANDROID_PY)))
        from android_backend import AndroidBackend
        self.bridge = FakeBridge()
        self.b = AndroidBackend(self.bridge, SCREEN)

    def test_scroll_accumulates_then_flushes_when_idle_or_big(self):
        self.b.flush(0.0)
        self.b.scroll(0, 1)
        self.assertEqual(self.bridge.of("swipe"), [])                   # una línea sola: espera
        self.b.flush(0.1)
        self.assertEqual(self.bridge.of("swipe"), [])
        self.b.flush(0.4)                                               # se detuvo el movimiento
        self.assertEqual(len(self.bridge.of("swipe")), 1)
        self.b.flush(1.0)
        self.b.scroll(0, -5)                                            # un scroll grande sale al momento, hacia arriba
        self.assertEqual(len(self.bridge.of("swipe")), 2)
        self.assertLess(self.bridge.of("swipe")[-1][4], 0)
        self.b.scroll(0, -5)                                            # pero no dos seguidos sin respiro (se cancelarían)
        self.assertEqual(len(self.bridge.of("swipe")), 2)
        self.b.flush(1.3)
        self.assertEqual(len(self.bridge.of("swipe")), 3)

    def test_events_map_to_bridge_calls(self):
        b = self.b
        b.apply(("move", 100, 200))
        b.apply(("click", "left", 2))
        b.apply(("click", "right", 1))
        b.apply(("press", "left"))
        b.apply(("move", 150, 260))
        b.apply(("release", "left"))
        b.type_text("hola")
        b.media("play_pause")
        b.nav("home")
        b.open_target("https://example.com")
        self.assertEqual(self.bridge.calls, [
            ("move", 100, 200), ("tap", 100, 200, 2), ("longPress", 100, 200), ("dragStart", 100, 200),
            ("move", 150, 260), ("dragMove", 150, 260), ("dragEnd", 150, 260), ("typeText", "hola"),
            ("media", "play_pause"), ("nav", "home"), ("openTarget", "https://example.com")])

    def test_a_second_press_without_release_does_not_restart_the_drag(self):
        self.b.press("left")
        self.b.press("left")
        self.assertEqual(len(self.bridge.of("dragStart")), 1)
        self.b.release("right")                                          # el botón derecho no cierra el arrastre izquierdo
        self.assertEqual(self.bridge.of("dragEnd"), [])


class PackagedSubsetTests(unittest.TestCase):
    """El conjunto de ficheros que Gradle mete en el APK debe funcionar solo, sin librerías de escritorio."""

    def test_packaged_files_exist(self):
        for f in FILES:
            self.assertTrue((ROOT / "aircontrol" / f).exists(), f)

    def test_runs_with_only_the_packaged_files_and_no_desktop_libraries(self):
        tree = Path(tempfile.mkdtemp())
        for f in FILES:
            dst = tree / "aircontrol" / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((ROOT / "aircontrol" / f).read_bytes())
        data = Path(tempfile.mkdtemp())
        script = textwrap.dedent(f"""
            import sys
            BLOCKED = {{"cv2", "mediapipe", "pynput", "flask", "werkzeug", "cryptography", "segno", "faster_whisper",
                        "sounddevice", "ollama", "PIL", "pystray", "AppKit", "Quartz", "ctranslate2"}}
            class Blocker:
                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in BLOCKED:
                        raise ImportError("bloqueado en Android: " + name)
            sys.meta_path.insert(0, Blocker())
            sys.path[:0] = [{str(tree)!r}, {str(ANDROID_PY)!r}]
            import android_main
            class Bridge:
                calls = []
                def __getattr__(self, n): return lambda *a: self.calls.append((n, *a))
            assert android_main.start(Bridge(), {str(data)!r}, 1080, 2400) == "ok"
            hand = [0.5, 0.5, 0.0] * 21
            for i in range(5):
                out = android_main.process(hand, 480, 640, i * 33.0)
            assert isinstance(out, dict) and "mode" in out
            android_main.stop(1000.0)
            print("OK")
        """)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "OK", out.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()

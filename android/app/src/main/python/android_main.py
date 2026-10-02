"""Puente entre la app Android (Kotlin) y el motor de AirControl (Python).

Kotlin detecta la mano con MediaPipe y llama a `process` con los 21 puntos de cada fotograma; el motor decide (puntero,
pellizco, scroll, gestos...) y ejecuta el resultado a través de `bridge` (PhoneBridge, Kotlin)."""
import os

# Gestos de la mano en Android. Las acciones `nav:*` son globales (Atrás, Inicio...). Sin atajos de teclado: no hay teclado.
ANDROID_PROFILES = {
    "default": {
        "swipe_left": "nav:back",
        "swipe_right": "nav:recents",
        "swipe_up": "nav:home",
        "swipe_down": "nav:notifications",
        "thumbs_up": "media:play_pause",
    },
    "profiles": [],
}

_session = None


class Session:
    def __init__(self, bridge, screen):
        # El directorio de datos tiene que fijarse ANTES de importar aircontrol.config.
        import numpy as np
        from android_backend import AndroidBackend
        from aircontrol import geometry
        from aircontrol.config import DATA_DIR, Settings
        from aircontrol.controller import Controller
        from aircontrol.gestures import GestureStore
        from aircontrol.profiles import Profiles

        self._np, self._geometry = np, geometry
        self.logs = []
        self.settings = Settings.load()
        self.backend = AndroidBackend(bridge, screen)
        self.ctl = Controller(self.backend, self.settings, GestureStore(DATA_DIR / "gestures.json"),
                              Profiles(ANDROID_PROFILES), log=self.logs.append, app_provider=lambda: "")

    def process(self, hand, width, height, now_ms):
        """Un fotograma. `hand`: 63 números (x, y, z de 21 puntos, imagen vertical SIN espejar) o None."""
        np = self._np
        lm = None
        if hand is not None:
            arr = np.asarray(list(hand), dtype=np.float32)
            if arr.size == 63 and np.isfinite(arr).all():
                lm = arr.reshape(21, 3).copy()
                lm[:, 0] = 1.0 - lm[:, 0]                          # espejo, como una webcam: mover la mano a la derecha = cursor a la derecha
        self._geometry.set_aspect(width, height)
        now = now_ms / 1000.0
        status = self.ctl.process(lm, now, None, width / height)
        self.backend.flush(now)
        return {"mode": status.mode, "paused": bool(self.ctl.paused), "pose": status.pose, "action": status.last_action}

    def command(self, name):
        if name in ("pause", "resume", "toggle_pause"):
            self.ctl._control(name)

    def shutdown(self, now_ms):
        self.ctl.shutdown(now_ms / 1000.0)


def start(bridge, data_dir, screen_w, screen_h):
    global _session
    os.environ["AIRCONTROL_DATA_DIR"] = str(data_dir)
    _session = Session(bridge, (int(screen_w), int(screen_h)))
    return "ok"


def process(hand, width, height, now_ms):
    return _session.process(hand, int(width), int(height), float(now_ms))


def command(name):
    if _session:
        _session.command(str(name))


def stop(now_ms=0):
    global _session
    if _session:
        _session.shutdown(float(now_ms))
        _session = None

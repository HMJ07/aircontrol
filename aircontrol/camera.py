import sys

import cv2


def _preferred_backend():
    # Backends nativos: arrancan mucho más rápido que el autodetectado en Windows/macOS.
    if sys.platform == "win32":
        return cv2.CAP_DSHOW
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


class Camera:
    def __init__(self, settings):
        index = settings.camera_index
        self.cap = cv2.VideoCapture(index, _preferred_backend())
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"No se pudo abrir la cámara {index}. En macOS revisa Ajustes > Privacidad y seguridad > Cámara; "
                "si tienes varias prueba con \"camera_index\": 1 en settings.json.")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.frame_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.frame_height)

    def read(self):
        """Fotograma espejado (mover la mano a la derecha mueve el cursor a la derecha)."""
        ok, frame = self.cap.read()
        return (True, cv2.flip(frame, 1)) if ok else (False, None)

    def release(self):
        self.cap.release()


class CameraHealth:
    """Detecta una cámara que abre pero no entrega imagen útil (obturador de privacidad, otra app que la tiene en
    exclusiva, driver colgado): sin esto la ventana se queda en negro y no se sabe por qué."""
    BLACK_MAX = 3                       # un fotograma cuyo píxel más claro es <= 3 se considera negro
    NO_FRAMES = ("La camara no entrega fotogramas.",
                 "Cierra otras apps que la usen y reinicia AirControl.")
    BLACK = ("La camara devuelve imagen negra.",
             "Revisa el obturador o la tecla de privacidad y que otra app no la use.")

    def __init__(self, grace=3.0):
        self.grace = grace              # los primeros segundos pueden ser negros mientras se enciende
        self._last_good = None
        self._seen_frame = False

    def update(self, ok, frame, now):
        """Devuelve None si todo va bien, o las líneas (ASCII) del aviso a mostrar."""
        if self._last_good is None:
            self._last_good = now
        if ok and frame is not None:
            self._seen_frame = True
            if int(frame[::8, ::8].max()) > self.BLACK_MAX:
                self._last_good = now
        if now - self._last_good <= self.grace:
            return None
        return self.BLACK if self._seen_frame else self.NO_FRAMES

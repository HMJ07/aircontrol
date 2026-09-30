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

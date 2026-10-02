import sys
import time

import cv2


def _preferred_backend():
    # Backends nativos: arrancan mucho más rápido que el autodetectado en Windows/macOS.
    if sys.platform == "win32":
        return cv2.CAP_DSHOW
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def has_image(frame):
    """True si el fotograma tiene imagen de verdad: no basta un píxel suelto en un fotograma negro (algunas webcams
    integradas devuelven negro con algún píxel encendido cuando su modo está roto), hace falta brillo medio."""
    sub = frame[::8, ::8]
    return int(sub.max()) > CameraHealth.BLACK_MAX and float(sub.mean()) > CameraHealth.MIN_MEAN


def _lit_frames_within(cap, seconds, needed=3, clock=time.monotonic):
    """True si en `seconds` llegan `needed` fotogramas con imagen: una cámara con el modo roto da negro o 1 fotograma
    por segundo (p. ej. el 640x480 de algunas webcams integradas en Windows) y no llega a juntarlos."""
    deadline, lit = clock() + seconds, 0
    while clock() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None and has_image(frame):
            lit += 1
            if lit >= needed:
                return True
    return False


class Camera:
    # Modos a probar si el pedido no da imagen (los 16:9 suelen funcionar en webcams cuyo 4:3 va mal).
    FALLBACK_MODES = ((1280, 720), (1920, 1080), (1024, 768), (800, 600))
    PROBE_SECONDS = 2.0
    MAX_WIDTH_FACTOR = 1.3              # si la imagen es más ancha que esto x frame_width se reduce (más rápido)

    def __init__(self, settings, open_capture=None, clock=time.monotonic):
        self._open_capture = open_capture or self._open_default
        self._clock = clock
        self.target_width = settings.frame_width
        self.note = None                # texto para el log si hubo que cambiar de modo
        wanted = (settings.frame_width, settings.frame_height)
        self.cap, self.mode = self._open_working(settings.camera_index, wanted)

    @staticmethod
    def _open_default(index, kind="native"):
        if kind == "msmf":
            return cv2.VideoCapture(index, cv2.CAP_MSMF)
        cap = cv2.VideoCapture(index, _preferred_backend())
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
        return cap

    def _open_at(self, index, size, kind="native"):
        cap = self._open_capture(index, kind)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"No se pudo abrir la cámara {index}. En macOS revisa Ajustes > Privacidad y seguridad > Cámara; "
                "en Windows, Configuración > Privacidad > Cámara. Si tienes varias prueba con "
                "\"camera_index\": 1 en settings.json.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        return cap

    def _attempts(self, wanted):
        """(backend, modo) en orden. En Windows primero Media Foundation (el que usa la app Cámara de Windows: va bien
        donde el modo 640x480 de DirectShow está roto, y a 30 fps) y, como abrirlo justo después de soltar DirectShow
        puede tardar más de 10 s, se prueba antes que él. Después, lo pedido y otros modos con el backend nativo."""
        seq = []
        if sys.platform == "win32":
            seq.append(("msmf", wanted if wanted[0] >= 1280 else (1280, 720)))
        seq.append(("native", wanted))
        seq += [("native", size) for size in self.FALLBACK_MODES]
        return seq

    def _open_working(self, index, wanted):
        """Abre la cámara con el primer modo que entrega imagen sostenida; si ninguno, vuelve al pedido."""
        tried, failed = set(), []
        for n, (kind, size) in enumerate(self._attempts(wanted)):
            if (kind, size) in tried:
                continue
            cap = self._open_at(index, size, kind)
            actual = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            if actual != (0, 0) and actual != size and (kind, actual) in tried:
                cap.release()                                       # la cámara redondeó a un modo ya probado
                continue
            tried.update({(kind, size), (kind, actual)})
            if _lit_frames_within(cap, self.PROBE_SECONDS, clock=self._clock):
                how = "Media Foundation" if kind == "msmf" else "DirectShow/nativo"
                self.note = f"Cámara {index}: {actual[0]}x{actual[1]} ({how})"
                if failed:
                    self.note += f"; sin imagen en {', '.join(failed)}"
                if actual[0] > self.target_width * self.MAX_WIDTH_FACTOR:
                    self.note += f"; se reduce a {self.target_width} px de ancho"
                return cap, actual
            failed.append(f"{actual[0]}x{actual[1]} {'MSMF' if kind == 'msmf' else 'nativo'}")
            cap.release()
        # Ningún modo da imagen: vuelve al pedido y deja que CameraHealth avise al usuario.
        self.note = f"Cámara {index}: ningún modo entrega imagen ({', '.join(failed)})"
        return self._open_at(index, wanted), wanted

    def read(self):
        """Fotograma espejado (mover la mano a la derecha mueve el cursor a la derecha)."""
        ok, frame = self.cap.read()
        if not ok:
            return False, None
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        if w > self.target_width * self.MAX_WIDTH_FACTOR:
            frame = cv2.resize(frame, (self.target_width, round(self.target_width * h / w)),
                               interpolation=cv2.INTER_AREA)
        return True, frame

    def release(self):
        self.cap.release()


class CameraHealth:
    """Detecta una cámara que abre pero no entrega imagen útil (obturador de privacidad, otra app que la tiene en
    exclusiva, driver colgado): sin esto la ventana se queda en negro y no se sabe por qué."""
    BLACK_MAX = 3                       # un fotograma cuyo píxel más claro es <= 3 se considera negro
    MIN_MEAN = 1.5                      # ...y también si su brillo medio es <= 1.5 (negro con algún píxel suelto)
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
            if has_image(frame):
                self._last_good = now
        if now - self._last_good <= self.grace:
            return None
        return self.BLACK if self._seen_frame else self.NO_FRAMES

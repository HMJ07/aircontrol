"""Calibración guiada de la mano: llevas el dedo a las cuatro esquinas de la pantalla y se ajusta la región de la
imagen de cámara que las cubre, a la medida de tu alcance."""
import math
from collections import deque

from .geometry import finger_states
from .pointer import anchor_point

CORNERS = [("arriba a la izquierda", 0, 0), ("arriba a la derecha", 1, 0),
           ("abajo a la derecha", 1, 1), ("abajo a la izquierda", 0, 1)]
MIN_SPAN = (0.22, 0.18)              # región mínima (ancho, alto) para no hacer el cursor hipersensible


class HandCalibration:
    def __init__(self, hold_s=1.2, still=0.025):
        self.hold_s, self.still = hold_s, still
        self.index = 0
        self.points = []
        self._trail = deque()
        self.region = None
        self.error = None

    @property
    def done(self):
        return self.index >= len(CORNERS)

    @property
    def prompt(self):
        return "" if self.done else CORNERS[self.index][0]

    def progress(self, now):
        if not self._trail:
            return 0.0
        return min(1.0, (now - self._trail[0][0]) / self.hold_s)

    def update(self, lm, now):
        """Un fotograma. Devuelve True al terminar. El dedo debe apuntar y quedarse quieto `hold_s`."""
        if self.done:
            return True
        fingers = finger_states(lm) if lm is not None else None
        if fingers is None or not fingers[1] or fingers[3] or fingers[4]:        # misma pose que el puntero
            self._trail.clear()
            return False
        pos = anchor_point(lm)
        if self._trail:
            xs, ys = [p[1] for p in self._trail], [p[2] for p in self._trail]
            if max(math.dist(pos, (x, y)) for x, y in zip(xs, ys)) > self.still:
                self._trail.clear()
        self._trail.append((now, pos[0], pos[1]))
        if now - self._trail[0][0] >= self.hold_s:
            xs = sorted(p[1] for p in self._trail)
            ys = sorted(p[2] for p in self._trail)
            self.points.append((xs[len(xs) // 2], ys[len(ys) // 2]))
            self._trail.clear()
            self.index += 1
            if self.done:
                self._finish()
        return self.done

    def _finish(self):
        tl, tr, br, bl = self.points
        x0, x1 = (tl[0] + bl[0]) / 2, (tr[0] + br[0]) / 2
        y0, y1 = (tl[1] + tr[1]) / 2, (bl[1] + br[1]) / 2
        if x1 - x0 < MIN_SPAN[0] or y1 - y0 < MIN_SPAN[1]:
            self.error = "La región es demasiado pequeña: abre más el gesto hacia cada esquina."
            return
        self.region = {"region_x0": round(float(x0), 4), "region_y0": round(float(y0), 4),
                       "region_x1": round(float(x1), 4), "region_y1": round(float(y1), 4)}

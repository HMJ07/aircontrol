"""Gestos: los que enseña el usuario con unas pocas muestras + los integrados (👍, deslizar la palma)."""
import json
from collections import deque

import numpy as np

from .fsutil import write_text_atomic
from .geometry import finger_states, gesture_features, is_thumbs_up

MIN_SAMPLES = 3
# Nombres reservados: el puño sostenido pausa/reanuda el control, los demás vienen integrados.
BUILTIN = ("thumbs_up", "pinky_up", "swipe_left", "swipe_right", "swipe_up", "swipe_down")
RESERVED = BUILTIN + ("fist",)


class GestureStore:
    """Muestras de gestos entrenados, guardadas en JSON: {nombre: [[42 floats], ...]}."""

    def __init__(self, path=None):
        self.path = path
        self.samples = {}
        if path:
            self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.samples = {name: [np.asarray(s, dtype=np.float32) for s in vecs] for name, vecs in data.items()}

    def save(self):
        if self.path:
            write_text_atomic(self.path, json.dumps({n: [s.round(5).tolist() for s in v] for n, v in self.samples.items()}))

    def add(self, name, features):
        if name in RESERVED:
            raise ValueError(f"'{name}' es un nombre reservado ({', '.join(RESERVED)})")
        self.samples.setdefault(name, []).append(np.asarray(features, dtype=np.float32))

    def remove(self, name):
        return self.samples.pop(name, None) is not None

    def names(self):
        return sorted(self.samples)


class GestureRecognizer:
    """k vecinos más cercanos sobre landmarks normalizados. Un gesto se reconoce solo si está lo bastante
    cerca de sus muestras (`threshold`) y claramente más cerca que cualquier otro (`margin`)."""

    def __init__(self, store, threshold=0.65, margin=1.15, k=3):
        self.store, self.threshold, self.margin, self.k = store, threshold, margin, k

    def classify(self, lm):
        """-> (nombre | None, distancia al mejor)."""
        scores = []
        feat = gesture_features(lm)
        for name, vecs in self.store.samples.items():
            if len(vecs) < MIN_SAMPLES:
                continue
            d = np.sort(np.linalg.norm(np.stack(vecs) - feat, axis=1))[: self.k]
            scores.append((float(d.mean()), name))
        if not scores:
            return None, float("inf")
        scores.sort()
        best_d, best = scores[0]
        if best_d > self.threshold:
            return None, best_d
        if len(scores) > 1 and scores[1][0] < best_d * self.margin:
            return None, best_d                       # ambiguo entre dos gestos
        return best, best_d


class HoldDetector:
    """Dispara un nombre cuando se mantiene `hold` segundos seguidos; luego espera `cooldown`.
    Un parpadeo del tracker (`grace`) no reinicia la cuenta."""

    def __init__(self, default_hold=0.5, holds=None, cooldown=1.5, grace=0.15):
        self.default_hold, self.holds = default_hold, holds or {}
        self.cooldown, self.grace = cooldown, grace
        self._name = None
        self._start = 0.0
        self._last_seen = 0.0
        self._last_fire = float("-inf")

    def update(self, name, now):
        if name != self._name:
            if name is None and self._name is not None and now - self._last_seen <= self.grace:
                return None
            self._name, self._start = name, now
        if name is None:
            return None
        self._last_seen = now
        hold = self.holds.get(name, self.default_hold)
        if now - self._start >= hold and now - self._last_fire >= self.cooldown:
            self._last_fire = now
            self._start = now + 1e9                    # no repetir hasta soltar
            return name
        return None

    def hold_fraction(self, now):
        if self._name is None or self._start > now:
            return 0.0
        return min(1.0, (now - self._start) / self.holds.get(self._name, self.default_hold))


class SwipeDetector:
    """Palma abierta que cruza la imagen en horizontal/vertical -> swipe_left/right/up/down.
    (La imagen llega espejada: derecha = a la derecha del usuario.)"""

    def __init__(self, distance=0.30, window=0.5, cooldown=0.9):
        self.distance, self.window, self.cooldown = distance, window, cooldown
        self._trail = deque()
        self._last_fire = float("-inf")

    def update(self, lm, now):
        if lm is None or sum(finger_states(lm)[1:]) < 4:         # solo con la palma abierta
            self._trail.clear()
            return None
        self._trail.append((now, float(lm[0][0]), float(lm[0][1])))
        while self._trail and now - self._trail[0][0] > self.window:
            self._trail.popleft()
        if now - self._last_fire < self.cooldown or len(self._trail) < 3:
            return None
        dx, dy = self._trail[-1][1] - self._trail[0][1], self._trail[-1][2] - self._trail[0][2]
        if max(abs(dx), abs(dy)) < self.distance or min(abs(dx), abs(dy)) * 2 > max(abs(dx), abs(dy)):
            return None
        self._last_fire = now
        self._trail.clear()
        if abs(dx) > abs(dy):
            return "swipe_right" if dx > 0 else "swipe_left"
        return "swipe_down" if dy > 0 else "swipe_up"


def builtin_pose(lm):
    """Poses estáticas integradas: 'fist' (pausa), 'thumbs_up' y 'pinky_up' (meñique solo, 🤙: abre el teclado)."""
    fingers = finger_states(lm)
    if is_thumbs_up(lm, fingers):
        return "thumbs_up"
    if fingers[1:] == [0, 0, 0, 1]:                  # solo el meñique (con o sin pulgar: 🤙)
        return "pinky_up"
    if sum(fingers[1:]) == 0:
        return "fist"
    return None


def too_similar(store, name, threshold=0.65, margin=1.3):
    """Nombre de otro gesto casi igual a `name` (se confundirían al reconocerlos), o None."""
    mine = store.samples.get(name, [])
    if not mine:
        return None
    mine = np.stack(mine)
    for other, vecs in store.samples.items():
        if other == name or not vecs:
            continue
        d = np.linalg.norm(mine[:, None, :] - np.stack(vecs)[None, :, :], axis=2)
        if d.min(axis=1).mean() < threshold * margin:
            return other
    return None

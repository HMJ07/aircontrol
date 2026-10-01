"""Mirada como puntero. Sin depender de la cámara: trabaja sobre los 478 landmarks de MediaPipe Face Landmarker.

1. `extract_features`: posición del iris dentro de cada ojo + giro/inclinación y posición de la cabeza.
2. `GazeModel`: regresión (ridge, con términos cuadráticos del iris) de esas características a píxeles de pantalla,
   ajustada con la calibración guiada (puntos en pantalla) y validada dejando fuera cada punto.
3. `GazePointer`: modelo + filtro One-Euro + clic por permanencia (dwell), parpadeo largo o pellizco.

Una webcam da una precisión de ~2-4 cm en pantalla: por eso el clic es por permanencia y los objetivos deben ser grandes.
"""
import json
import math
from collections import deque

import numpy as np

from .smoothing import OneEuroFilter

# Índices del Face Mesh de MediaPipe (con iris). "izq/der" = lado de la imagen, no del sujeto.
IRIS_L, IRIS_R = 468, 473
EYE_L = {"left": 33, "right": 133, "up": 159, "down": 145}
EYE_R = {"left": 362, "right": 263, "up": 386, "down": 374}
NOSE, CHIN, FOREHEAD, CHEEK_L, CHEEK_R = 1, 152, 10, 234, 454

FEATURE_NAMES = ("h", "v", "yaw", "pitch", "nx", "ny")

# Apertura de ojo (EAR) por debajo de la cual el ojo está CERRADO. Al mirar hacia abajo los párpados bajan y el EAR cae
# a ~0,1 con los ojos abiertos (medido en una calibración real), así que el umbral debe ser mucho menor que eso.
CLOSED_EAR = 0.06
MIN_PER_POINT = 8                     # muestras mínimas para dar un punto de calibración por bueno
MAX_RETRIES = 2                       # veces que se repite un punto sin muestras suficientes


def _pt(face, i, aspect):
    return np.array([face[i][0] * aspect, face[i][1]], dtype=np.float64)


def _eye(face, iris, eye, aspect):
    left, right = _pt(face, eye["left"], aspect), _pt(face, eye["right"], aspect)
    up, down, c = _pt(face, eye["up"], aspect), _pt(face, eye["down"], aspect), _pt(face, iris, aspect)
    axis = right - left
    width = max(np.linalg.norm(axis), 1e-6)
    axis /= width
    perp = np.array([-axis[1], axis[0]])                       # hacia abajo en la imagen (y crece hacia abajo)
    rel = c - (left + right) / 2
    return float(rel @ axis / width), float(rel @ perp / width), float(np.linalg.norm(up - down) / width)


def extract_features(face, aspect=4 / 3):
    """-> (vector de 6 características, apertura de ojos EAR). `face`: array (478, 3) normalizado a la imagen."""
    hl, vl, ear_l = _eye(face, IRIS_L, EYE_L, aspect)
    hr, vr, ear_r = _eye(face, IRIS_R, EYE_R, aspect)
    nose, chin, fore = _pt(face, NOSE, aspect), _pt(face, CHIN, aspect), _pt(face, FOREHEAD, aspect)
    cl, cr = _pt(face, CHEEK_L, aspect), _pt(face, CHEEK_R, aspect)
    face_w = max(abs(cr[0] - cl[0]), 1e-6)
    face_h = max(abs(chin[1] - fore[1]), 1e-6)
    yaw = (nose[0] - (cl[0] + cr[0]) / 2) / face_w
    pitch = (nose[1] - (fore[1] + chin[1]) / 2) / face_h
    feats = np.array([(hl + hr) / 2, (vl + vr) / 2, yaw, pitch, face[NOSE][0], face[NOSE][1]], dtype=np.float64)
    return feats, (ear_l + ear_r) / 2


def _phi(x, quad=True):
    """Términos de la regresión: 1 y las 6 características; con `quad`, también h², v² y h·v."""
    x = np.atleast_2d(x)
    cols = [np.ones(len(x)), *x.T]
    if quad:
        h, v = x[:, 0], x[:, 1]
        cols += [h * h, v * v, h * v]
    return np.column_stack(cols)


def trim_outliers(feats, groups, z=3.5):
    """Máscara de muestras válidas: dentro de cada punto de calibración se descartan los fotogramas que se alejan de la
    mediana (parpadeos, saltos del landmark del iris). Si se descartaría casi todo un punto, se conserva entero."""
    keep = np.ones(len(feats), dtype=bool)
    for g in np.unique(groups):
        idx = np.where(groups == g)[0]
        if len(idx) < 6:
            continue
        x = feats[idx]
        med = np.median(x, axis=0)
        mad = np.median(np.abs(x - med), axis=0) * 1.4826 + 1e-6
        ok = (np.abs(x - med) / mad).max(axis=1) < z
        if ok.mean() >= 0.4:
            keep[idx] = ok
    return keep


class GazeModel:
    # Candidatos (¿términos cuadráticos?, regularización relativa al nº de muestras). Se elige el mejor por validación.
    CANDIDATES = [(False, 0.01), (False, 0.1), (False, 1.0), (True, 0.1), (True, 1.0), (True, 5.0)]

    def __init__(self, mean, std, weights, screen, rms=None, quad=True, rms_all=None):
        self.mean, self.std, self.weights = np.asarray(mean), np.asarray(std), np.asarray(weights)
        self.screen, self.rms, self.quad, self.rms_all = tuple(screen), rms, quad, rms_all

    @staticmethod
    def _fit_weights(feats, targets, ridge, quad):
        mean, std = feats.mean(axis=0), feats.std(axis=0) + 1e-9
        phi = _phi((feats - mean) / std, quad)
        reg = ridge * len(feats) * np.eye(phi.shape[1])
        reg[0, 0] = 0                                           # el término constante no se penaliza
        return mean, std, np.linalg.solve(phi.T @ phi + reg, phi.T @ targets)

    @staticmethod
    def _corner_groups(targets, groups):
        """Puntos de calibración que son esquinas: para validarlos hay que EXTRAPOLAR, así que no cuentan en el error."""
        ids = np.unique(groups)
        pts = np.array([targets[groups == g][0] for g in ids])
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        span = np.maximum(hi - lo, 1.0)
        edge = (np.abs(pts - lo) < 0.05 * span) | (np.abs(pts - hi) < 0.05 * span)
        return {g for g, e in zip(ids, edge) if e.all()}

    @classmethod
    def _cv(cls, feats, targets, groups, screen, quad, ridge, corners):
        """Error por puntos dejando fuera cada punto de calibración (predicción recortada a la pantalla)."""
        inner, every = [], []
        for g in np.unique(groups):
            train, test = groups != g, groups == g
            if train.sum() < 12:
                continue
            m, s, w = cls._fit_weights(feats[train], targets[train], ridge, quad)
            pred = _phi((feats[test] - m) / s, quad) @ w
            pred = np.clip(pred, 0, np.array(screen) - 1)
            err = np.linalg.norm(pred - targets[test], axis=1)
            every.extend(err)
            if g not in corners:
                inner.extend(err)
        rms = lambda e: float(np.sqrt(np.mean(np.square(e)))) if len(e) else None
        return rms(inner or every), rms(every)

    @classmethod
    def fit(cls, feats, targets, screen, groups=None):
        """`feats` (N,6), `targets` (N,2) en píxeles. Con `groups` (id del punto de calibración de cada muestra) se
        descartan atípicos, se prueban varios modelos y se elige el de menor error dejando fuera cada punto."""
        feats, targets = np.asarray(feats, float), np.asarray(targets, float)
        if len(feats) < 12 or feats.std(axis=0).max() < 1e-6:
            raise ValueError("muestras insuficientes para calibrar")
        if groups is None:
            mean, std, w = cls._fit_weights(feats, targets, 0.1, False)
            return cls(mean, std, w, screen, None, quad=False)
        groups = np.asarray(groups)
        keep = trim_outliers(feats, groups)
        feats, targets, groups = feats[keep], targets[keep], groups[keep]
        corners = cls._corner_groups(targets, groups)
        best = None
        for quad, ridge in cls.CANDIDATES:
            rms, rms_all = cls._cv(feats, targets, groups, screen, quad, ridge, corners)
            if rms is not None and (best is None or rms < best[0]):
                best = (rms, rms_all, quad, ridge)
        if best is None:
            mean, std, w = cls._fit_weights(feats, targets, 0.1, False)
            return cls(mean, std, w, screen, None, quad=False)
        rms, rms_all, quad, ridge = best
        mean, std, w = cls._fit_weights(feats, targets, ridge, quad)
        return cls(mean, std, w, screen, rms, quad=quad, rms_all=rms_all)

    def predict(self, feats):
        p = (_phi((np.asarray(feats, float) - self.mean) / self.std, self.quad) @ self.weights)[0]
        return (min(max(p[0], 0.0), self.screen[0] - 1), min(max(p[1], 0.0), self.screen[1] - 1))

    def to_json(self):
        return json.dumps({"mean": self.mean.tolist(), "std": self.std.tolist(), "weights": self.weights.tolist(),
                           "screen": list(self.screen), "rms": self.rms, "quad": self.quad, "rms_all": self.rms_all})

    @classmethod
    def from_json(cls, text):
        d = json.loads(text)
        return cls(d["mean"], d["std"], d["weights"], d["screen"], d.get("rms"), d.get("quad", True), d.get("rms_all"))


class GazeCalibration:
    """Calibración guiada: muestra un punto, espera a que el ojo se asiente y recoge características.
    Se maneja por fotogramas (`update`) para poder dibujarla y probarla sin cámara."""

    GRID = [(0.5, 0.5), (0.08, 0.08), (0.92, 0.08), (0.08, 0.92), (0.92, 0.92),
            (0.5, 0.08), (0.5, 0.92), (0.08, 0.5), (0.92, 0.5)]

    def __init__(self, screen, settle_s=1.2, collect_s=1.2, points=None):
        self.screen, self.settle_s, self.collect_s = screen, settle_s, collect_s
        self.points = points or self.GRID
        self.index, self.started = 0, None
        self.retries = {}
        self.feats, self.targets, self.groups, self.ears = [], [], [], []
        self.model = None
        self.error = None

    @property
    def done(self):
        return self.index >= len(self.points)

    def target_px(self):
        x, y = self.points[min(self.index, len(self.points) - 1)]
        return x * (self.screen[0] - 1), y * (self.screen[1] - 1)

    def progress(self, now):
        """(punto actual, total, fase 'settle'|'collect', 0..1 dentro de la fase)."""
        t = 0.0 if self.started is None else now - self.started
        if t < self.settle_s:
            return self.index, len(self.points), "settle", t / self.settle_s
        return self.index, len(self.points), "collect", min(1.0, (t - self.settle_s) / self.collect_s)

    def retrying(self):
        return self.retries.get(self.index, 0) > 0

    def update(self, features, ear, now, eyes_open=True):
        """Un fotograma. `features` None si no hay cara. Devuelve True al terminar."""
        if self.done:
            return True
        if self.started is None:
            self.started = now
        t = now - self.started
        if t >= self.settle_s and features is not None and eyes_open:
            self.feats.append(features)
            self.targets.append(self.target_px())
            self.groups.append(self.index)
            self.ears.append(ear)
        if t >= self.settle_s + self.collect_s:
            got = self.groups.count(self.index)
            if got < MIN_PER_POINT and self.retries.get(self.index, 0) < MAX_RETRIES:
                self.retries[self.index] = self.retries.get(self.index, 0) + 1     # sin muestras: se repite el punto
                self.started = None
                return False
            self.index, self.started = self.index + 1, None
            if self.done:
                self._finish()
        return self.done

    @property
    def missing(self):
        """Puntos de calibración que se quedaron sin muestras suficientes (aun tras repetirlos)."""
        return [i for i in range(len(self.points)) if self.groups.count(i) < MIN_PER_POINT]

    def _finish(self):
        try:
            self.model = GazeModel.fit(self.feats, self.targets, self.screen, self.groups)
        except ValueError as e:
            self.error = str(e)


class BlinkDetector:
    """Parpadeo largo (cerrar los ojos entre `min_s` y `max_s`) = clic. Los parpadeos normales se ignoran.
    La apertura se compara con la mediana reciente, así sirve para ojos de cualquier forma."""

    def __init__(self, min_s=0.6, max_s=2.0, closed_ratio=0.55, closed_abs=CLOSED_EAR):
        self.min_s, self.max_s, self.closed_ratio, self.closed_abs = min_s, max_s, closed_ratio, closed_abs
        self._open = deque(maxlen=90)
        self._closed_since = None
        self.closed = False

    def update(self, ear, now):
        """ear None = sin cara. Devuelve True en el instante en que se abren los ojos tras un parpadeo largo."""
        if ear is None:
            self._closed_since, self.closed = None, False
            return False
        baseline = float(np.median(self._open)) if len(self._open) >= 10 else None
        # Cerrado = mucho menos que lo habitual Y casi sin apertura. Solo lo primero confundía "mirar abajo" con cerrar.
        is_closed = baseline is not None and ear < baseline * self.closed_ratio and ear < self.closed_abs
        fired = False
        if is_closed:
            self._closed_since = self._closed_since if self._closed_since is not None else now
        else:
            if self._closed_since is not None and self.min_s <= now - self._closed_since <= self.max_s:
                fired = True
            self._closed_since = None
            self._open.append(ear)                              # solo se aprende del ojo abierto
        self.closed = is_closed
        return fired


class DwellClicker:
    """Clic cuando el cursor se queda dentro de `radius` px durante `dwell_s`. Tras el clic hay un respiro
    y hay que salir del radio antes de poder volver a hacer clic (evita ráfagas al mirar un botón)."""

    def __init__(self, dwell_s=1.0, radius=60.0, cooldown=0.8):
        self.dwell_s, self.radius, self.cooldown = dwell_s, radius, cooldown
        self._anchor = None
        self._since = 0.0
        self._armed = True
        self._last_click = float("-inf")

    def progress(self, now):
        if self._anchor is None or not self._armed:
            return 0.0
        return min(1.0, (now - self._since) / self.dwell_s)

    def update(self, pos, now):
        if pos is None:
            self._anchor = None
            return False
        if self._anchor is None or math.dist(pos, self._anchor) > self.radius:
            self._anchor, self._since, self._armed = pos, now, True
            return False
        if self._armed and now - self._since >= self.dwell_s and now - self._last_click >= self.cooldown:
            self._armed, self._last_click = False, now
            return True
        return False


class GazePointer:
    """Mirada -> eventos de ratón, como AirMouse pero con la cara. Clic por `click_mode`:
    dwell | blink | pinch (el clic lo da la mano, ver Controller) | off."""

    def __init__(self, model, settings):
        self.model, self.cfg = model, settings
        self.fx = OneEuroFilter(settings.gaze_min_cutoff, settings.gaze_beta)
        self.fy = OneEuroFilter(settings.gaze_min_cutoff, settings.gaze_beta)
        self.dwell = DwellClicker(settings.dwell_s, settings.dwell_radius_px)
        self.blink = BlinkDetector()
        self.cursor = None
        self.click_enabled = True              # el teclado aéreo gestiona su propia permanencia
        self.norm = None                       # posición normalizada 0..1 (para el teclado)
        self.mode = "idle"
        self._hold_until = 0.0

    def update(self, face, now, aspect=4 / 3, active=True):
        events = []
        if face is None or not active:
            self.dwell.update(None, now)
            self.blink.update(None, now)
            self.mode = "idle"
            return events
        feats, ear = extract_features(face, aspect)
        blink_click = self.blink.update(ear, now)
        if self.blink.closed:
            self._hold_until = now + 0.25      # con el ojo cerrándose el iris se mueve: congelar el cursor
        self.mode = "gaze"
        if now >= self._hold_until:
            x, y = self.model.predict(feats)
            x, y = self.fx(x, now), self.fy(y, now)
            pos = (int(round(x)), int(round(y)))
            if pos != self.cursor:
                self.cursor = pos
                events.append(("move", *pos))
            self.norm = (x / max(self.model.screen[0] - 1, 1), y / max(self.model.screen[1] - 1, 1))
        if not self.click_enabled:
            return events
        if self.cfg.gaze_click == "dwell" and self.cursor and not self.blink.closed:
            if self.dwell.update(self.cursor, now):
                events.append(("click", "left", 1))
        elif self.cfg.gaze_click == "blink" and blink_click and self.cursor:
            events.append(("click", "left", 1))
        return events

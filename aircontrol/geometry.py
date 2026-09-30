"""Geometría de la mano sobre landmarks de MediaPipe: array (21, 3) normalizado a la imagen (y crece hacia abajo)."""
import math

import numpy as np

WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, MIDDLE_TIP = 0, 4, 5, 8, 9, 12

CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_size(lm):
    """Muñeca -> base del corazón: escala que no cambia con la distancia a la cámara."""
    return max(dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-6)


def finger_states(lm):
    """[pulgar, índice, corazón, anular, meñique] -> 1 si el dedo está extendido."""
    wrist = lm[WRIST]
    thumb = dist(lm[4], lm[17]) > dist(lm[2], lm[17]) * 1.15
    states = [1 if thumb else 0]
    for tip, pip in zip((8, 12, 16, 20), (6, 10, 14, 18)):
        states.append(1 if dist(lm[tip], wrist) > dist(lm[pip], wrist) * 1.08 else 0)
    return states


def finger_extension(lm):
    """[índice, corazón, anular, meñique] -> distancia punta-muñeca / distancia articulación-muñeca.
    ~0.5 plegado, ~1.2 a medias, ~1.6+ extendido del todo: permite exigir "claramente extendido"."""
    wrist = lm[WRIST]
    return [dist(lm[tip], wrist) / max(dist(lm[pip], wrist), 1e-6)
            for tip, pip in zip((8, 12, 16, 20), (6, 10, 14, 18))]


def pinch_ratio(lm, finger_tip):
    """Distancia pulgar-punta del dedo en tamaños de mano (0 = pellizco completo)."""
    return dist(lm[THUMB_TIP], lm[finger_tip]) / hand_size(lm)


def is_thumbs_up(lm, fingers=None):
    """Pulgar hacia arriba con el resto de dedos plegados (no vale con la mano boca abajo)."""
    fingers = fingers or finger_states(lm)
    if any(fingers[1:]):
        return False
    return lm[4][1] < lm[3][1] < lm[2][1] and (lm[0][1] - lm[4][1]) > 0.9 * hand_size(lm)


def gesture_features(lm):
    """Vector invariante a posición y tamaño: landmarks relativos a la muñeca / tamaño de la mano."""
    pts = np.asarray(lm, dtype=np.float32)[:, :2]
    return ((pts - pts[0]) / hand_size(lm)).flatten()

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


# Los landmarks son coordenadas normalizadas: x en [0,1] del ANCHO e y en [0,1] del ALTO. En un fotograma 16:9 una misma
# distancia física horizontal pesa un 25 % menos (frente a la vertical) que en uno 4:3, y los umbrales (pellizco,
# extensión de dedos) se afinaron con 4:3 (640x480). `set_aspect` ajusta el peso de x para que cualquier forma de
# fotograma dé las mismas medidas que 4:3. Con 4:3 el factor es 1: nada cambia.
REFERENCE_ASPECT = 4 / 3
_x_scale = 1.0


def set_aspect(width, height):
    """Indica la forma (ancho x alto en píxeles) de los fotogramas de los que vienen los landmarks."""
    global _x_scale
    if width and height and width > 0 and height > 0:
        _x_scale = (width / height) / REFERENCE_ASPECT


def aspect_scale():
    return _x_scale


def dist(a, b):
    """Distancia euclídea sin corregir (sirve para píxeles de pantalla)."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hdist(a, b):
    """Distancia entre dos landmarks de la mano, corregida por la forma del fotograma."""
    return math.hypot((a[0] - b[0]) * _x_scale, a[1] - b[1])


def hand_size(lm):
    """Muñeca -> base del corazón: escala que no cambia con la distancia a la cámara."""
    return max(hdist(lm[WRIST], lm[MIDDLE_MCP]), 1e-6)


def finger_states(lm):
    """[pulgar, índice, corazón, anular, meñique] -> 1 si el dedo está extendido."""
    wrist = lm[WRIST]
    thumb = hdist(lm[4], lm[17]) > hdist(lm[2], lm[17]) * 1.15
    states = [1 if thumb else 0]
    for tip, pip in zip((8, 12, 16, 20), (6, 10, 14, 18)):
        states.append(1 if hdist(lm[tip], wrist) > hdist(lm[pip], wrist) * 1.08 else 0)
    return states


def finger_extension(lm):
    """[índice, corazón, anular, meñique] -> distancia punta-muñeca / distancia articulación-muñeca.
    ~0.5 plegado, ~1.2 a medias, ~1.6+ extendido del todo: permite exigir "claramente extendido"."""
    wrist = lm[WRIST]
    return [hdist(lm[tip], wrist) / max(hdist(lm[pip], wrist), 1e-6)
            for tip, pip in zip((8, 12, 16, 20), (6, 10, 14, 18))]


def pinch_ratio(lm, finger_tip):
    """Distancia pulgar-punta del dedo en tamaños de mano (0 = pellizco completo)."""
    return hdist(lm[THUMB_TIP], lm[finger_tip]) / hand_size(lm)


def is_thumbs_up(lm, fingers=None):
    """Pulgar hacia arriba con el resto de dedos plegados (no vale con la mano boca abajo)."""
    fingers = fingers or finger_states(lm)
    if any(fingers[1:]):
        return False
    return lm[4][1] < lm[3][1] < lm[2][1] and (lm[0][1] - lm[4][1]) > 0.9 * hand_size(lm)


def gesture_features(lm):
    """Vector invariante a posición y tamaño: landmarks relativos a la muñeca / tamaño de la mano."""
    pts = np.array(lm, dtype=np.float32)[:, :2]
    pts[:, 0] *= _x_scale                                   # misma corrección de forma que el resto de medidas
    return ((pts - pts[0]) / hand_size(lm)).flatten()

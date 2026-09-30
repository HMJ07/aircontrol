"""Manos sintéticas (array 21x3, coordenadas normalizadas, y hacia abajo) para probar sin cámara."""
import numpy as np


def hand(thumb=0, index=0, middle=0, ring=0, pinky=0, thumb_up=False, at=(0.0, 0.0),
         pinch=None, index_x=0.44):
    """`pinch` = "index" | "middle": la punta del pulgar toca esa punta de dedo. `at` desplaza toda la mano."""
    lm = np.full((21, 3), 0.5, dtype=np.float32)
    lm[0] = (0.5, 0.9, 0)
    for base, pip, tip, x, up in ((5, 6, 8, index_x, index), (9, 10, 12, 0.50, middle),
                                  (13, 14, 16, 0.56, ring), (17, 18, 20, 0.62, pinky)):
        lm[base] = (x, 0.65, 0)
        lm[pip] = (x, 0.55, 0)
        lm[tip] = (x, 0.30 if up else 0.70, 0)
    lm[2] = (0.40, 0.80, 0)
    lm[3] = (0.38, 0.70 if thumb_up else 0.80, 0)
    if thumb_up:
        lm[4] = (0.38, 0.55, 0)
    else:
        lm[4] = (0.22, 0.72, 0) if thumb else (0.52, 0.78, 0)
    if pinch:
        tip = lm[8 if pinch == "index" else 12]
        lm[4] = (tip[0] + 0.01, tip[1] + 0.02, 0)
    lm[:, 0] += at[0]
    lm[:, 1] += at[1]
    return lm


def point(**kw):
    """☝️ índice extendido, resto plegado."""
    return hand(index=1, **kw)


def peace(**kw):
    return hand(index=1, middle=1, **kw)


def open_palm(**kw):
    return hand(thumb=1, index=1, middle=1, ring=1, pinky=1, **kw)


def fist(**kw):
    return hand(**kw)

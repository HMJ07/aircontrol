"""Grabar y reproducir sesiones de manos: permite afinar umbrales con tus propios movimientos, sin cámara.

  aircontrol record sesion.npz --seconds 30     (haz los gestos que te dan problemas)
  aircontrol replay sesion.npz --set pinch_on=0.25 --set scroll_enter_s=0.4
"""
from collections import Counter

import numpy as np


def save(path, times, hands, aspect=None):
    """`hands`: lista de arrays (21, 3) o None. Sin mano se guarda NaN."""
    arr = np.stack([h if h is not None else np.full((21, 3), np.nan, np.float32) for h in hands]) \
        if hands else np.zeros((0, 21, 3), np.float32)
    np.savez_compressed(path, times=np.asarray(times, dtype=np.float64), hands=arr.astype(np.float32),
                        aspect=np.float64(aspect or 4 / 3))


def load(path):
    data = np.load(path)
    hands = [None if np.isnan(h[0, 0]) else h for h in data["hands"]]
    return list(data["times"]), hands


def load_aspect(path, default=4 / 3):
    """Forma (ancho/alto) del fotograma con el que se grabó; las grabaciones antiguas no la guardan (4:3)."""
    data = np.load(path)
    return float(data["aspect"]) if "aspect" in data.files else default


def replay(times, hands, settings, backend=None, profiles=None, store=None, app="", screen=(1710, 1107), aspect=None):
    """Pasa la sesión por el Controller con un backend de simulación. Devuelve (eventos, resumen).
    `aspect` (ancho/alto del fotograma de la grabación) ajusta las medidas de la mano como en directo."""
    from . import geometry
    from .controller import Controller
    from .gestures import GestureStore
    from .profiles import Profiles
    from .system.backend import DryRunBackend

    backend = backend or DryRunBackend(log=lambda *_: None)
    backend.screen_size = lambda: screen
    ctl = Controller(backend, settings, store or GestureStore(), profiles or Profiles(), log=lambda *_: None,
                     app_provider=lambda: app)
    modes = Counter()
    previous = geometry.aspect_scale()
    if aspect:
        geometry.set_aspect(aspect, 1)
    try:
        for t, lm in zip(times, hands):
            modes[ctl.process(lm, t).mode] += 1
        ctl.shutdown(times[-1] + 1 if len(times) else 0)
    finally:
        geometry.set_aspect(previous * geometry.REFERENCE_ASPECT, 1)                     # no deja el ajuste cambiado
    events = backend.calls
    summary = Counter(e[0] for e in events)
    summary.update({f"modo_{m}": n for m, n in modes.items()})
    return events, dict(summary)

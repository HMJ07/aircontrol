"""Grabar y reproducir sesiones de manos: permite afinar umbrales con tus propios movimientos, sin cámara.

  aircontrol record sesion.npz --seconds 30     (haz los gestos que te dan problemas)
  aircontrol replay sesion.npz --set pinch_on=0.25 --set scroll_enter_s=0.4
"""
from collections import Counter

import numpy as np


def save(path, times, hands):
    """`hands`: lista de arrays (21, 3) o None. Sin mano se guarda NaN."""
    arr = np.stack([h if h is not None else np.full((21, 3), np.nan, np.float32) for h in hands]) \
        if hands else np.zeros((0, 21, 3), np.float32)
    np.savez_compressed(path, times=np.asarray(times, dtype=np.float64), hands=arr.astype(np.float32))


def load(path):
    data = np.load(path)
    hands = [None if np.isnan(h[0, 0]) else h for h in data["hands"]]
    return list(data["times"]), hands


def replay(times, hands, settings, backend=None, profiles=None, store=None, app="", screen=(1710, 1107)):
    """Pasa la sesión por el Controller con un backend de simulación. Devuelve (eventos, resumen)."""
    from .controller import Controller
    from .gestures import GestureStore
    from .profiles import Profiles
    from .system.backend import DryRunBackend

    backend = backend or DryRunBackend(log=lambda *_: None)
    backend.screen_size = lambda: screen
    ctl = Controller(backend, settings, store or GestureStore(), profiles or Profiles(), log=lambda *_: None,
                     app_provider=lambda: app)
    modes = Counter()
    for t, lm in zip(times, hands):
        modes[ctl.process(lm, t).mode] += 1
    ctl.shutdown(times[-1] + 1 if len(times) else 0)
    events = backend.calls
    summary = Counter(e[0] for e in events)
    summary.update({f"modo_{m}": n for m, n in modes.items()})
    return events, dict(summary)

"""Diagnóstico con la mano real: qué ve el reconocedor en cada gesto y qué ajuste cambiaría el resultado."""
from collections import Counter

import numpy as np

STEPS = [
    ("point", "Señala con el índice (los demás dedos plegados) y mueve la mano por la imagen"),
    ("click", "Pellizca pulgar + índice UNA vez, rápido, como un clic"),
    ("double", "Haz DOS pellizcos seguidos, rápidos (doble clic)"),
    ("drag", "Pellizca, MANTÉN el pellizco y mueve la mano; luego suelta (arrastrar)"),
    ("right", "Extiende el corazón y pellizca pulgar + corazón (clic derecho)"),
    ("scroll", "Haz ✌️ (índice y corazón bien extendidos) y sube / baja la mano (scroll)"),
    ("thumb", "Pulgar hacia arriba, mantenido 1 segundo"),
]


class StepStats:
    """Acumula lo que midió el reconocedor durante un paso."""

    def __init__(self):
        self.frames = self.hand_frames = 0
        self.modes = Counter()
        self.left, self.right = [], []
        self.ext = [[], [], [], []]              # índice, corazón, anular, meñique
        self.events = Counter()
        self.pinch_runs = []                     # duración (s) de cada racha continua de pellizco
        self.lost_during_pinch = 0               # veces que el seguimiento perdió la mano en pleno pellizco
        self._run_start = None
        self._last_now = None
        self._last_mode = None
        self.pair_min = []                       # por fotograma: min(extensión índice, extensión corazón)

    def add(self, lm, mode, debug, events=(), now=0.0):
        self.frames += 1
        self.modes[mode] += 1
        for e in events:
            self.events[e[0]] += 1
        pinching = mode in ("pinch", "drag")
        if pinching and self._run_start is None:
            self._run_start = now
        elif not pinching and self._run_start is not None:
            self.pinch_runs.append(self._last_now - self._run_start)
            self._run_start = None
        if lm is None and self._last_mode in ("pinch", "drag"):
            self.lost_during_pinch += 1
        self._last_now, self._last_mode = now, mode
        if lm is None or not debug:
            return
        self.pair_min.append(min(debug["ext"][0], debug["ext"][1]))
        self.hand_frames += 1
        self.left.append(debug["left"])
        self.right.append(debug["right"])
        for i, v in enumerate(debug["ext"]):
            self.ext[i].append(v)

    def median(self, i):
        return float(np.median(self.ext[i])) if self.ext[i] else float("nan")

    def pct(self, i, q):
        return float(np.percentile(self.ext[i], q)) if self.ext[i] else float("nan")

    def longest_pinch(self):
        runs = self.pinch_runs + ([self._last_now - self._run_start] if self._run_start is not None else [])
        return max(runs) if runs else 0.0

    def fraction(self, mode):
        return self.modes[mode] / self.frames if self.frames else 0.0


def measures(st):
    """Una línea con lo medido (la hoja de datos para ajustar umbrales)."""
    if not st.hand_frames:
        return "sin mano"
    return (f"mano {st.hand_frames}/{st.frames} · pellizco mín índice {min(st.left):.2f} corazón {min(st.right):.2f} · "
            f"extensión p90 (índ/cor/anu/meñ) {st.pct(0, 90):.2f}/{st.pct(1, 90):.2f}/{st.pct(2, 90):.2f}/{st.pct(3, 90):.2f} · "
            f"p10 {st.pct(0, 10):.2f}/{st.pct(1, 10):.2f}/{st.pct(2, 10):.2f}/{st.pct(3, 10):.2f} · "
            f"pellizco más largo {st.longest_pinch():.2f}s · modos {dict(st.modes)}")


def suggest(step, st, cfg):
    """Lista de frases: qué pasó y qué cambiar. Vacía si el paso salió bien o no hay nada concreto que proponer."""
    out = []
    if st.frames and st.hand_frames / st.frames < 0.5:
        return [f"Casi no se vio la mano ({st.hand_frames}/{st.frames} fotogramas): más luz, fondo liso y la mano a "
                "30-60 cm de la cámara."]
    if step == "point" and st.fraction("point") < 0.3:
        ring, pinky, middle = st.pct(2, 25), st.pct(3, 25), st.pct(1, 25)
        if max(ring, pinky) > 1.08:
            out.append(f"Anular/meñique salen extendidos al apuntar (extensión {ring:.2f}/{pinky:.2f}; se consideran "
                       "extendidos por encima de 1.08): pliégalos más.")
        if middle > cfg.scroll_extension:
            out.append(f"El corazón sale extendido (extensión {middle:.2f} > scroll_extension {cfg.scroll_extension}): "
                       "se toma como ✌️. Pliégalo o sube scroll_extension.")
        out.append(f"Modo reconocido: {dict(st.modes)}")
    elif step in ("click", "double", "right"):
        ratios = st.right if step == "right" else st.left
        closest = min(ratios) if ratios else float("nan")
        if closest > cfg.pinch_on:
            out.append(f"Tu pellizco más cerrado midió {closest:.2f} y el umbral pinch_on es {cfg.pinch_on}: "
                       f"súbelo a ~{min(0.55, closest * 1.3):.2f}.")
        elif st.events["press"] and not st.events["click"]:
            out.append(f"El pellizco se detecta pero dura más de drag_hold_s ({cfg.drag_hold_s} s) o la mano se mueve "
                       f"más de {cfg.drag_deadzone_px:.0f} px: se toma como arrastre. Hazlo más corto o sube esos ajustes.")
        elif not st.events["click"]:
            out.append("El pellizco no llegó a empezar: apunta con el índice un instante antes de pellizcar.")
        if step == "right" and st.median(1) < 1.08:
            out.append("Para el clic derecho el corazón debe estar extendido antes de pellizcar.")
    elif step == "drag" and not (st.events["press"] and st.events["release"]):
        longest = st.longest_pinch()
        if longest == 0:
            out.append("No se detectó ningún pellizco: apunta con el índice y luego pellizca.")
        elif st.lost_during_pinch:
            out.append(f"El seguimiento perdió la mano {st.lost_during_pinch} veces en pleno pellizco (los dedos se tapan "
                       "entre sí): gira la palma hacia la cámara, más luz, o aleja un poco la mano.")
        elif longest < cfg.drag_hold_s:
            out.append(f"Tu pellizco más largo duró {longest:.2f} s y hacen falta {cfg.drag_hold_s} s (o mover la mano "
                       f"{cfg.drag_deadzone_px:.0f} px): mantenlo más tiempo.")
        else:
            out.append(f"Hubo un pellizco de {longest:.2f} s pero no arrastre: copia esta salida, es un fallo a corregir.")
    elif step == "scroll" and not st.events["scroll"]:
        reach = float(np.percentile(st.pair_min, 90)) if st.pair_min else float("nan")
        if reach <= cfg.scroll_extension:
            out.append(f"Tus dedos con ✌️ llegaron (p90) a {reach:.2f} y scroll_extension pide {cfg.scroll_extension}: "
                       f"extiéndelos más o baja scroll_extension a ~{max(1.05, reach * 0.92):.2f}.")
        else:
            out.append(f"Mantén ✌️ {cfg.scroll_enter_s} s quieto antes de mover la mano.")
    return out

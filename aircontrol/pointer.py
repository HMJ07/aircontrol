"""Ratón aéreo: convierte la posición de la mano en eventos de ratón.

Lógica pura (sin cámara ni SO): recibe landmarks y un reloj, devuelve eventos
  ("move", x, y) · ("click", botón, nº_clics) · ("press", botón) · ("release", botón) · ("scroll", dx, dy)
para que se pueda probar con manos sintéticas.

Poses:
  ☝️  índice extendido (anular y meñique plegados) -> mueve el cursor
  🤏  pulgar + índice -> clic izquierdo (dos seguidos = doble clic; mantener o mover = arrastrar)
  🤏  pulgar + corazón -> clic derecho
  ✌️  índice y corazón extendidos -> scroll con el movimiento vertical de la mano
  🖐️ / ✊ y cualquier otra -> reposo: el cursor no se toca
"""
import math
from collections import deque

from .config import Settings
from .geometry import (INDEX_MCP, INDEX_TIP, MIDDLE_TIP, dist, finger_extension, finger_states, hand_size,
                       pinch_ratio)
from .smoothing import OneEuroFilter

# Si el tracker pierde la mano un instante no se cancela un clic o arrastre en curso.
LOST_GRACE = 0.30
# Posición del cursor "hace un momento": al pellizcar, el dedo se mueve antes de que lo detectemos.
REWIND_S = 0.25
# Un pellizco puede empezar si hace menos de esto se estaba apuntando: al cerrar la mano el índice se curva hacia el
# pulgar y los demás dedos se relajan, así que en el instante del contacto la pose "apuntar" ya no se cumple.
PINCH_LATCH_S = 0.4
# Con los dedos pegados el seguimiento tiembla: un pellizco no se suelta por UN fotograma por encima de pinch_off.
RELEASE_FRAMES = 2
# Peso de la punta del índice frente a su base: la base se mueve menos al pellizcar y evita clics desviados.
TIP_WEIGHT = 0.2


def anchor_point(lm, tip_weight=TIP_WEIGHT):
    """Punto de la mano que mueve el cursor: mezcla de base y punta del índice (normalizado a la imagen).
    Con `tip_weight=0` es solo la base del índice, que no se mueve al curvar el dedo."""
    return (lm[INDEX_MCP][0] * (1 - tip_weight) + lm[INDEX_TIP][0] * tip_weight,
            lm[INDEX_MCP][1] * (1 - tip_weight) + lm[INDEX_TIP][1] * tip_weight)


class AirMouse:
    def __init__(self, settings: Settings, screen_size):
        self.cfg = settings
        self.screen_w, self.screen_h = screen_size
        self.fx = OneEuroFilter(settings.smooth_min_cutoff, settings.smooth_beta)
        self.fy = OneEuroFilter(settings.smooth_min_cutoff, settings.smooth_beta)
        self.mode = "idle"                 # idle | point | pinch | drag | scroll  (para el overlay)
        self.debug = {}                    # valores en vivo para el HUD de diagnóstico
        self._reset()
        self._last_click = (float("-inf"), (0.0, 0.0), 0)    # (hora, posición, nº de clics)

    def _reset(self):
        self.fx.reset()
        self.fy.reset()
        self.cursor = None                 # última posición enviada al SO
        self._history = deque()
        self._last_seen = float("-inf")
        self._pinch = None                 # dict del pellizco en curso
        self._over = 0                     # fotogramas seguidos por encima de pinch_off
        self._scroll_y = None
        self._scroll_acc = 0.0
        self._scroll_since = None          # desde cuándo se mantiene ✌️ claramente extendido
        self._last_point = float("-inf")   # última vez que se apuntó (índice fuera, anular y meñique plegados)
        self._last_index_up = float("-inf")
        self._last_middle_up = float("-inf")
        self.mode = "idle"

    # --- utilidades ---------------------------------------------------------------------------
    def _to_screen(self, lm, tip_weight=TIP_WEIGHT):
        """Landmark -> píxeles de pantalla usando la región activa de la cámara (con margen)."""
        cfg = self.cfg
        ax, ay = anchor_point(lm, tip_weight)
        nx = (ax - cfg.region_x0) / (cfg.region_x1 - cfg.region_x0)
        ny = (ay - cfg.region_y0) / (cfg.region_y1 - cfg.region_y0)
        return (min(max(nx, 0.0), 1.0) * (self.screen_w - 1), min(max(ny, 0.0), 1.0) * (self.screen_h - 1))

    def _emit_move(self, x, y, now, events):
        x, y = int(round(x)), int(round(y))
        if self.cursor != (x, y):
            self.cursor = (x, y)
            events.append(("move", x, y))
        self._history.append((now, (x, y)))
        while self._history and now - self._history[0][0] > 0.5:
            self._history.popleft()

    def _position_at(self, t):
        pos = None
        for ts, p in self._history:
            if ts > t:
                break
            pos = p
        return pos or (self._history[0][1] if self._history else self.cursor)

    def _release_all(self, events):
        if self._pinch and self._pinch["pressed"]:
            events.append(("release", self._pinch["button"]))

    # --- bucle principal ----------------------------------------------------------------------
    def update(self, lm, now, active=True):
        """Un fotograma. `lm` es None si no hay mano; `active=False` (pausa) suelta todo y no emite nada."""
        events = []
        if lm is None or not active:
            if not active or now - self._last_seen > LOST_GRACE:
                self._release_all(events)
                self._reset()
            return events
        self._last_seen = now

        cfg = self.cfg
        fingers = finger_states(lm)
        index, middle, ring, pinky = fingers[1:]
        left_ratio, right_ratio = pinch_ratio(lm, INDEX_TIP), pinch_ratio(lm, MIDDLE_TIP)

        # Pellizco con histéresis: se activa por debajo de `pinch_on` y no se suelta hasta `pinch_off`.
        pointing = index and not (ring or pinky)
        if pointing:
            self._last_point = now
        if index:
            self._last_index_up = now
        if middle:
            self._last_middle_up = now
        if self._pinch:
            ratio = left_ratio if self._pinch["button"] == "left" else right_ratio
            self._over = self._over + 1 if ratio >= cfg.pinch_off else 0
            pinching = self._over < RELEASE_FRAMES
        else:
            pinching = None
            # Solo se pellizca con un dedo que estaba extendido hace un instante: con un puño o el corazón plegado
            # el pulgar queda junto a otros dedos y daría clics fantasma. El margen (PINCH_LATCH_S) cubre que el dedo
            # se curve al cerrar el pellizco y que anular y meñique se relajen (gesto "OK").
            latched = now - self._last_point <= PINCH_LATCH_S
            candidates = {
                "left": left_ratio if (index or now - self._last_index_up <= PINCH_LATCH_S) else 9,
                "right": right_ratio if (middle or now - self._last_middle_up <= PINCH_LATCH_S) else 9}
            best = min(candidates, key=candidates.get)        # con dos cerca gana el más cerrado
            if (latched or not (ring or pinky)) and candidates[best] < cfg.pinch_on:
                pinching = best

        if self._pinch and not pinching:
            self._finish_pinch(now, events)
        elif self._pinch:
            self._update_pinch(lm, now, events)
            return events
        elif pinching:
            self._start_pinch(pinching, now, lm)
            return events

        ext = finger_extension(lm)
        self.debug = {"left": left_ratio, "right": right_ratio, "ext": ext, "fingers": fingers}
        # ✌️: solo con índice y corazón CLARAMENTE extendidos y mantenidos. Un corazón a medias mientras se apunta
        # es normal: eso sigue siendo apuntar (mueve el cursor), no scroll.
        holding = self.mode == "scroll"
        clear = pointing and middle and min(ext[0], ext[1]) > (cfg.scroll_extension - (0.15 if holding else 0.0))
        if clear:
            self._scroll_since = self._scroll_since if self._scroll_since is not None else now
            if holding or now - self._scroll_since >= cfg.scroll_enter_s:
                self._update_scroll(lm, events)
                self.mode = "scroll"
            else:
                self.mode = "idle"                                 # ✌️ recién hecho: esperar a que se sostenga
            return events
        self._scroll_since = None
        if pointing:
            self.mode = "point"
            x, y = self._to_screen(lm)
            self._emit_move(self.fx(x, now), self.fy(y, now), now, events)
        else:
            self.mode = "idle"
            self.fx.reset()
            self.fy.reset()
        return events

    # --- pellizco / clic / arrastre ----------------------------------------------------------
    def _start_pinch(self, button, now, lm):
        origin = self._position_at(now - REWIND_S)
        if origin is None:                                     # primer gesto tras el reposo: donde está la mano
            origin = tuple(int(round(v)) for v in self._to_screen(lm))
        # Durante el pellizco el movimiento se mide con la BASE del índice (estable): la punta se desplaza varios
        # centímetros al curvar el dedo y convertiría cada clic rápido en un arrastre.
        self._over = 0
        self._pinch = {"button": button, "start": now, "pressed": False, "origin": origin,
                       "base0": self._to_screen(lm, 0.0)}
        self.mode = "pinch"

    def _update_pinch(self, lm, now, events):
        pinch, cfg = self._pinch, self.cfg
        bx, by = self._to_screen(lm, 0.0)
        dx, dy = bx - pinch["base0"][0], by - pinch["base0"][1]
        x, y = self.fx(pinch["origin"][0] + dx, now), self.fy(pinch["origin"][1] + dy, now)
        if not pinch["pressed"]:
            moved = math.hypot(dx, dy)
            if moved > cfg.drag_deadzone_px or now - pinch["start"] > cfg.drag_hold_s:
                if self.cursor != pinch["origin"]:
                    events.append(("move", *pinch["origin"]))
                    self.cursor = pinch["origin"]
                events.append(("press", pinch["button"]))
                pinch["pressed"] = True
                self.mode = "drag"
        if pinch["pressed"]:
            self._emit_move(x, y, now, events)

    def _finish_pinch(self, now, events):
        pinch, cfg = self._pinch, self.cfg
        self._pinch = None
        self.mode = "point"
        if pinch["pressed"]:
            events.append(("release", pinch["button"]))
            return
        pos = pinch["origin"]
        if self.cursor != pos:
            events.append(("move", *pos))
            self.cursor = pos
        last_t, last_pos, last_n = self._last_click
        count = 1
        if pinch["button"] == "left" and now - last_t < cfg.double_click_s and dist(pos, last_pos) < cfg.drag_deadzone_px:
            count = min(last_n + 1, 3)
        self._last_click = (now, pos, count)
        events.append(("click", pinch["button"], count))

    # --- scroll ------------------------------------------------------------------------------
    def _update_scroll(self, lm, events):
        y = lm[INDEX_MCP][1]
        if self._scroll_y is not None:
            delta = (y - self._scroll_y) / hand_size(lm) * self.cfg.scroll_gain   # líneas por tamaño de mano
            self._scroll_acc += delta if self.cfg.scroll_natural else -delta
            lines = int(self._scroll_acc)
            if lines:
                self._scroll_acc -= lines
                events.append(("scroll", 0, lines))
        self._scroll_y = y

"""Une puntero, gestos, pausa y perfiles. Un `process()` por fotograma; no sabe nada de cámara ni de ventanas."""
from dataclasses import dataclass

from .actions import ActionRunner
from .gaze import GazePointer
from .geometry import INDEX_TIP, finger_states, pinch_ratio
from .gestures import GestureRecognizer, HoldDetector, SwipeDetector, builtin_pose
from .keyboard import KeyboardState
from .pointer import AirMouse

APP_POLL_S = 0.5


@dataclass
class Status:
    mode: str = "idle"           # idle | point | pinch | drag | scroll | paused
    paused: bool = False
    pose: str = ""               # gesto que se está manteniendo ahora ("fist", "thumbs_up", nombre propio...)
    hold: float = 0.0            # 0..1 progreso del gesto mantenido (anillo de progreso)
    app: str = ""
    profile: str = "default"
    last_action: str = ""
    input_mode: str = "hand"     # hand | gaze
    keyboard: bool = False
    dwell: float = 0.0           # 0..1 progreso del clic por permanencia / de la tecla del teclado


class Controller:
    def __init__(self, backend, settings, store, profiles, log=print, app_provider=None, gaze_model=None,
                 on_voice=None, on_feedback=None):
        self.backend, self.cfg, self.profiles, self.log = backend, settings, profiles, log
        self.on_voice, self.on_feedback = on_voice, on_feedback
        self.app_provider = app_provider or backend.active_app
        self.mouse = AirMouse(settings, backend.screen_size())
        self.recognizer = GestureRecognizer(store, threshold=settings.gesture_threshold)
        self.swipes = SwipeDetector(settings.swipe_distance)
        self.holds = HoldDetector(settings.gesture_hold_s, holds={"fist": settings.pause_hold_s, "thumbs_up": 0.6, "pinky_up": 0.8})
        self.runner = ActionRunner(backend, on_control=self._control, log=log)
        self.paused = False
        self.input_mode = "hand"
        self.gaze = GazePointer(gaze_model, settings) if gaze_model else None
        self.keyboard = None                 # KeyboardState mientras el teclado aéreo está abierto
        self.keyboard_rect = None            # (x, y, ancho, alto) del teclado en pantalla: la mirada se mapea a él
        self.kb_uv = None
        self._gaze_pinch = False
        self.status = Status()
        self._app, self._app_checked = "", float("-inf")
        self._action_shown_until = 0.0

    def _control(self, kind, arg=None):
        if kind in ("pause", "resume", "toggle_pause"):
            self.paused = {"pause": True, "resume": False}.get(kind, not self.paused)
            self.log("⏸  AirControl en pausa" if self.paused else "▶  AirControl activo")
        elif kind == "keyboard":
            self.set_keyboard(self.keyboard is None)
        elif kind == "mode":
            self.set_mode(arg if arg != "toggle" else ("gaze" if self.input_mode == "hand" else "hand"))
        elif kind == "voice" and self.on_voice:
            self.on_voice()

    def set_gaze_model(self, model):
        self.gaze = GazePointer(model, self.cfg) if model else None

    def set_mode(self, mode):
        """'hand' | 'gaze'. La mirada necesita calibración previa; devuelve False si no se pudo cambiar."""
        if mode == "gaze" and self.gaze is None:
            self.log("⚠️  La mirada necesita calibración: aircontrol calibrate gaze")
            return False
        self.input_mode = mode
        self.log(f"🖱  Puntero: {'mirada' if mode == 'gaze' else 'mano'}")
        return True

    def set_keyboard(self, on):
        if on and self.keyboard is None:
            self.keyboard = KeyboardState(self.cfg.keyboard_dwell_s)
            self.kb_uv = None
            target = self.app_provider() or ""                    # la app que está detrás de AirControl
            self.keyboard.target = target
            if target:                                            # si el foco quedó en AirControl, lo escrito se perdería
                self.backend.focus_app(target)
            self.log(f"⌨️  Teclado aéreo abierto (escribe en: {target or 'la app en primer plano'})")
        elif not on and self.keyboard is not None:
            self.keyboard = None
            self.log("⌨️  Teclado aéreo cerrado")

    def _current_app(self, now):
        if now - self._app_checked >= APP_POLL_S:
            self._app, self._app_checked = self.app_provider() or "", now
        return self._app

    def process(self, lm, now, face=None, aspect=4 / 3):
        """Procesa un fotograma (`lm` = landmarks de la mano, `face` = de la cara; None si no hay) y devuelve
        el Status para el overlay."""
        custom = self.recognizer.classify(lm)[0] if lm is not None and not self.paused else None
        pose = custom or (builtin_pose(lm) if lm is not None else None)
        fired = self.holds.update(pose, now)
        if self.paused:
            pose = pose if pose == "fist" else None
            fired = fired if fired == "fist" else None

        # El puño sostenido pausa/reanuda; se comprueba antes que nada para que siempre haya una salida.
        if fired == "fist":
            self._control("toggle_pause")
            self.mouse.update(None, now, active=False)              # suelta botones y reinicia
            self.status.last_action, self._action_shown_until = "pausa" if self.paused else "activo", now + 2
            return self._status(now, pose)

        active = not self.paused
        # Un gesto propio reconocido manda sobre el puntero mientras se mantiene.
        self._pointer(None if custom else lm, face, aspect, now, active)
        if not active:
            return self._status(now, pose)

        gesture = fired or self.swipes.update(lm, now)
        if gesture:
            action = self.profiles.resolve(gesture, self._current_app(now))
            if action:
                self.runner.run(action)
                self.status.last_action = f"{gesture} → {action.spec}"
                self._action_shown_until = now + 2
                self.log(f"✋ {self.status.last_action}  [{self._app or '?'}]")
        return self._status(now, pose)

    # --- puntero: mano o mirada, o teclado aéreo -----------------------------------------------
    def _gaze_pinch_edge(self, lm):
        """Flanco de subida del pellizco pulgar-índice (para hacer clic con la mano mientras la mirada apunta)."""
        if lm is None:
            self._gaze_pinch = False
            return False
        ratio = pinch_ratio(lm, INDEX_TIP)
        pinching = ratio < (self.cfg.pinch_off if self._gaze_pinch else self.cfg.pinch_on) \
            and (self._gaze_pinch or finger_states(lm)[1])
        edge, self._gaze_pinch = pinching and not self._gaze_pinch, pinching
        return edge

    def _pointer(self, lm, face, aspect, now, active):
        use_gaze = self.input_mode == "gaze" and self.gaze is not None
        if self.keyboard is not None:
            return self._keyboard_frame(lm, face, aspect, now, active, use_gaze)
        if use_gaze:
            self.mouse.update(None, now, active=False)                  # la mano no mueve el cursor
            self.gaze.click_enabled = True
            events = self.gaze.update(face, now, aspect, active)
            pinch = self.cfg.gaze_click == "pinch" and active and self._gaze_pinch_edge(lm)
            for event in events:
                self.backend.apply(event)
                if event[0] == "click" and self.on_feedback:
                    self.on_feedback("click")
            if pinch:
                self.backend.apply(("click", "left", 1))
            return
        for event in self.mouse.update(lm, now, active=active):
            self.backend.apply(event)

    def _keyboard_frame(self, lm, face, aspect, now, active, use_gaze):
        """Con el teclado abierto el puntero no mueve el cursor del sistema: elige teclas."""
        uv, pinch = None, False
        self.keyboard.target = self._current_app(now) or self.keyboard.target
        if use_gaze:
            self.mouse.update(None, now, active=False)
            self.gaze.click_enabled = False
            self.gaze.update(face, now, aspect, active)
            if self.gaze.cursor and self.keyboard_rect:
                x, y, w, h = self.keyboard_rect
                uv = ((self.gaze.cursor[0] - x) / w, (self.gaze.cursor[1] - y) / h)
            pinch = self.cfg.gaze_click == "pinch" and active and self._gaze_pinch_edge(lm)
        else:
            W, H = self.mouse.screen_w - 1, self.mouse.screen_h - 1
            for event in self.mouse.update(lm, now, active=active):
                if event[0] == "move":
                    self.kb_uv = (event[1] / W, event[2] / H)
                elif event[0] == "click" and event[1] == "left":
                    pinch = True
            uv = self.kb_uv if lm is not None and active else None
            if lm is None:
                self.kb_uv = None
        self.kb_uv = uv if use_gaze else self.kb_uv
        for action in (self.keyboard.update(uv, now, pinch) if active and uv else []):
            if action[0] in ("type", "key") and self.on_feedback:
                self.on_feedback("key")
            if action[0] == "type":
                self.backend.type_text(action[1])
            elif action[0] == "key":
                self.backend.key_combo([action[1]])
            elif action[0] == "close":
                self.set_keyboard(False)
                break

    def shutdown(self, now):
        """Al salir: suelta cualquier botón que siga pulsado para no dejar el ratón 'agarrado'."""
        for event in self.mouse.update(None, now, active=False):
            self.backend.apply(event)

    def _status(self, now, pose):
        s = self.status
        s.paused, s.pose = self.paused, pose or ""
        s.mode = "paused" if self.paused else self.mouse.mode
        s.hold = self.holds.hold_fraction(now) if pose else 0.0
        s.app = self._current_app(now)
        s.profile = self.profiles.profile_name(s.app)
        s.input_mode, s.keyboard = self.input_mode, self.keyboard is not None
        if self.keyboard is not None:
            s.dwell = self.keyboard.dwell_progress(now) if not self.keyboard.hover is None else 0.0
        elif self.input_mode == "gaze" and self.gaze is not None:
            s.dwell = self.gaze.dwell.progress(now) if self.cfg.gaze_click == "dwell" else 0.0
        else:
            s.dwell = 0.0
        if now > self._action_shown_until:
            s.last_action = ""
        return s

    def refresh_settings(self):
        """Tras cambiar los ajustes en caliente: traslada a los filtros y detectores los valores que copiaron al crearse."""
        c = self.cfg
        for f in (self.mouse.fx, self.mouse.fy):
            f.min_cutoff, f.beta = c.smooth_min_cutoff, c.smooth_beta
        self.recognizer.threshold = c.gesture_threshold
        self.swipes.distance = c.swipe_distance
        self.holds.default_hold = c.gesture_hold_s
        self.holds.holds["fist"] = c.pause_hold_s
        if self.keyboard:
            self.keyboard.dwell_s = c.keyboard_dwell_s
        if self.gaze:
            for f in (self.gaze.fx, self.gaze.fy):
                f.min_cutoff, f.beta = c.gaze_min_cutoff, c.gaze_beta
            self.gaze.dwell.dwell_s, self.gaze.dwell.radius = c.dwell_s, c.dwell_radius_px

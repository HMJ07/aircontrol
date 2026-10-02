"""InputBackend de Android: traduce los eventos del motor a llamadas al puente Kotlin (PhoneBridge), que los ejecuta con el
Servicio de Accesibilidad (toques, arrastres, deslizamientos, Atrás/Inicio) y dibuja el cursor."""
from aircontrol.system.backend import InputBackend

SCROLL_PX_PER_LINE = 45          # píxeles de deslizamiento por cada "línea" de scroll del motor
SCROLL_FLUSH_LINES = 4           # se envía un deslizamiento cuando se acumulan al menos estas líneas...
SCROLL_FLUSH_IDLE = 0.25         # ...o cuando pasan estos segundos sin más movimiento
SCROLL_MIN_INTERVAL = 0.2        # entre dos deslizamientos (un gesto nuevo de Accesibilidad cancela el anterior)


class AndroidBackend(InputBackend):
    def __init__(self, bridge, screen):
        self.bridge, self._screen = bridge, tuple(screen)
        self.pos = (screen[0] // 2, screen[1] // 2)
        self.pressed = False
        self._scroll_acc, self._scroll_at, self._last_swipe = 0.0, 0.0, -1e9

    # --- consulta
    def screen_size(self):
        return self._screen

    def active_app(self):
        return ""                                                 # Android no deja saber qué app hay delante sin permisos extra

    # --- puntero
    def move(self, x, y):
        self.pos = (int(x), int(y))
        self.bridge.move(*self.pos)
        if self.pressed:
            self.bridge.dragMove(*self.pos)

    def press(self, button):
        if button == "left" and not self.pressed:
            self.pressed = True
            self.bridge.dragStart(*self.pos)

    def release(self, button):
        if button == "left" and self.pressed:
            self.pressed = False
            self.bridge.dragEnd(*self.pos)

    def click(self, button, count=1):
        if button == "left":
            self.bridge.tap(self.pos[0], self.pos[1], int(count))
        else:                                                     # en Android no hay clic derecho: pulsación larga (menús)
            self.bridge.longPress(*self.pos)

    def scroll(self, dx, dy):
        """Pequeños scrolls se acumulan y salen como UN deslizamiento (cada gesto de Accesibilidad tiene latencia)."""
        self._scroll_acc += dy
        self._scroll_at = self._clock_now
        if abs(self._scroll_acc) >= SCROLL_FLUSH_LINES and self._clock_now - self._last_swipe >= SCROLL_MIN_INTERVAL:
            self._flush_scroll()

    _clock_now = 0.0

    def flush(self, now):
        """Se llama en cada fotograma: suelta un scroll pendiente si el movimiento se detuvo."""
        self._clock_now = now
        if self._scroll_acc and (now - self._scroll_at >= SCROLL_FLUSH_IDLE or (
                abs(self._scroll_acc) >= SCROLL_FLUSH_LINES and now - self._last_swipe >= SCROLL_MIN_INTERVAL)):
            self._flush_scroll()

    def _flush_scroll(self):
        dy_px = int(self._scroll_acc * SCROLL_PX_PER_LINE)
        self._scroll_acc, self._last_swipe = 0.0, self._clock_now
        if dy_px:
            self.bridge.swipe(self.pos[0], self.pos[1], 0, dy_px)

    # --- teclado, medios, navegación
    def key_combo(self, keys):
        pass                                                      # no hay teclado físico que simular (v1)

    def type_text(self, text):
        self.bridge.typeText(text)

    def media(self, name):
        self.bridge.media(name)

    def open_target(self, target):
        self.bridge.openTarget(target)

    def focus_app(self, name):
        return False

    def nav(self, name):
        self.bridge.nav(name)

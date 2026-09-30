"""Backends de entrada: PynputBackend (real, macOS y Windows) y DryRunBackend (solo registra)."""
from . import active_app, open_target, screen_size

BUTTONS = ("left", "right")


class InputBackend:
    """Interfaz que usan AirMouse/ActionRunner. `screen_size` y `active_app` vienen del SO."""

    def screen_size(self):
        return screen_size()

    def active_app(self):
        return active_app()

    def move(self, x, y): raise NotImplementedError
    def press(self, button): raise NotImplementedError
    def release(self, button): raise NotImplementedError
    def click(self, button, count=1): raise NotImplementedError
    def scroll(self, dx, dy): raise NotImplementedError
    def key_combo(self, keys): raise NotImplementedError
    def type_text(self, text): raise NotImplementedError
    def media(self, name): raise NotImplementedError
    def open_target(self, target): open_target(target)

    def apply(self, event):
        """Aplica un evento de AirMouse: ("move", x, y) · ("click", botón, n) · ("press"/"release", botón) · ("scroll", dx, dy)."""
        kind, *args = event
        getattr(self, kind)(*args)


class DryRunBackend(InputBackend):
    """No toca el ratón ni el teclado: escribe lo que haría. Para probar gestos y perfiles sin riesgos."""

    def __init__(self, log=print):
        self.log = log
        self.calls = []

    def _note(self, *call):
        self.calls.append(call)
        self.log("[dry-run] " + " ".join(str(c) for c in call))

    def move(self, x, y):
        self.calls.append(("move", x, y))                 # demasiado frecuente para imprimirlo

    def press(self, button): self._note("press", button)
    def release(self, button): self._note("release", button)
    def click(self, button, count=1): self._note("click", button, count)
    def scroll(self, dx, dy): self._note("scroll", dx, dy)
    def key_combo(self, keys): self._note("key", "+".join(keys))
    def type_text(self, text): self._note("text", repr(text))
    def media(self, name): self._note("media", name)
    def open_target(self, target): self._note("open", target)


class PynputBackend(InputBackend):
    def __init__(self):
        from pynput import keyboard, mouse
        self._mouse, self._keyboard = mouse.Controller(), keyboard.Controller()
        self._Button = {"left": mouse.Button.left, "right": mouse.Button.right}
        Key = keyboard.Key
        self._Key = Key
        self._named = {"ctrl": Key.ctrl, "shift": Key.shift, "alt": Key.alt, "cmd": Key.cmd, "esc": Key.esc,
                       "enter": Key.enter, "tab": Key.tab, "space": Key.space, "backspace": Key.backspace,
                       "delete": Key.delete, "left": Key.left, "right": Key.right, "up": Key.up, "down": Key.down,
                       "home": Key.home, "end": Key.end, "page_up": Key.page_up, "page_down": Key.page_down}
        self._media = {"play_pause": Key.media_play_pause, "next": Key.media_next, "prev": Key.media_previous,
                       "volume_up": Key.media_volume_up, "volume_down": Key.media_volume_down,
                       "mute": Key.media_volume_mute}

    def move(self, x, y):
        self._mouse.position = (x, y)

    def press(self, button):
        self._mouse.press(self._Button[button])

    def release(self, button):
        self._mouse.release(self._Button[button])

    def click(self, button, count=1):
        self._mouse.click(self._Button[button], count)

    def scroll(self, dx, dy):
        self._mouse.scroll(dx, dy)

    def _resolve(self, name):
        if name in self._named:
            return self._named[name]
        if len(name) > 1 and name[0] == "f" and name[1:].isdigit():
            return getattr(self._Key, name)
        return name                                        # carácter suelto

    def key_combo(self, keys):
        *mods, last = [self._resolve(k) for k in keys]
        pressed = []
        try:
            for m in mods:
                self._keyboard.press(m)
                pressed.append(m)
            self._keyboard.press(last)
            self._keyboard.release(last)
        finally:
            for m in reversed(pressed):                    # nunca dejar un modificador pulsado
                self._keyboard.release(m)

    def type_text(self, text):
        self._keyboard.type(text)

    def media(self, name):
        key = self._media[name]
        self._keyboard.press(key)
        self._keyboard.release(key)


def create_backend(dry_run=False):
    return DryRunBackend() if dry_run else PynputBackend()

"""Acciones asociables a un gesto, escritas como texto en los perfiles:

  key:mod+c  key:ctrl+shift+t  key:space     atajo de teclado (`mod` = ⌘ en macOS, Ctrl en Windows)
  click · right_click · double_click         clics en la posición actual
  scroll:down[:5]  scroll:up[:5]             rueda del ratón
  media:play_pause|next|prev|volume_up|volume_down|mute
  open:Safari  open:https://...              abre una aplicación o una dirección
  text:hola                                   escribe texto
  pause · resume · toggle_pause               control de AirControl
  nav:back|home|recents|notifications|quick_settings   navegación del sistema (Android)
  mode:hand|gaze|toggle                       puntero con la mano o con la mirada
  keyboard                                    abre/cierra el teclado aéreo
  voice                                       escucha una orden de voz
"""
import sys
from dataclasses import dataclass

MEDIA = ("play_pause", "next", "prev", "volume_up", "volume_down", "mute")
SIMPLE = ("click", "right_click", "double_click", "pause", "resume", "toggle_pause", "keyboard", "voice")
MODES = ("hand", "gaze", "toggle")
NAV = ("back", "home", "recents", "notifications", "quick_settings")
MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "option": "alt", "opt": "alt",
             "cmd": "cmd", "command": "cmd", "win": "cmd", "super": "cmd", "meta": "cmd"}
KEY_ALIASES = {"esc": "esc", "escape": "esc", "enter": "enter", "return": "enter", "tab": "tab", "space": "space",
               "backspace": "backspace", "delete": "delete", "del": "delete", "left": "left", "right": "right",
               "up": "up", "down": "down", "home": "home", "end": "end", "pageup": "page_up",
               "pagedown": "page_down", "plus": "+", "minus": "-", "page_up": "page_up", "page_down": "page_down"}


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    kind: str
    arg: object = None
    spec: str = ""


def parse_keys(spec, platform=None):
    """'mod+shift+t' -> ['cmd', 'shift', 't'] (macOS) / ['ctrl', 'shift', 't'] (resto). La última es la tecla."""
    platform = platform or sys.platform
    parts = [p.strip().lower() for p in spec.split("+")]
    if not parts or not all(parts):
        raise ActionError(f"atajo vacío o mal escrito: '{spec}'")
    out = []
    for part in parts:
        if part == "mod":
            part = "cmd" if platform == "darwin" else "ctrl"
        elif part in MODIFIERS:
            part = MODIFIERS[part]
        elif part in KEY_ALIASES:
            part = KEY_ALIASES[part]
        elif len(part) == 1 or (part[0] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 20):
            pass
        else:
            raise ActionError(f"tecla desconocida '{part}' en '{spec}'")
        out.append(part)
    if out[-1] in ("ctrl", "shift", "alt", "cmd") and len(out) > 1:
        raise ActionError(f"el atajo '{spec}' no tiene tecla final")
    return out


def parse_action(spec, platform=None):
    if isinstance(spec, dict):                             # {"darwin": "...", "win32": "...", "default": "..."}
        platform = platform or sys.platform
        chosen = spec.get(platform) or spec.get("default")
        if chosen is None:
            raise ActionError(f"sin acción para {platform}: {spec}")
        return parse_action(chosen, platform)
    if not isinstance(spec, str) or not spec.strip():
        raise ActionError(f"acción no válida: {spec!r}")
    kind, _, arg = spec.partition(":")
    kind = kind.strip().lower()
    if kind in SIMPLE and not arg:
        return Action(kind, None, spec.strip())
    if kind == "key":
        return Action("key", parse_keys(arg, platform), spec)
    if kind == "scroll":
        direction, _, amount = arg.partition(":")
        if direction.strip() not in ("up", "down"):
            raise ActionError(f"scroll necesita up o down: '{spec}'")
        lines = int(amount) if amount.strip().isdigit() else 5
        return Action("scroll", lines if direction.strip() == "up" else -lines, spec)
    if kind == "mode":
        if arg.strip() not in MODES:
            raise ActionError(f"mode necesita {', '.join(MODES)}: '{spec}'")
        return Action("mode", arg.strip(), spec)
    if kind == "nav":
        if arg.strip() not in NAV:
            raise ActionError(f"nav necesita {', '.join(NAV)}: '{spec}'")
        return Action("nav", arg.strip(), spec)
    if kind == "media":
        if arg.strip() not in MEDIA:
            raise ActionError(f"media desconocido '{arg}' (usa: {', '.join(MEDIA)})")
        return Action("media", arg.strip(), spec)
    if kind in ("open", "text") and arg.strip() != "":
        return Action(kind, arg if kind == "text" else arg.strip(), spec)
    raise ActionError(f"acción desconocida: '{spec}'")


class ActionRunner:
    """Ejecuta acciones sobre un backend de entrada. `on_control` recibe 'pause'/'resume'/'toggle_pause'."""

    def __init__(self, backend, on_control=None, log=print):
        self.backend, self.on_control, self.log = backend, on_control, log

    def run(self, action):
        b = self.backend
        if action.kind == "key":
            b.key_combo(action.arg)
        elif action.kind == "click":
            b.click("left", 1)
        elif action.kind == "right_click":
            b.click("right", 1)
        elif action.kind == "double_click":
            b.click("left", 2)
        elif action.kind == "scroll":
            b.scroll(0, action.arg)
        elif action.kind == "media":
            b.media(action.arg)
        elif action.kind == "nav":
            b.nav(action.arg)
        elif action.kind == "open":
            b.open_target(action.arg)
        elif action.kind == "text":
            b.type_text(action.arg)
        elif action.kind in ("pause", "resume", "toggle_pause", "keyboard", "voice", "mode") and self.on_control:
            self.on_control(action.kind, action.arg)

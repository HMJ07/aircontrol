import os

import Quartz
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt


def screen_size():
    """Pantalla principal en puntos (las mismas coordenadas que usan los eventos de ratón)."""
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return int(bounds.size.width), int(bounds.size.height)


def active_app():
    """App con la ventana más al frente. Se lee de la lista de ventanas (ordenada de delante a atrás) porque
    NSWorkspace.frontmostApplication no se actualiza sin un bucle de eventos de Cocoa. No pide Grabación de
    pantalla (solo se usa el nombre del dueño, no el título). Se ignora nuestra propia ventana de vista previa."""
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    me = os.getpid()
    for win in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        if win.get("kCGWindowLayer") == 0 and win.get("kCGWindowOwnerPID") != me:
            return win.get("kCGWindowOwnerName") or ""
    return ""


def input_trusted(prompt=False):
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))

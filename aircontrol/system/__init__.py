"""Acceso al sistema operativo (pantalla, app en primer plano, permisos, ratón y teclado).
Cada función delega en macos.py o windows.py; en otros sistemas hay valores por defecto para poder desarrollar."""
import sys

if sys.platform == "darwin":
    from . import macos as _os
elif sys.platform == "win32":
    from . import windows as _os
else:
    _os = None

ACCESSIBILITY_HELP = (
    "AirControl necesita el permiso de Accesibilidad para mover el ratón y pulsar teclas.\n"
    "  1. Abre Ajustes del Sistema > Privacidad y seguridad > Accesibilidad.\n"
    "  2. Activa AirControl (o la Terminal / el editor desde el que lo lanzas, si lo ejecutas con Python).\n"
    "  3. Vuelve a abrir AirControl.\n"
    "Sin ese permiso macOS ignora en silencio los eventos de ratón y teclado."
)


def screen_size():
    if _os:
        return _os.screen_size()
    return (1920, 1080)


def active_app():
    """Nombre de la aplicación en primer plano ('' si no se sabe)."""
    try:
        return _os.active_app() if _os else ""
    except Exception:
        return ""


def input_trusted(prompt=False):
    """True si el SO deja a este proceso controlar ratón y teclado. En macOS es el permiso de Accesibilidad;
    con `prompt=True` se muestra el diálogo del sistema que lo solicita."""
    return _os.input_trusted(prompt) if _os and hasattr(_os, "input_trusted") else True


def open_target(target):
    import subprocess
    if sys.platform == "win32":
        import os
        os.startfile(target)                                   # noqa: S606  (aplicaciones y URLs)
        return
    is_url = "://" in target or target.startswith("www.")
    if sys.platform == "darwin":
        subprocess.Popen(["open", target] if is_url else ["open", "-a", target])
    else:
        subprocess.Popen(["xdg-open", target])


def focus_app(name):
    """Trae `name` al primer plano (sin permisos de Automatización: en macOS se usa `open -a`). Devuelve True si lo intentó.
    Sirve para que el teclado aéreo escriba en la app de destino aunque el foco haya quedado en AirControl."""
    if not name:
        return False
    try:
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", "-a", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform == "win32":
            return _os.focus_app(name)
    except Exception:
        return False
    return False


def beep():
    """Pitido corto no bloqueante: confirma un clic o una tecla cuando no se ve el cursor (mirada)."""
    try:
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            import threading
            import winsound
            threading.Thread(target=winsound.Beep, args=(1100, 35), daemon=True).start()
    except Exception:
        pass

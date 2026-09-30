import sys

from .config import IS_FROZEN, LOG_PATH

MAX_LOG_BYTES = 2_000_000


def setup_logging():
    """Sin consola (app empaquetada) print() no llega a ningún sitio y en Windows incluso falla si
    sys.stdout es None: se redirige todo a DATA_DIR/aircontrol.log."""
    import os
    if not (IS_FROZEN or sys.stdout is None or sys.stderr is None):
        return None
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            os.replace(LOG_PATH, str(LOG_PATH) + ".1")
        log = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
    except OSError:
        return None
    sys.stdout = sys.stderr = log
    return LOG_PATH


def show_error(title, message):
    """Diálogo nativo de error: una app de doble clic sin consola no puede 'imprimir' el problema."""
    print(f"❌ {message}")
    if not IS_FROZEN:
        return
    try:
        if sys.platform == "darwin":
            import json
            import subprocess
            script = (f'display dialog {json.dumps(message)} with title {json.dumps(title)} '
                      'buttons {"OK"} default button "OK" with icon stop')
            subprocess.run(["osascript", "-e", script], timeout=300)
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        pass

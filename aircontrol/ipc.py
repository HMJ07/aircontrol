"""Comunicación entre la app de la bandeja/ajustes y el motor (procesos distintos) mediante ficheros en DATA_DIR:
  control.json  órdenes [{"id": n, "cmd": "..."}] que escribe la app y lee el motor
  state.json    estado que publica el motor (pausa, modo, app en primer plano...) para el menú y la página de ajustes
Ficheros en vez de sockets: sin puertos ni firewalls, y fáciles de inspeccionar si algo falla."""
import json
import os
import threading
import time

from .config import DATA_DIR
from .fsutil import write_text_atomic

CONTROL_PATH = DATA_DIR / "control.json"
STATE_PATH = DATA_DIR / "state.json"
KEEP = 30
_send_lock = threading.Lock()     # la bandeja y la web envían órdenes desde hilos distintos


def write_json(path, obj):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def send_command(cmd, path=None):
    """Añade una orden para el motor. Devuelve su id."""
    path = path or CONTROL_PATH
    with _send_lock:                 # leer-añadir-guardar: sin el cerrojo dos órdenes simultáneas se perderían
        items = read_json(path, []) or []
        new_id = (max((i.get("id", 0) for i in items), default=0)) + 1
        items = (items + [{"id": new_id, "cmd": cmd, "t": time.time()}])[-KEEP:]
        write_json(path, items)
    return new_id


class CommandReader:
    """Lado del motor: entrega cada orden una sola vez. Las órdenes anteriores al arranque se ignoran."""

    def __init__(self, path=None):
        self.path = path or CONTROL_PATH
        items = read_json(self.path, []) or []
        self._last = max((i.get("id", 0) for i in items), default=0)

    def poll(self):
        items = read_json(self.path, []) or []
        fresh = [i for i in items if i.get("id", 0) > self._last]
        if fresh:
            self._last = max(i["id"] for i in fresh)
        return [i["cmd"] for i in sorted(fresh, key=lambda i: i["id"])]


class FileWatcher:
    """Detecta que un fichero cambió (por fecha de modificación); el primer `changed()` es False."""

    def __init__(self, *paths):
        self._seen = {p: self._stamp(p) for p in paths}

    @staticmethod
    def _stamp(path):
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def changed(self):
        """Lista de rutas que cambiaron desde la última comprobación."""
        out = []
        for path, old in self._seen.items():
            new = self._stamp(path)
            if new != old:
                self._seen[path] = new
                out.append(path)
        return out


def engine_state(path=None, max_age=3.0):
    """Estado publicado por el motor, o None si no hay motor en marcha (estado ausente o viejo)."""
    state = read_json(path or STATE_PATH)
    if not state or time.time() - state.get("updated", 0) > max_age:
        return None
    return state

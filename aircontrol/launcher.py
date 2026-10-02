"""App de bandeja: icono en la barra de menús (macOS) / área de notificación (Windows) + página de ajustes local.
El motor (cámara y vista previa) corre en un proceso aparte: así cada uno tiene su propio bucle de eventos (macOS exige
que la ventana de OpenCV y el menú vivan en el hilo principal de su proceso) y un fallo de uno no tumba al otro."""
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import config, ipc, system
from .actions import ActionError
from .config import APP_NAME, IS_FROZEN, ROOT_DIR, Settings
from .fsutil import write_text_atomic
from .gestures import RESERVED, GestureStore
from .profiles import DEFAULT_PROFILES, Profiles

WEB_DIR = Path(__file__).resolve().parent / "web"
NAME_RE = re.compile(r"^[a-z0-9_]{2,24}$")
ALLOWED_COMMANDS = {"pause", "resume", "toggle_pause", "keyboard", "voice", "mode:hand", "mode:gaze", "mode:toggle"}
LOCK_PATH = config.DATA_DIR / "app.lock"


def pid_alive(pid):
    if not pid or pid == os.getpid():
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259                                                 # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EngineProcess:
    """Arranca y para el motor (`aircontrol run`) como proceso hijo."""

    def __init__(self, argv_prefix=None):
        self.prefix = argv_prefix or ([sys.executable] if IS_FROZEN else [sys.executable, str(ROOT_DIR / "main.py")])
        self.proc = None
        self.args = ()                                  # con qué argumentos arrancó (p. ej. --remote)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, *args):
        if self.running():
            return False
        kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}     # CREATE_NO_WINDOW
        self.proc = subprocess.Popen([*self.prefix, "run", *args], **kwargs)
        self.args = tuple(args)
        return True

    def restart(self, *args):
        self.stop()
        return self.start(*args)

    def stop(self, timeout=4.0):
        if not self.running():
            return
        ipc.send_command("quit")
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            ipc.STATE_PATH.unlink()
        except OSError:
            pass

    def run_blocking(self, *args):
        """Ejecuta otro subcomando (train, calibrate) y espera a que termine."""
        kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        return subprocess.call([*self.prefix, *args], **kwargs)


class AppContext:
    """Estado compartido entre el menú de la bandeja y la página de ajustes."""

    def __init__(self, engine=None):
        self.engine = engine or EngineProcess()
        self.token = secrets.token_urlsafe(24)
        self.port = None
        self.busy = ""                                  # "training:ok" | "calibrating:gaze" ... (para mostrarlo)
        self._lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def state(self):
        return ipc.engine_state()

    # --- acciones (las usan menú y web) -----------------------------------------------------
    def send(self, cmd):
        if cmd not in ALLOWED_COMMANDS and cmd not in ("calibrate:hand", "calibrate:gaze"):
            raise ValueError(f"orden no permitida: {cmd}")
        if not self.engine.running():
            raise RuntimeError("El control no está en marcha")
        ipc.send_command(cmd)

    def run_exclusive(self, label, *args):
        """Para, ejecuta un subcomando que necesita la cámara (train/calibrate) y vuelve a arrancar el motor."""
        with self._lock:
            if self.busy:
                raise RuntimeError(f"Ocupado: {self.busy}")
            self.busy = label
        was_running = self.engine.running()

        def job():
            try:
                self.engine.stop()
                self.engine.run_blocking(*args)
            finally:
                if was_running:
                    self.engine.start()
                self.busy = ""
        threading.Thread(target=job, name="exclusive-job", daemon=True).start()

    def train(self, name, samples=15):
        if name in RESERVED or not NAME_RE.match(name):
            raise ValueError("Nombre no válido (2-24 letras minúsculas, números o _) o reservado")
        self.run_exclusive(f"entrenando '{name}'", "train", name, "--samples", str(samples), "--replace")

    def remote_info(self):
        """Estado del móvil como cámara según el motor: None si no está activado."""
        state = self.state()
        return (state or {}).get("remote")

    def set_remote(self, enable):
        """Activa/desactiva el móvil como cámara: el motor se reinicia con o sin `--remote` (el servidor vive en él)."""
        if self.busy:
            raise RuntimeError(f"Ocupado: {self.busy}")
        self.engine.restart(*(("--remote",) if enable else ()))

    def calibrate(self, what):
        if what not in ("hand", "gaze"):
            raise ValueError("calibración desconocida")
        if self.engine.running():
            self.send(f"calibrate:{what}")              # el propio motor tiene la cámara
        else:
            self.run_exclusive(f"calibrando {what}", "calibrate", what)


def create_web_app(ctx):
    from flask import Flask, abort, jsonify, make_response, redirect, request

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    COOKIE = "aircontrol_token"
    write_lock = threading.Lock()          # leer-modificar-guardar es una sección crítica: el servidor atiende en paralelo

    def authed():
        return secrets.compare_digest(request.cookies.get(COOKIE, ""), ctx.token)

    @app.before_request
    def guard():
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)                                                 # anti DNS-rebinding
        if request.path == "/":
            return None
        if not authed():
            abort(403)
        if request.method in ("POST", "DELETE") and not secrets.compare_digest(
                request.headers.get("X-Token", ""), ctx.token):
            abort(403)                                                 # anti CSRF

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "default-src 'self' 'unsafe-inline'"
        return resp

    @app.get("/")
    def index():
        given = request.args.get("t", "")
        if given and secrets.compare_digest(given, ctx.token):
            resp = make_response(redirect("/"))
            resp.set_cookie(COOKIE, ctx.token, httponly=True, samesite="Strict")
            return resp
        if not authed():
            return "Abre esta página desde el icono de AirControl (Ajustes…).", 403
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", ctx.token)
        return html

    @app.get("/api/state")
    def state():
        store = GestureStore(config.GESTURES_PATH)
        try:
            profiles = json.loads(config.PROFILES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            profiles = DEFAULT_PROFILES
        import importlib.util
        voice_installed = all(importlib.util.find_spec(m) for m in ("faster_whisper", "sounddevice"))   # sin importarlos
        return jsonify(engine=ctx.state(), running=ctx.engine.running(), busy=ctx.busy,
                       settings=Settings.load().__dict__, schema=Settings.describe(),
                       gestures={n: len(store.samples[n]) for n in store.names()},
                       builtin=[g for g in RESERVED if g != "fist"], profiles=profiles,
                       voice_installed=voice_installed, platform=sys.platform,
                       gaze_ready=config.GAZE_PATH.exists())

    @app.post("/api/settings")
    def set_settings():
        changes = request.get_json(force=True) or {}
        with write_lock:                   # dos sliders a la vez no deben pisarse entre sí
            settings = Settings.load()
            errors = settings.apply(changes)
            settings.save()
        return jsonify(errors=errors, settings=settings.__dict__), (400 if errors else 200)

    @app.post("/api/settings/reset")
    def reset_settings():
        try:
            config.SETTINGS_PATH.unlink()
        except OSError:
            pass
        return jsonify(settings=Settings().__dict__)

    @app.post("/api/profiles")
    def set_profiles():
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify(errors=["El perfil debe ser un objeto"]), 400
        errors = Profiles(data).errors
        if errors:
            return jsonify(errors=errors), 400
        write_text_atomic(config.PROFILES_PATH, json.dumps(data, indent=2, ensure_ascii=False))
        return jsonify(errors=[])

    @app.post("/api/profiles/reset")
    def reset_profiles():
        write_text_atomic(config.PROFILES_PATH, json.dumps(DEFAULT_PROFILES, indent=2, ensure_ascii=False))
        return jsonify(profiles=DEFAULT_PROFILES)

    @app.delete("/api/gestures/<name>")
    def delete_gesture(name):
        with write_lock:
            store = GestureStore(config.GESTURES_PATH)
            ok = store.remove(name)
            store.save()
        return jsonify(ok=ok), (200 if ok else 404)

    def guarded(fn):
        try:
            fn()
            return jsonify(ok=True)
        except (ValueError, RuntimeError, ActionError) as e:
            return jsonify(ok=False, error=str(e)), 409

    @app.post("/api/train")
    def train():
        body = request.get_json(force=True) or {}
        return guarded(lambda: ctx.train(str(body.get("name", "")).lower(), int(body.get("samples", 15))))

    @app.post("/api/calibrate")
    def calibrate():
        return guarded(lambda: ctx.calibrate((request.get_json(force=True) or {}).get("what", "")))

    @app.post("/api/command")
    def command():
        return guarded(lambda: ctx.send((request.get_json(force=True) or {}).get("cmd", "")))

    @app.post("/api/engine")
    def engine():
        action = (request.get_json(force=True) or {}).get("action")
        if action == "start":
            ctx.engine.start()
        elif action == "stop":
            ctx.engine.stop()
        elif action == "restart":
            ctx.engine.stop()
            ctx.engine.start()
        else:
            return jsonify(ok=False, error="acción desconocida"), 400
        return jsonify(ok=True)

    @app.get("/api/remote")
    def remote_state():
        from . import remote as rm
        info = ctx.remote_info()
        enabled = "--remote" in getattr(ctx.engine, "args", ())
        out = {"enabled": enabled, "running": ctx.engine.running(), "connected": bool((info or {}).get("connected"))}
        if info and info.get("url"):
            out["url"] = info["url"]
            out["svg"] = rm.qr_svg(info["url"], scale=5)
        return jsonify(out)

    @app.post("/api/remote")
    def remote_toggle():
        enable = bool((request.get_json(force=True) or {}).get("enable"))
        return guarded(lambda: ctx.set_remote(enable))

    @app.get("/api/ollama")
    def ollama_models():
        try:
            import ollama
            listing = ollama.Client(timeout=2).list()
            models = getattr(listing, "models", None) or listing.get("models", [])
            return jsonify(models=[getattr(m, "model", None) or m.get("model") for m in models])
        except Exception as e:
            return jsonify(models=[], error=f"{type(e).__name__}: {e}")

    return app


def serve(ctx):
    from werkzeug.serving import make_server
    ctx.port = free_port()
    server = make_server("127.0.0.1", ctx.port, create_web_app(ctx), threaded=True)
    threading.Thread(target=server.serve_forever, name="settings-web", daemon=True).start()
    return server


# --- bandeja -------------------------------------------------------------------------------------
def make_icon(kind):
    """Icono 64x64: flecha de cursor sobre un círculo; el color indica el estado."""
    from PIL import Image, ImageDraw
    colors = {"active": (46, 184, 92), "paused": (240, 170, 30), "stopped": (130, 130, 130), "busy": (60, 130, 240)}
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=colors.get(kind, colors["stopped"]))
    d.polygon([(22, 14), (22, 46), (30, 38), (36, 50), (42, 47), (36, 35), (47, 35)], fill=(255, 255, 255))
    return img


def icon_kind(ctx):
    if ctx.busy:
        return "busy"
    state = ctx.state()
    if not ctx.engine.running() or state is None:
        return "stopped"
    return "paused" if state.get("paused") else "active"


def run_app(start_engine=True, open_settings=False):
    """`aircontrol app`: bandeja + página de ajustes (+ motor)."""
    old = None
    try:
        old = int(LOCK_PATH.read_text())
    except (OSError, ValueError):
        pass
    if pid_alive(old):
        print(f"{APP_NAME} ya está en marcha (pid {old}); busca su icono en la barra de menús.")
        return 0
    LOCK_PATH.write_text(str(os.getpid()))

    import pystray
    ctx = AppContext()
    server = serve(ctx)
    if start_engine:
        ctx.engine.start()

    def item(text, fn, **kw):
        return pystray.MenuItem(text, lambda icon, _: fn(), **kw)

    def safe(fn):
        def run():
            try:
                fn()
            except Exception as e:
                print(f"⚠️  {e}")
        return run

    def status_text(_item=None):
        st = ctx.state()
        if ctx.busy:
            return f"Ocupado: {ctx.busy}"
        if not ctx.engine.running() or st is None:
            return "Control detenido"
        return "Control en pausa" if st.get("paused") else f"Control activo · {'mirada' if st.get('input_mode') == 'gaze' else 'mano'}"

    def toggle_engine():
        ctx.engine.stop() if ctx.engine.running() else ctx.engine.start()

    def st(key, default=None):
        s = ctx.state()
        return s.get(key, default) if s else default

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        item(lambda _i: "Detener control" if ctx.engine.running() else "Iniciar control", safe(toggle_engine)),
        item(lambda _i: "Reanudar" if st("paused") else "Pausar", safe(lambda: ctx.send("toggle_pause")),
             enabled=lambda _i: ctx.engine.running()),
        pystray.MenuItem("Puntero", pystray.Menu(
            pystray.MenuItem("Mano", lambda i, _: safe(lambda: ctx.send("mode:hand"))(),
                             checked=lambda _i: st("input_mode", "hand") == "hand", radio=True),
            pystray.MenuItem("Mirada", lambda i, _: safe(lambda: ctx.send("mode:gaze"))(),
                             checked=lambda _i: st("input_mode") == "gaze", radio=True)),
            enabled=lambda _i: ctx.engine.running()),
        item("Teclado aéreo", safe(lambda: ctx.send("keyboard")), checked=lambda _i: bool(st("keyboard")),
             enabled=lambda _i: ctx.engine.running()),
        item("Escuchar una orden de voz", safe(lambda: ctx.send("voice")), enabled=lambda _i: ctx.engine.running()),
        item("Usar el móvil como cámara", safe(lambda: (ctx.set_remote("--remote" not in ctx.engine.args), webbrowser.open(ctx.url))),
             checked=lambda _i: "--remote" in ctx.engine.args),
        pystray.MenuItem("Calibrar", pystray.Menu(
            item("La mano (esquinas)", safe(lambda: ctx.calibrate("hand"))),
            item("La mirada (9 puntos)", safe(lambda: ctx.calibrate("gaze"))))),
        pystray.Menu.SEPARATOR,
        item("Ajustes…", lambda: webbrowser.open(ctx.url), default=True),
        item("Salir", lambda: quit_app()),
    )
    icon = pystray.Icon(APP_NAME, make_icon("stopped"), APP_NAME, menu)

    def quit_app():
        ctx.engine.stop()
        server.shutdown()
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
        icon.stop()

    def refresher():
        last = None
        while True:
            time.sleep(1.0)
            kind = icon_kind(ctx)
            if kind != last:
                icon.icon, last = make_icon(kind), kind
            try:
                icon.update_menu()
            except Exception:
                pass

    def on_ready(icon):
        icon.visible = True
        threading.Thread(target=refresher, name="tray-refresh", daemon=True).start()
        if open_settings:
            webbrowser.open(ctx.url)

    print(f"{APP_NAME}: icono en la barra de menús · ajustes en {ctx.url}")
    try:
        icon.run(setup=on_ready)
    finally:
        ctx.engine.stop()
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
    return 0

"""El móvil como cámara y mando del ordenador (Android e iOS, sin instalar nada: una página web).

El móvil detecta la mano y la cara en SU cámara (MediaPipe en el navegador) y envía solo los puntos (unos cientos de
bytes por fotograma), nunca vídeo. Este módulo los recibe por HTTPS y se los da al motor como si vinieran de una
webcam. Como esto puede mover el ratón y pulsar teclas del ordenador, el acceso exige un token aleatorio que solo
aparece en el QR local, va cifrado (HTTPS), admite un único móvil a la vez y solo se activa si lo pides (`run --remote`)."""
import ipaddress
import json
import queue
import secrets
import socket
import ssl
import threading
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import geometry
from .gaze import CHEEK_L, CHEEK_R, CHIN, EYE_L, EYE_R, FOREHEAD, IRIS_L, IRIS_R, NOSE

# Puntos de la cara que usa la mirada (gaze.extract_features): el móvil envía solo estos, no los 478.
FACE_KEYS = sorted({IRIS_L, IRIS_R, NOSE, CHIN, FOREHEAD, CHEEK_L, CHEEK_R, *EYE_L.values(), *EYE_R.values()})
FACE_POINTS = 478
MAX_BODY = 64 * 1024
CLAIM_S = 5.0                         # un segundo móvil solo puede entrar si el primero lleva tanto sin enviar
ALLOWED_COMMANDS = {"pause", "resume", "toggle_pause", "keyboard", "voice", "mode:hand", "mode:gaze", "mode:toggle"}
WAITING = ("Esperando al movil...", "Escanea el QR de la consola o de Ajustes con el movil.")

Frame = namedtuple("Frame", "hand face aspect client_t")


def _points(raw, n, what):
    arr = np.asarray(raw, dtype=np.float64)
    if arr.shape != (n * 3,) or not np.isfinite(arr).all() or (np.abs(arr) > 5).any():
        raise ValueError(f"{what}: se esperaban {n * 3} números finitos")
    return arr.reshape(n, 3).astype(np.float32)


def parse_payload(data):
    """JSON del móvil -> Frame (con la imagen espejada, como una webcam de portátil). ValueError si no es válido.
    {"t": ms, "w": ancho, "h": alto, "hand": [63 números] | null, "face": {"índice": [x, y, z], ...} | null}"""
    if not isinstance(data, dict):
        raise ValueError("el cuerpo debe ser un objeto")
    w, h = data.get("w"), data.get("h")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 16 <= v <= 10000 for v in (w, h)):
        raise ValueError("w y h deben ser el tamaño del vídeo en píxeles")
    hand = face = None
    if data.get("hand") is not None:
        hand = _points(data["hand"], 21, "hand")
        hand[:, 0] = 1.0 - hand[:, 0]                              # la webcam del ordenador va espejada
    if data.get("face") is not None:
        raw = data["face"]
        if not isinstance(raw, dict) or not raw:
            raise ValueError("face debe ser un objeto {índice: [x, y, z]}")
        face = np.zeros((FACE_POINTS, 3), dtype=np.float32)
        for key, xyz in raw.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                raise ValueError(f"face: índice no válido {key!r}")
            if idx not in FACE_KEYS:
                raise ValueError(f"face: índice no permitido {idx}")
            face[idx] = _points(xyz, 1, "face")[0]
        if set(int(k) for k in raw) != set(FACE_KEYS):
            raise ValueError("face: faltan puntos")
        face[FACE_KEYS, 0] = 1.0 - face[FACE_KEYS, 0]               # espejo, solo en los puntos recibidos
    t = data.get("t")
    return Frame(hand, face, float(w) / float(h), float(t) if isinstance(t, (int, float)) else None)


class RemoteHub:
    """Último fotograma recibido + órdenes del móvil + estado para devolverle. Seguro entre hilos."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._cond = threading.Condition()
        self._seq = self._seen = 0
        self._frame = None
        self.client, self._client_at = None, float("-inf")
        self._commands = queue.Queue()
        self.status = {}                                           # lo que el ordenador le cuenta al móvil
        self.frames = 0

    def push(self, client_id, frame):
        """Guarda un fotograma. False si OTRO móvil está en uso (envió hace menos de CLAIM_S)."""
        now = self._clock()
        with self._cond:
            if self.client not in (None, client_id) and now - self._client_at < CLAIM_S:
                return False
            self.client, self._client_at = client_id, now
            self._frame, self._seq, self.frames = frame, self._seq + 1, self.frames + 1
            self._cond.notify_all()
        return True

    def wait_frame(self, timeout=0.5):
        """Espera a un fotograma nuevo (o a `timeout`) y lo devuelve; None si no llegó ninguno."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq != self._seen, timeout=timeout):
                return None
            self._seen = self._seq
            return self._frame

    def connected(self, within=2.0):
        return self._clock() - self._client_at < within

    def command(self, cmd):
        if cmd not in ALLOWED_COMMANDS:
            raise ValueError(f"orden no permitida: {cmd}")
        self._commands.put(cmd)

    def pop_commands(self):
        out = []
        while True:
            try:
                out.append(self._commands.get_nowait())
            except queue.Empty:
                return out


class RemoteCamera:
    """Hace de `Camera`: cada `read()` espera a un fotograma del móvil y devuelve un lienzo oscuro de su forma (el
    vídeo no sale del móvil); los landmarks vienen en `latest`."""
    remote = True

    def __init__(self, hub, width=640):
        self.hub, self.width, self.latest = hub, width, None

    def read(self):
        frame = self.hub.wait_frame(0.5)
        if frame is None:
            return False, None
        self.latest = frame
        return True, np.full((max(round(self.width / frame.aspect), 16), self.width, 3), 32, np.uint8)

    def release(self):
        pass


class RemoteHands:
    def __init__(self, camera):
        self.camera = camera

    def detect(self, frame_bgr, timestamp_ms):
        geometry.set_aspect(frame_bgr.shape[1], frame_bgr.shape[0])
        return self.camera.latest.hand if self.camera.latest else None


class RemoteFaces:
    def __init__(self, camera):
        self.camera = camera

    def detect(self, frame_bgr, timestamp_ms):
        return self.camera.latest.face if self.camera.latest else None


# --- HTTPS ---------------------------------------------------------------------------------------------------------------
def _port_is_free(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
        return True
    except OSError:
        return False


def lan_ip():
    """IP del ordenador en la red local (sin enviar nada: un UDP 'conectado' solo elige la interfaz)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def ensure_cert(directory, ip):
    """Certificado autofirmado (se reutiliza mientras valga para esta IP). Devuelve (cert.pem, key.pem)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path, meta_path = directory / "remote_cert.pem", directory / "remote_key.pem", directory / "remote_cert.json"
    hosts = sorted({"localhost", "127.0.0.1", ip, socket.gethostname()})
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("hosts") == hosts and cert_path.exists() and key_path.exists() \
                and meta.get("expires", 0) > time.time() + 30 * 86400:
            return cert_path, key_path
    except (OSError, ValueError):
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AirControl (local)")])
    san = []
    for h in hosts:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            san.append(x509.DNSName(h))
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=800))              # Apple no acepta certificados de más de 825 días
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    meta_path.write_text(json.dumps({"hosts": hosts, "expires": (now + timedelta(days=800)).timestamp()}), encoding="utf-8")
    return cert_path, key_path


def create_app(hub, token, web_dir, models_dir, info=None):
    from flask import Flask, abort, jsonify, redirect, request, send_from_directory

    app = Flask(__name__)
    info = info or {}
    web_dir, models_dir = Path(web_dir).resolve(), Path(models_dir).resolve()      # send_from_directory resolvería rutas relativas desde otra carpeta

    def authed():
        return secrets.compare_digest(request.headers.get("X-Token", ""), token)

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    def root():
        return redirect("/remote/")

    @app.get("/remote/")
    def page():
        return send_from_directory(web_dir, "index.html")

    @app.get("/remote/models/<name>")
    def model(name):
        if name not in ("hand_landmarker.task", "face_landmarker.task"):
            abort(404)
        return send_from_directory(models_dir, name)

    @app.get("/remote/<path:asset>")
    def asset(asset):
        return send_from_directory(web_dir, asset)

    @app.get("/api/hello")
    def hello():
        if not authed():
            abort(403)
        return jsonify(ok=True, face_keys=FACE_KEYS, **info)

    @app.post("/api/frame")
    def frame():
        if not authed():
            abort(403)
        if (request.content_length or 0) > MAX_BODY:
            abort(413)
        try:
            parsed = parse_payload(json.loads(request.get_data(cache=False)[:MAX_BODY].decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as e:
            return jsonify(ok=False, error=str(e)), 400
        if not hub.push(request.headers.get("X-Client", "?")[:64], parsed):
            return jsonify(ok=False, error="Otro móvil está conectado"), 409
        return jsonify(ok=True, **hub.status)

    @app.post("/api/command")
    def command():
        if not authed():
            abort(403)
        try:
            hub.command((request.get_json(force=True, silent=True) or {}).get("cmd", ""))
        except ValueError as e:
            return jsonify(ok=False, error=str(e)), 400
        return jsonify(ok=True)

    return app


class RemoteServer:
    def __init__(self, hub, data_dir, web_dir, models_dir, port=8443, host="0.0.0.0", token=None, ip=None, info=None):
        from werkzeug.serving import make_server
        self.hub, self.token, self.ip = hub, token or secrets.token_urlsafe(18), ip or lan_ip()
        cert, key = ensure_cert(data_dir, self.ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        app = create_app(hub, self.token, web_dir, models_dir, info)
        last_error = None
        for p in range(port, port + 10):                           # si el puerto está ocupado, prueba los siguientes
            if not _port_is_free(host, p):
                last_error = OSError(f"el puerto {p} está ocupado")
                continue
            try:
                self._server = make_server(host, p, app, threaded=True, ssl_context=ctx)
                self.port = p
                break
            except (OSError, SystemExit) as e:                    # werkzeug llama a sys.exit(1) si el puerto está ocupado
                last_error = e if isinstance(e, OSError) else OSError(f"no se pudo abrir el puerto {p}")
        else:
            raise last_error
        self._thread = threading.Thread(target=self._server.serve_forever, name="remote-server", daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"https://{self.ip}:{self.port}/remote/#t={self.token}"          # el token va en el fragmento: no se envía

    def stop(self):
        self._server.shutdown()
        self._thread.join(timeout=3)


def qr_svg(url, scale=6):
    import segno
    return segno.make(url, error="m").svg_inline(scale=scale, border=2, dark="#111", light="#fff")


def qr_terminal(url):
    import io

    import segno
    out = io.StringIO()
    segno.make(url, error="l").terminal(out=out, compact=True)
    return out.getvalue()

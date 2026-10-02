"""Motor: cámara -> manos/cara -> Controller -> ratón y teclado. Gestiona la ventana de vista previa, el teclado aéreo,
las calibraciones guiadas, la voz y la comunicación con la app de la bandeja (órdenes, estado y recarga de ajustes)."""
import os
import time

import cv2
import numpy as np

from . import config, ipc, system
from .actions import ActionError, parse_action
from .camera import CameraHealth
from .controller import Controller
from .fsutil import write_text_atomic
from .gaze import CLOSED_EAR, GazeModel, GazeCalibration, extract_features
from .gestures import GestureStore
from .handcal import CORNERS, HandCalibration
from .profiles import Profiles
from .runtime import show_error
from .ui import draw, draw_calibration, draw_debug, draw_keyboard, draw_notice

PREVIEW_SIZE = (480, 360)
RESULT_SECONDS = 3.5
PUBLISH_EVERY = 0.5
RELOAD_EVERY = 0.5
MAX_GAZE_RMS_OK = 250               # px de error medio por encima del cual se avisa de calibración poco fiable
MAX_GAZE_RMS_SAVE = 500             # por encima, la calibración se descarta (peor que no tener ninguna)


def keyboard_rect(screen):
    """Teclado acoplado abajo y centrado (sobre el Dock): (x, y, ancho, alto) en píxeles de pantalla."""
    sw, sh = screen
    w = min(int(sw * 0.8), 1400)
    h = int(w * 0.40)
    return ((sw - w) // 2, sh - h - 70, w, h)


class Engine:
    def __init__(self, dry_run=False, preview=True, camera_index=None, calibrate=None, exit_after_calibration=False,
                 camera=None, hands=None, faces=None, window=None, backend=None, voice=None, log=print, remote=None):
        self.dry_run, self.want_preview, self.camera_index = dry_run, preview, camera_index
        self.log = log
        self._calibrate_on_start, self.exit_after_calibration = calibrate, exit_after_calibration
        self.camera, self.hands, self.faces, self.window = camera, hands, faces, window
        self.remote = remote                       # True: servidor HTTPS para el móvil; o un RemoteHub ya creado (pruebas)
        self.hub, self._remote_server = None, None
        self.backend, self.voice = backend, voice
        self.cal = None
        self.debug = False
        self._quit = False
        self._start = time.monotonic()
        self._last_publish = self._last_reload = 0.0
        self._fps, self._last_frame = 0.0, None
        self._voice_key = None
        self._voice_warned = False
        self._camera_issue = None
        self._camera_health = None

    # --- arranque ----------------------------------------------------------------------------
    def setup(self):
        from .system.backend import create_backend
        self.settings = config.Settings.load()
        if self.camera_index is not None:
            self.settings.camera_index = self.camera_index
        if not self.dry_run and self.backend is None and not system.input_trusted(prompt=True):
            show_error(config.APP_NAME, system.ACCESSIBILITY_HELP)
            return False
        self.backend = self.backend or create_backend(self.dry_run)
        if self.remote and not self._setup_remote():
            return False
        self.store = GestureStore(config.GESTURES_PATH)
        self.profiles = Profiles.load(config.PROFILES_PATH)
        for err in self.profiles.errors:
            self.log(f"⚠️  {err}")
        if self.profiles.migrated:
            self.log("ℹ️  Perfiles de ejemplo actualizados (los anteriores cerraban pestañas con un gesto accidental).")
        try:
            if self.camera is None:
                from .camera import Camera
                self.camera = Camera(self.settings)
                if getattr(self.camera, "note", None):
                    self.log(f"📷 {self.camera.note}")
            if self.hands is None:
                from .hands import HandTracker
                self.hands = HandTracker()
        except Exception as e:
            show_error(config.APP_NAME, str(e))
            return False
        self.ctl = Controller(self.backend, self.settings, self.store, self.profiles, log=self.log,
                              gaze_model=self._load_gaze(), on_voice=self._voice_once, on_feedback=self._feedback)
        self.screen = self.backend.screen_size()
        self.kb_rect = keyboard_rect(self.screen)
        self.ctl.keyboard_rect = self.kb_rect
        if self.window is None and self.want_preview:
            from .window import Preview
            self.window = Preview(config.APP_NAME)
        self.watcher = ipc.FileWatcher(config.SETTINGS_PATH, config.PROFILES_PATH, config.GESTURES_PATH, config.GAZE_PATH)
        self.commands = ipc.CommandReader()
        self._sync_voice()
        if self._calibrate_on_start:
            self.start_calibration(self._calibrate_on_start)
        return True

    def _setup_remote(self):
        """El móvil hace de cámara: sus landmarks llegan por HTTPS y sustituyen a la webcam, la mano y la cara."""
        from pathlib import Path
        from . import __version__, remote as rm
        self.hub = self.remote if isinstance(self.remote, rm.RemoteHub) else rm.RemoteHub()
        if self.remote is True:
            try:
                self._remote_server = rm.RemoteServer(
                    self.hub, config.DATA_DIR, Path(__file__).resolve().parent / "web" / "remote", config.MODEL_PATH.parent,
                    port=self.settings.remote_port,
                    info={"name": __import__("socket").gethostname(), "screen": list(system.screen_size()), "version": __version__})
            except Exception as e:
                show_error(config.APP_NAME, f"No se pudo abrir el servidor para el móvil: {e}")
                return False
            self.log("📱 Móvil como cámara. Abre esta dirección en el móvil (misma Wi-Fi) o escanea el QR:")
            self.log(self._remote_server.url)
            try:
                self.log(rm.qr_terminal(self._remote_server.url))
            except Exception:
                pass
            self.log("   (el móvil avisará de que el certificado no es de confianza: es el de tu propio ordenador; acéptalo)")
        self.camera = self.camera or rm.RemoteCamera(self.hub)
        self.hands = self.hands or rm.RemoteHands(self.camera)
        self.faces = self.faces or rm.RemoteFaces(self.camera)
        return True

    @staticmethod
    def _load_gaze():
        try:
            return GazeModel.from_json(config.GAZE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            return None

    def _feedback(self, kind):
        if not self.dry_run:
            system.beep()

    # --- voz ---------------------------------------------------------------------------------
    def _sync_voice(self):
        s = self.settings
        if s.voice_mode == "off":
            if self.voice:
                self.voice.stop()
            return
        key = (s.whisper_model, s.voice_language, s.ollama_model)
        if self.voice is not None and self._voice_key not in (None, key):
            self.voice.stop()
            self.voice = None
        if self.voice is None:
            try:
                from .voice.listener import VoiceListener
                from .voice.llm import Interpreter
                from .voice.stt import Transcriber
                self.voice = VoiceListener(s, Transcriber(s.whisper_model, s.voice_language, log=self.log),
                                           Interpreter(s.ollama_model, log=self.log), log=self.log)
            except ImportError as e:
                if not self._voice_warned:
                    self.log(f"⚠️  Voz no disponible ({e}). Instala los extras: pip install -r requirements-voice.txt")
                    self._voice_warned = True
                return
        self._voice_key = key
        self.voice.cfg = s
        self.voice.start()

    def _voice_once(self):
        if self.settings.voice_mode == "off":
            self.log("⚠️  La voz está desactivada: actívala en Ajustes > Voz.")
        elif self.voice:
            self.voice.listen_once()

    def _drain_voice(self):
        item = self.voice.poll() if self.voice else None
        if item is None:
            return
        heard, spec = item
        try:
            self.ctl.runner.run(parse_action(spec))
            self.ctl.status.last_action = f"voz: {heard}"
            self.ctl._action_shown_until = time.monotonic() + 2
        except (ActionError, ValueError) as e:
            self.log(f"⚠️  Orden de voz no válida ({e})")

    # --- órdenes y recarga -------------------------------------------------------------------
    def command(self, cmd):
        if cmd in ("pause", "resume", "toggle_pause", "keyboard"):
            self.ctl._control(cmd)
        elif cmd.startswith("mode:"):
            self.ctl._control("mode", cmd[5:])
        elif cmd == "voice":
            self._voice_once()
        elif cmd in ("calibrate:hand", "calibrate:gaze"):
            self.start_calibration(cmd[10:])
        elif cmd == "reload":
            self.reload(self.watcher._seen.keys())
        elif cmd == "quit":
            self._quit = True

    def reload(self, paths):
        for path in paths:
            if path == config.SETTINGS_PATH:
                fresh = config.Settings.load()
                self.settings.__dict__.update(fresh.__dict__)        # en sitio: todos comparten el mismo objeto
                self.ctl.refresh_settings()
                self._sync_voice()
            elif path == config.PROFILES_PATH:
                self.ctl.profiles = Profiles.load(config.PROFILES_PATH)
                for err in self.ctl.profiles.errors:
                    self.log(f"⚠️  {err}")
            elif path == config.GESTURES_PATH:
                self.store.load()
            elif path == config.GAZE_PATH:
                self.ctl.set_gaze_model(self._load_gaze())

    # --- calibración -------------------------------------------------------------------------
    def start_calibration(self, kind):
        if kind == "gaze" and self.faces is None:
            from .faces import FaceTracker
            self.faces = FaceTracker()
        obj = HandCalibration() if kind == "hand" else GazeCalibration(self.screen)
        self.cal = {"kind": kind, "obj": obj, "done_at": None, "message": ""}
        self.ctl.set_keyboard(False)
        self.ctl.shutdown(time.monotonic())                          # suelta botones: durante la calibración no se controla nada
        if self.window:
            self.window.set_fullscreen(True)
        self.log(f"🎯 Calibración de {'la mano' if kind == 'hand' else 'la mirada'} iniciada (Esc cancela)")

    def _finish_calibration(self, cal, now):
        obj = cal["obj"]
        if obj.error:
            cal["message"] = obj.error
        elif cal["kind"] == "hand":
            fresh = config.Settings.load()                         # sobre lo que hay en disco: no pisar lo que cambió la web
            errors = fresh.apply(obj.region)
            fresh.save()
            self.settings.apply(obj.region)
            cal["message"] = "Región de la mano guardada" if not errors else "No se pudo guardar: " + "; ".join(errors)
        else:
            rms = obj.model.rms
            try:                                                   # datos crudos: permiten diagnosticar sin repetir nada
                np.savez_compressed(config.GAZE_SAMPLES_PATH, feats=np.array(obj.feats), targets=np.array(obj.targets),
                                    groups=np.array(obj.groups), ears=np.array(obj.ears), screen=np.array(self.screen))
            except OSError:
                pass
            missing = obj.missing
            if missing:
                self.log(f"⚠️  Puntos sin muestras suficientes: {[m + 1 for m in missing]} (¿se cierran los ojos al mirar abajo? "
                         "sube un poco la pantalla o aléjate)")
            if rms is not None and rms > MAX_GAZE_RMS_SAVE:
                cal["message"] = (f"Calibración descartada: error medio ≈ {rms:.0f} px (se guardaría algo peor que nada). "
                                  "Repite con buena luz, la cara de frente a la cámara y mirando fijo cada punto sin mover la cabeza.")
            else:
                write_text_atomic(config.GAZE_PATH, obj.model.to_json())
                self.ctl.set_gaze_model(obj.model)
                cal["message"] = (f"Mirada calibrada · error medio ≈ {rms:.0f} px" if rms is not None else "Mirada calibrada")
                if missing:
                    cal["message"] += f" · sin datos en los puntos {[m + 1 for m in missing]}: la parte de pantalla cercana será imprecisa"
                if rms is not None and rms > MAX_GAZE_RMS_OK:
                    cal["message"] += " (poco fiable: repite con buena luz y la cabeza centrada)"
        cal["done_at"] = now
        bad = obj.error or cal["message"].startswith(("Calibración descartada", "No se pudo"))
        self.log(("⚠️  " if bad else "🎯 ") + cal["message"])

    def _calibration_frame(self, frame, hand, face, aspect, now):
        cal = self.cal
        obj, kind = cal["obj"], cal["kind"]
        sw, sh = self.screen
        if cal["done_at"] is None:
            if kind == "hand":
                done = obj.update(hand, now)
                _, cx, cy = CORNERS[min(obj.index, len(CORNERS) - 1)]
                target = (60 + cx * (sw - 121), 60 + cy * (sh - 121))
                title, sub = f"Apunta con el dedo a la esquina {obj.prompt}", "Mantén el dedo quieto sobre la esquina"
                progress, phase, ok = obj.progress(now), "collect", hand is not None
            else:
                feats, ear = extract_features(face, aspect) if face is not None else (None, None)
                done = obj.update(feats, ear, now, eyes_open=ear is not None and ear > CLOSED_EAR)
                idx, total, phase, progress = obj.progress(now)
                target = obj.target_px()
                title = "Mira fijamente el punto"
                sub = f"Punto {min(idx + 1, total)} de {total} · cabeza quieta y de frente" + \
                      (" · repitiendo: no te vi bien los ojos, mira el punto con los ojos abiertos" if obj.retrying() else "")
                ok = face is not None
            if done:
                self._finish_calibration(cal, now)
        if cal["done_at"] is not None:
            title, sub, target, progress, phase = cal["message"], "", None, 0.0, "settle"
            ok = True
            if now - cal["done_at"] > RESULT_SECONDS:
                self._end_calibration()
                return None
        return draw_calibration((sw, sh), title, sub, target, progress, phase, camera=frame, face_ok=ok,
                                footer="Esc para cancelar")

    def _end_calibration(self):
        self.cal = None
        if self.window:
            self.window.set_fullscreen(False)
        if self.exit_after_calibration:
            self._quit = True

    # --- bucle -------------------------------------------------------------------------------
    def step(self, frame, now):
        """Procesa un fotograma y devuelve la imagen a mostrar."""
        ts = (now - self._start) * 1000
        hand = self.hands.detect(frame, ts)
        need_face = (self.cal is not None and self.cal["kind"] == "gaze") or \
                    (self.ctl.input_mode == "gaze" and self.ctl.gaze is not None)
        if need_face and self.faces is None:
            from .faces import FaceTracker
            self.faces = FaceTracker()
        face = self.faces.detect(frame, ts) if need_face else None
        aspect = frame.shape[1] / frame.shape[0]
        if self.cal is not None:
            self._tell_phone()
            return self._calibration_frame(frame, hand, face, aspect, now), hand
        status = self.ctl.process(hand, now, face, aspect)
        self._tell_phone()
        self._drain_voice()
        return self._render(frame, hand, status, now), hand

    def _tell_phone(self):
        """Lo que la página del móvil necesita saber: si hay que enviar la cara (mirada), si está en pausa, etc."""
        if self.hub is None:
            return
        st = self.ctl.status
        self.hub.status = {"paused": self.ctl.paused, "input_mode": self.ctl.input_mode, "keyboard": self.ctl.keyboard is not None,
                           "mode": st.mode, "last_action": st.last_action, "calibrating": self.cal["kind"] if self.cal else None,
                           "need_face": (self.ctl.input_mode == "gaze" and self.ctl.gaze is not None)
                                        or bool(self.cal and self.cal["kind"] == "gaze")}

    def _render(self, frame, hand, status, now):
        if self.ctl.keyboard is not None:
            x, y, w, h = self.kb_rect
            if self.window:
                self.window.set_geometry(x, y, w, h)
            return draw_keyboard(self.ctl.keyboard, self.ctl.kb_uv, now, size=(w, h))
        if self.window:
            ph = round(PREVIEW_SIZE[0] * frame.shape[0] / frame.shape[1])              # respeta 4:3 o 16:9
            self.window.set_geometry(self.screen[0] - PREVIEW_SIZE[0] - 20, 40, PREVIEW_SIZE[0], ph)
        img = draw(frame, hand, status, self.settings)
        if self.debug:
            draw_debug(img, self.ctl.mouse, status)
        if self.voice:
            cv2.putText(img, f"VOZ: {self.voice.state}", (img.shape[1] - 150, img.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
        return img

    def _publish(self, now, status):
        if now - self._last_publish < PUBLISH_EVERY:
            return
        self._last_publish = now
        ipc.write_json(ipc.STATE_PATH, {
            "pid": os.getpid(), "updated": time.time(), "paused": self.ctl.paused, "mode": status.mode,
            "input_mode": self.ctl.input_mode, "keyboard": self.ctl.keyboard is not None,
            "voice_mode": self.settings.voice_mode, "voice_state": self.voice.state if self.voice else "idle",
            "app": status.app, "profile": status.profile, "gaze_ready": self.ctl.gaze is not None,
            "calibrating": self.cal["kind"] if self.cal else None, "dry_run": self.dry_run,
            "remote": ({"url": self._remote_server.url if self._remote_server else None, "connected": self.hub.connected()}
                       if self.hub is not None else None),
            "fps": round(self._fps, 1)})

    def run(self, max_frames=None):
        if not self.setup():
            return 1
        self.log(f"{config.APP_NAME} · {'SIMULACIÓN (no se toca el ratón)' if self.dry_run else 'activo'} · "
                 f"gestos propios: {', '.join(self.store.names()) or 'ninguno'}")
        frames = 0
        try:
            while not self._quit and (max_frames is None or frames < max_frames):
                ok, frame = self.camera.read()
                self._check_camera(ok, frame)
                if not ok:
                    # Sin fotogramas la ventana sigue atendiendo eventos (si no, Windows la marca "no responde").
                    if self._pump_window(self._blank() if self._camera_issue else None):
                        break
                    time.sleep(0.01)
                    frames += 1
                    continue
                now = time.monotonic()
                img, hand = self.step(frame, now)
                frames += 1
                dt = now - self._last_frame if self._last_frame else 0
                self._last_frame = now
                if dt > 0:
                    self._fps = 0.9 * self._fps + 0.1 / dt if self._fps else 1 / dt
                if self.hub is not None:
                    for cmd in self.hub.pop_commands():             # botones de la página del móvil
                        self.command(cmd)
                if now - self._last_reload >= RELOAD_EVERY:
                    self._last_reload = now
                    changed = self.watcher.changed()
                    if changed:
                        self.reload(changed)
                    for cmd in self.commands.poll():
                        self.command(cmd)
                self._publish(now, self.ctl.status)
                if self._camera_issue and img is not None:
                    draw_notice(img, self._camera_issue)
                if self._pump_window(img):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
        return 0

    def _check_camera(self, ok, frame):
        """Avisa (log + franja en la vista previa) si la cámara no da imagen útil; se limpia sola al recuperarse."""
        if self.hub is not None:                                    # con el móvil no hay "cámara negra": hay "móvil sin conectar"
            from .remote import WAITING
            issue = None if self.hub.connected() else WAITING
            if issue != self._camera_issue:
                self._camera_issue = issue
                self.log("📱 Esperando al móvil..." if issue else "📱 Móvil conectado.")
            return
        if self._camera_health is None:
            self._camera_health = CameraHealth()
        issue = self._camera_health.update(ok, frame, time.monotonic())
        if issue != self._camera_issue:
            self._camera_issue = issue
            self.log(f"⚠️  {' '.join(issue)}" if issue else "✅ La cámara vuelve a dar imagen.")

    def _blank(self):
        key = (self._camera_issue, self.settings.frame_width, self.settings.frame_height)
        if getattr(self, "_blank_key", None) != key:
            self._blank_key = key
            self._blank_img = draw_notice(np.zeros((key[2], key[1], 3), np.uint8), self._camera_issue)
        return self._blank_img

    def _pump_window(self, img):
        """Muestra `img` (si hay) y atiende teclado/ventana. Devuelve True si hay que salir."""
        if not self.window:
            return False
        if img is not None:
            self.window.show(img)
        key = self.window.key()
        if key == 27 and self.cal is not None:
            self._end_calibration()
        elif key in (ord("q"), 27) or self.window.closed():
            self.log("ℹ️  Ventana de vista previa cerrada: el control se detiene.")
            return True
        elif key == ord("d"):
            self.debug = not self.debug
        elif key == ord("k"):
            self.ctl._control("keyboard")
        return False

    def shutdown(self):
        try:
            self.ctl.shutdown(time.monotonic())
        except Exception:
            pass
        if self.voice:
            self.voice.stop()
        if self._remote_server:
            self._remote_server.stop()
        if self.camera:
            self.camera.release()
        if self.window:
            try:
                self.window.close()
            except Exception:
                pass
        try:
            ipc.STATE_PATH.unlink()
        except OSError:
            pass

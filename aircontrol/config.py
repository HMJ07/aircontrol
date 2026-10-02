import json
import numbers
import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from .fsutil import write_text_atomic

APP_NAME = "AirControl"
ROOT_DIR = Path(__file__).resolve().parent.parent
IS_FROZEN = getattr(sys, "frozen", False)                  # True dentro del instalador (PyInstaller)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT_DIR))    # modelos empaquetados (solo lectura)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")


def _user_data_dir():
    """Carpeta de datos del usuario (gestos entrenados, perfiles, ajustes, registro)."""
    override = os.environ.get("AIRCONTROL_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if not IS_FROZEN:
        return ROOT_DIR / "data"                            # en desarrollo todo queda en el proyecto
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


DATA_DIR = _user_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                  "face_landmarker/float16/1/face_landmarker.task")
MODEL_PATH = RESOURCE_DIR / "models" / "hand_landmarker.task"
FACE_MODEL_PATH = RESOURCE_DIR / "models" / "face_landmarker.task"
GAZE_PATH = DATA_DIR / "gaze.json"
GAZE_SAMPLES_PATH = DATA_DIR / "gaze_samples.npz"      # datos crudos de la última calibración (para diagnosticar)
GESTURES_PATH = DATA_DIR / "gestures.json"
PROFILES_PATH = DATA_DIR / "profiles.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "aircontrol.log"


@dataclass
class Settings:
    """Ajustes editables en DATA_DIR/settings.json (solo hace falta poner las claves que cambian)."""
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    # Región de la imagen de la cámara que cubre toda la pantalla (x0, y0, x1, y1 normalizados): así no hay
    # que estirar el brazo. `aircontrol calibrate hand` la ajusta a tu alcance.
    region_x0: float = 0.16
    region_y0: float = 0.20
    region_x1: float = 0.84
    region_y1: float = 0.80
    # Suavizado One-Euro (en píxeles de pantalla): menos temblor parado, poca latencia en movimiento.
    smooth_min_cutoff: float = 1.0
    smooth_beta: float = 0.012
    # Pellizco (distancia pulgar-dedo / tamaño de la mano): se activa por debajo de ON y se suelta por encima de OFF.
    pinch_on: float = 0.30
    pinch_off: float = 0.45
    drag_deadzone_px: float = 60.0
    drag_hold_s: float = 0.50
    double_click_s: float = 0.50
    scroll_gain: float = 25.0          # líneas de scroll por cada "tamaño de mano" que se mueve
    scroll_natural: bool = True
    # ✌️ solo cuenta si índice y corazón están claramente extendidos (ratio) y se mantiene `scroll_enter_s`.
    scroll_extension: float = 1.15
    scroll_enter_s: float = 0.25
    # Mirada
    gaze_min_cutoff: float = 0.6
    gaze_beta: float = 0.004
    gaze_click: str = "dwell"            # dwell | blink | pinch | off
    dwell_s: float = 1.0
    dwell_radius_px: float = 70.0
    # Teclado aéreo
    keyboard_dwell_s: float = 0.9
    # Móvil como cámara
    remote_port: int = 8443
    # Voz
    voice_mode: str = "off"              # off | push (gesto/menú) | wake (palabra de activación)
    voice_wake_word: str = "control"
    voice_language: str = "es"
    whisper_model: str = "base"
    ollama_model: str = ""               # vacío = el primero instalado
    # Gestos
    gesture_threshold: float = 0.65
    gesture_hold_s: float = 0.50
    pause_hold_s: float = 1.20
    swipe_distance: float = 0.30

    @classmethod
    def describe(cls):
        """Metadatos de cada ajuste para la interfaz: nombre, tipo, valor por defecto, límites, opciones y etiqueta."""
        out = []
        for f in fields(cls):
            default = getattr(cls(), f.name)
            label, group = META.get(f.name, (f.name, "Otros"))
            item = {"name": f.name, "type": type(default).__name__, "default": default, "label": label,
                    "group": group}
            if f.name in CHOICES:
                item["choices"] = CHOICES[f.name]
            if f.name in LIMITS:
                item["min"], item["max"] = LIMITS[f.name]
            out.append(item)
        return out

    def apply(self, changes):
        """Aplica {ajuste: valor} validando tipo, rango y opciones. Devuelve la lista de errores (los válidos se aplican)."""
        errors = []
        for name, value in changes.items():
            if name not in self.__dict__:
                errors.append(f"{name}: ajuste desconocido")
                continue
            default = getattr(type(self)(), name)
            kind = type(default)
            if kind is bool:
                ok = isinstance(value, bool)
            elif kind is int:
                ok = isinstance(value, numbers.Real) and not isinstance(value, bool) and float(value).is_integer()
                value = int(value) if ok else value
            elif kind is float:
                ok = isinstance(value, numbers.Real) and not isinstance(value, bool)
                value = float(value) if ok else value
            else:
                ok = isinstance(value, str)
            if not ok:
                errors.append(f"{name}: se esperaba {kind.__name__}")
                continue
            if name in CHOICES and value not in CHOICES[name]:
                errors.append(f"{name}: usa uno de {', '.join(CHOICES[name])}")
                continue
            if name in LIMITS and not LIMITS[name][0] <= value <= LIMITS[name][1]:
                errors.append(f"{name}: debe estar entre {LIMITS[name][0]} y {LIMITS[name][1]}")
                continue
            setattr(self, name, value)
        return errors

    def save(self):
        """Guarda en settings.json solo lo que difiere de los valores por defecto."""
        defaults = type(self)()
        data = {k: v for k, v in self.__dict__.items() if v != getattr(defaults, k)}
        write_text_atomic(SETTINGS_PATH, json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls):
        settings = cls()
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if isinstance(data, dict):
            settings.apply(data)
        return settings


CHOICES = {"gaze_click": ["dwell", "blink", "pinch", "off"], "voice_mode": ["off", "push", "wake"],
           "voice_language": ["es", "en"], "whisper_model": ["tiny", "base", "small"]}
LIMITS = {
    "camera_index": (0, 9), "frame_width": (320, 1920), "frame_height": (240, 1080),
    "region_x0": (0.0, 0.45), "region_y0": (0.0, 0.45), "region_x1": (0.55, 1.0), "region_y1": (0.55, 1.0),
    "smooth_min_cutoff": (0.1, 5.0), "smooth_beta": (0.0, 0.2),
    "pinch_on": (0.05, 0.6), "pinch_off": (0.1, 0.9), "drag_deadzone_px": (5.0, 200.0), "drag_hold_s": (0.1, 3.0),
    "double_click_s": (0.2, 1.5), "scroll_gain": (2.0, 100.0), "scroll_extension": (1.0, 1.8),
    "scroll_enter_s": (0.0, 1.5),
    "gaze_min_cutoff": (0.05, 5.0), "gaze_beta": (0.0, 0.2), "dwell_s": (0.3, 4.0), "dwell_radius_px": (20.0, 250.0),
    "keyboard_dwell_s": (0.3, 3.0), "remote_port": (1024, 65535),
    "gesture_threshold": (0.2, 1.5), "gesture_hold_s": (0.2, 3.0), "pause_hold_s": (0.5, 4.0),
    "swipe_distance": (0.1, 0.8),
}
META = {
    "camera_index": ("Cámara (0 = la principal)", "Cámara"),
    "frame_width": ("Ancho de imagen", "Cámara"), "frame_height": ("Alto de imagen", "Cámara"),
    "region_x0": ("Región de la mano: izquierda", "Ratón con la mano"),
    "region_y0": ("Región de la mano: arriba", "Ratón con la mano"),
    "region_x1": ("Región de la mano: derecha", "Ratón con la mano"),
    "region_y1": ("Región de la mano: abajo", "Ratón con la mano"),
    "smooth_min_cutoff": ("Suavizado con la mano quieta (menos = más suave)", "Ratón con la mano"),
    "smooth_beta": ("Reactividad al moverse rápido", "Ratón con la mano"),
    "pinch_on": ("Pellizco: distancia para activar", "Ratón con la mano"),
    "pinch_off": ("Pellizco: distancia para soltar", "Ratón con la mano"),
    "drag_deadzone_px": ("Arrastre: píxeles antes de empezar", "Ratón con la mano"),
    "drag_hold_s": ("Arrastre: segundos manteniendo el pellizco", "Ratón con la mano"),
    "double_click_s": ("Doble clic: tiempo máximo entre clics", "Ratón con la mano"),
    "scroll_gain": ("Velocidad del scroll", "Scroll"), "scroll_natural": ("Scroll natural (el contenido sigue a la mano)", "Scroll"),
    "scroll_extension": ("✌️: cuánto hay que extender los dedos", "Scroll"),
    "scroll_enter_s": ("✌️: segundos para activar el scroll", "Scroll"),
    "gaze_min_cutoff": ("Suavizado de la mirada (menos = más suave)", "Mirada"),
    "gaze_beta": ("Reactividad de la mirada", "Mirada"), "gaze_click": ("Cómo hacer clic con la mirada", "Mirada"),
    "dwell_s": ("Clic por permanencia: segundos", "Mirada"), "dwell_radius_px": ("Clic por permanencia: radio (px)", "Mirada"),
    "keyboard_dwell_s": ("Teclado: segundos para pulsar una tecla con la mirada", "Teclado aéreo"),
    "remote_port": ("Puerto HTTPS del móvil como cámara", "Móvil"),
    "voice_mode": ("Activación de la voz", "Voz"), "voice_wake_word": ("Palabra de activación", "Voz"),
    "voice_language": ("Idioma", "Voz"), "whisper_model": ("Modelo de reconocimiento (tiny=rápido, small=preciso)", "Voz"),
    "ollama_model": ("Modelo de Ollama (vacío = el primero instalado)", "Voz"),
    "gesture_threshold": ("Gestos propios: tolerancia", "Gestos"), "gesture_hold_s": ("Gestos: segundos manteniendo", "Gestos"),
    "pause_hold_s": ("Puño para pausar: segundos", "Gestos"), "swipe_distance": ("Deslizar: distancia mínima", "Gestos"),
}

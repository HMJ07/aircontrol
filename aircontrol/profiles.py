"""Perfiles por aplicación: el mismo gesto hace cosas distintas según la app en primer plano."""
import json

from .actions import ActionError, parse_action
from .fsutil import write_text_atomic

DEFAULT_PROFILES = {
    "version": 2,
    "comment": "Cada perfil se activa si el nombre de la app en primer plano contiene alguna de sus palabras "
               "'match'. Las acciones de 'default' valen en cualquier app que no las redefina. "
               "Gestos: swipe_left/right/up/down, thumbs_up, pinky_up y los que entrenes con `aircontrol train`. "
               "Los ejemplos evitan acciones destructivas (cerrar pestañas, salir): un gesto accidental no debe costar nada.",
    "default": {
        "thumbs_up": "media:play_pause",
        "swipe_up": "media:volume_up",
        "swipe_down": "media:volume_down",
    },
    "profiles": [
        {"name": "Navegador",
         "match": ["safari", "chrome", "firefox", "edge", "brave", "arc", "opera"],
         "bindings": {
             "swipe_left": {"darwin": "key:mod+[", "default": "key:alt+left"},
             "swipe_right": {"darwin": "key:mod+]", "default": "key:alt+right"},
             "swipe_up": "scroll:up:15",
             "swipe_down": "scroll:down:15"}},
        {"name": "Presentaciones",
         "match": ["powerpoint", "keynote", "impress", "slides"],
         "bindings": {"swipe_left": "key:left", "swipe_right": "key:right", "swipe_up": "key:home",
                      "thumbs_up": "key:f5"}},
        {"name": "Vídeo",
         "match": ["vlc", "quicktime", "mpv", "iina", "netflix", "spotify", "music", "movies"],
         "bindings": {"thumbs_up": "key:space", "swipe_left": "key:left", "swipe_right": "key:right"}},
    ],
}

# Ejemplos de versiones anteriores. Si profiles.json es idéntico a uno, nunca se tocó: se actualiza solo. Los ejemplos
# v1 tenían swipe_down = cerrar pestaña y thumbs_up = ir a la barra de direcciones, que se disparaban sin querer.
LEGACY_DEFAULTS = [json.loads(r'''{"version": 1, "comment": "Cada perfil se activa si el nombre de la app en primer plano contiene alguna de sus palabras 'match'. Las acciones de 'default' valen en cualquier app que no las redefina. Gestos: swipe_left/right/up/down, thumbs_up y los que entrenes con `aircontrol train`.", "default": {"thumbs_up": "media:play_pause", "swipe_up": "media:volume_up", "swipe_down": "media:volume_down"}, "profiles": [{"name": "Navegador", "match": ["safari", "chrome", "firefox", "edge", "brave", "arc", "opera"], "bindings": {"swipe_left": {"darwin": "key:mod+[", "default": "key:alt+left"}, "swipe_right": {"darwin": "key:mod+]", "default": "key:alt+right"}, "swipe_up": "key:mod+r", "swipe_down": "key:mod+w", "thumbs_up": "key:mod+l"}}, {"name": "Presentaciones", "match": ["powerpoint", "keynote", "impress", "slides"], "bindings": {"swipe_left": "key:left", "swipe_right": "key:right", "swipe_up": "key:home", "swipe_down": "key:esc", "thumbs_up": "key:f5"}}, {"name": "Vídeo", "match": ["vlc", "quicktime", "mpv", "iina", "netflix", "spotify", "music", "movies"], "bindings": {"thumbs_up": "key:space", "swipe_left": "key:left", "swipe_right": "key:right"}}]}''')]

# Acciones que existen aunque el perfil del usuario (creado con una versión anterior) no las mencione. Un perfil las puede
# redefinir; no las borra. 🤙 abre y cierra el teclado aéreo: así siempre hay una forma de llegar a él con solo la mano.
FALLBACKS = {"pinky_up": "keyboard"}


class Profiles:
    def __init__(self, data=None):
        self.data = data or DEFAULT_PROFILES
        self.errors = []
        self.migrated = False
        self._default = self._compile(self.data.get("default", {}), "default")
        self._profiles = []
        for p in self.data.get("profiles", []):
            match = [m.lower() for m in p.get("match", [])]
            self._profiles.append((p.get("name", "?"), match, self._compile(p.get("bindings", {}), p.get("name", "?"))))

    def _compile(self, bindings, owner):
        out = {}
        for gesture, spec in bindings.items():
            try:
                out[gesture] = parse_action(spec)
            except ActionError as e:
                self.errors.append(f"perfil '{owner}', gesto '{gesture}': {e}")
        return out

    @classmethod
    def load(cls, path):
        """Lee profiles.json; si no existe lo crea con los perfiles de ejemplo."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data in LEGACY_DEFAULTS:                              # nunca se editó: pasa a los ejemplos nuevos
                write_text_atomic(path, json.dumps(DEFAULT_PROFILES, indent=2, ensure_ascii=False))
                profiles = cls()
                profiles.migrated = True
                return profiles
            return cls(data)
        except FileNotFoundError:
            write_text_atomic(path, json.dumps(DEFAULT_PROFILES, indent=2, ensure_ascii=False))
            return cls()
        except (OSError, ValueError) as e:
            profiles = cls()
            profiles.errors.append(f"profiles.json no se pudo leer ({e}); se usan los perfiles de ejemplo")
            return profiles

    def profile_name(self, app_name):
        app = (app_name or "").lower()
        for name, match, _ in self._profiles:
            if any(m in app for m in match):
                return name
        return "default"

    def resolve(self, gesture, app_name):
        """Acción para `gesture` en `app_name`: la del perfil de la app o, si no la define, la de 'default'."""
        app = (app_name or "").lower()
        for _, match, bindings in self._profiles:
            if any(m in app for m in match) and gesture in bindings:
                return bindings[gesture]
        if gesture in self._default:
            return self._default[gesture]
        return parse_action(FALLBACKS[gesture]) if gesture in FALLBACKS else None

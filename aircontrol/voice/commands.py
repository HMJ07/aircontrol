"""Órdenes de voz por reglas (español e inglés): instantáneas, sin modelo de lenguaje. Devuelve una acción en el
mismo formato de texto que los perfiles ("key:mod+c", "click"...) o None si no la reconoce."""
import difflib
import re
import unicodedata


def normalize(text):
    """Minúsculas, sin tildes ni puntuación: 'Abre  Safari.' -> 'abre safari'."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


BACK = {"darwin": "key:mod+[", "default": "key:alt+left"}
FORWARD = {"darwin": "key:mod+]", "default": "key:alt+right"}

# (patrones exactos o frases que deben aparecer) -> acción. Se comprueban por orden: lo más específico primero.
EXACT = [
    (("doble clic", "doble click", "double click", "haz doble clic"), "double_click"),
    (("clic derecho", "click derecho", "right click", "boton derecho"), "right_click"),
    (("clic", "click", "haz clic", "haz click", "pulsa", "pincha", "selecciona", "pulsar"), "click"),
    (("baja", "bajar", "abajo", "scroll abajo", "desplaza abajo", "baja la pagina", "scroll down", "down"), "scroll:down:12"),
    (("sube", "subir", "arriba", "scroll arriba", "desplaza arriba", "sube la pagina", "scroll up", "up"), "scroll:up:12"),
    (("copia", "copiar", "copy"), "key:mod+c"),
    (("pega", "pegar", "paste"), "key:mod+v"),
    (("corta", "cortar", "cut"), "key:mod+x"),
    (("deshacer", "deshaz", "undo"), "key:mod+z"),
    (("rehacer", "rehaz", "redo"), "key:mod+shift+z"),
    (("selecciona todo", "seleccionar todo", "select all"), "key:mod+a"),
    (("guarda", "guardar", "save"), "key:mod+s"),
    (("buscar", "busca", "find"), "key:mod+f"),
    (("nueva pestana", "new tab"), "key:mod+t"),
    (("cierra la pestana", "cerrar pestana", "close tab"), "key:mod+w"),
    (("cierra la ventana", "cerrar ventana", "close window"), "key:mod+w"),
    (("actualiza", "actualizar", "recarga", "recargar", "refresh", "reload"), "key:mod+r"),
    (("atras", "vuelve", "volver", "go back", "back"), BACK),
    (("adelante", "go forward", "forward"), FORWARD),
    (("intro", "enter", "aceptar", "return"), "key:enter"),
    (("escape", "cancelar", "cancel", "escape key"), "key:esc"),
    (("borra", "borrar", "delete", "backspace"), "key:backspace"),
    (("tabulador", "tab"), "key:tab"),
    (("espacio", "space"), "key:space"),
    (("flecha izquierda", "left arrow"), "key:left"),
    (("flecha derecha", "right arrow"), "key:right"),
    (("flecha arriba", "up arrow"), "key:up"),
    (("flecha abajo", "down arrow"), "key:down"),
    (("pausa", "pausar", "reproduce", "reproducir", "play", "pause", "pausa la musica", "pausa el video",
      "pausa la cancion", "play pause"), "media:play_pause"),
    (("siguiente", "next", "siguiente cancion", "next song", "next track"), "media:next"),
    (("anterior", "previous", "cancion anterior", "previous song"), "media:prev"),
    (("sube el volumen", "mas volumen", "volume up", "louder"), "media:volume_up"),
    (("baja el volumen", "menos volumen", "volume down", "quieter"), "media:volume_down"),
    (("silencio", "silencia", "mute", "quita el sonido"), "media:mute"),
    (("pausa aircontrol", "pausa el control", "descansa", "detente", "stop control", "pause control"), "pause"),
    (("reanuda", "reanudar", "continua", "despierta", "activa el control", "resume", "wake up"), "resume"),
    (("teclado", "abre el teclado", "cierra el teclado", "keyboard", "open keyboard", "close keyboard"), "keyboard"),
    (("usa la mirada", "modo mirada", "con la mirada", "gaze mode", "use gaze", "use eyes"), "mode:gaze"),
    (("usa la mano", "modo mano", "con la mano", "hand mode", "use hand"), "mode:hand"),
]
PREFIXES = [
    (("escribe", "dicta", "teclea", "type", "write", "dictate"), "text"),
    (("abre", "abrir", "open", "lanza", "launch", "inicia", "ejecuta"), "open"),
]
APP_ALIASES = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "navegador": "Safari", "safari": "Safari",
               "correo": "Mail", "mail": "Mail", "musica": "Music", "spotify": "Spotify", "calendario": "Calendar",
               "notas": "Notes", "terminal": "Terminal", "finder": "Finder", "ajustes": "System Settings",
               "whatsapp": "WhatsApp", "word": "Microsoft Word", "excel": "Microsoft Excel",
               "powerpoint": "Microsoft PowerPoint", "firefox": "Firefox", "edge": "Microsoft Edge"}


FUZZY = 0.84            # parecido mínimo (0..1) para aceptar una variante mal oída ("use la mirada" ~ "usa la mirada")
ARTICLES = ("a", "al", "el", "la", "los", "las", "the", "to")
FLAT = [(phrase, action) for words, action in EXACT for phrase in words if len(phrase) >= 5]


def _fuzzy_action(norm):
    """Frase completa casi igual a una conocida. Solo frases de 5+ letras: 'up' o 'tab' no admiten aproximaciones."""
    if len(norm) < 5:
        return None
    best, score = None, FUZZY
    for phrase, action in FLAT:
        ratio = difflib.SequenceMatcher(None, norm, phrase).ratio()
        if ratio >= score:
            best, score = action, ratio
    return best


def _prefix_word(first):
    """Verbo inicial ('abres' -> 'abre') con una tolerancia pequeña."""
    for words, kind in PREFIXES:
        if first in words:
            return kind, 1.0
    candidates = [(w, kind) for words, kind in PREFIXES for w in words if len(w) >= 4]
    match = difflib.get_close_matches(first, [w for w, _ in candidates], n=1, cutoff=0.8)
    return (dict(candidates)[match[0]], 0.8) if match and len(first) >= 4 else (None, 0)


def parse(text):
    """Texto transcrito -> acción (str o dict por plataforma) o None."""
    norm = normalize(text)
    if not norm:
        return None
    for words, action in EXACT:                         # primero las frases completas ("abre el teclado")
        if norm in words:
            return action
    first, _, rest = norm.partition(" ")
    kind, _ = _prefix_word(first)
    if kind and rest:
        if kind == "open":
            arg = rest.split()
            while len(arg) > 1 and arg[0] in ARTICLES:
                arg.pop(0)
            arg = " ".join(arg)
            return f"open:{APP_ALIASES.get(arg, arg.title())}"
        original = re.sub(r"^\W*\w+\W*", "", text.strip(), count=1)             # conserva mayúsculas y tildes
        return f"text:{original.strip()}" if original.strip() else None
    return _fuzzy_action(norm)


def strip_wake_word(text, wake):
    """'Control, abre Safari' -> 'abre Safari' si empieza por la palabra de activación; si no, None."""
    wake_n = normalize(wake)
    norm = normalize(text)
    if not wake_n or not (norm == wake_n or norm.startswith(wake_n + " ")):
        return None
    rest = " ".join(text.strip().split()[len(wake_n.split()):])
    return rest.lstrip(" ,.:;-")

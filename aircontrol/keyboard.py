"""Teclado aéreo: teclado en pantalla que se maneja con la mano (apuntar + pellizcar) o con la mirada (permanencia).
Lógica pura: recibe la posición (u, v) en 0..1 sobre el teclado y devuelve acciones; el dibujo está en ui.py."""
import re

# Fila = lista de (etiqueta, tecla, ancho relativo). `tecla`: carácter, o nombre especial.
LETTERS = [
    [(c, c, 1) for c in "qwertyuiop"] + [("⌫", "backspace", 1.6)],
    [(c, c, 1) for c in "asdfghjklñ"] + [("⏎", "enter", 1.6)],
    [("⇧", "shift", 1.4)] + [(c, c, 1) for c in "zxcvbnm,."] + [("?", "?", 1)],
    [("123", "symbols", 1.4), ("espacio", "space", 5), ("←", "left", 1), ("→", "right", 1), ("✕", "close", 1.2)],
]
SYMBOLS = [
    [(c, c, 1) for c in "1234567890"] + [("⌫", "backspace", 1.6)],
    [(c, c, 1) for c in "@#$%&*-+=/"] + [("⏎", "enter", 1.6)],
    [(c, c, 1) for c in "()[]{}<>:;\"'"][:10] + [("!", "!", 1.6)],
    [("abc", "letters", 1.4), ("espacio", "space", 5), ("←", "left", 1), ("→", "right", 1), ("✕", "close", 1.2)],
]
SUGGEST_H = 0.16                      # fracción superior del teclado reservada a las sugerencias
REPEAT_KEYS = ("backspace", "left", "right")
REPEAT_S = 0.6

WORDS = """de la que el en y a los se del las un por con no una su para es al lo como más pero sus le ya o fue este
ha sí porque esta son entre está cuando muy sin sobre también me hasta hay donde quien desde todo nos durante todos
uno les ni contra otros ese eso ante ellos e esto mí antes algunos qué unos yo otro otras otra él tanto esa estos
mucho quienes nada muchos cual poco ella estar estas algunas algo nosotros mi mis tú te ti tu tus ellas nosotras
hola gracias buenos días tardes noches por favor ayuda necesito quiero puedo puedes vamos bien mal casa agua comer
beber médico llamar llama familia amigo amiga trabajo ahora luego mañana hoy ayer siempre nunca aquí allí dónde cómo
the be to of and in that have it for not on with he as you do at this but his by from they we say her she or an will
my one all would there their what so up out if about who get which go me when make can like time no just him know take
hello thanks please help need want can you are is was good morning night today tomorrow yes""".split()


def suggestions(prefix, limit=3):
    """Palabras que empiezan por `prefix` (las primeras de la lista son las más frecuentes)."""
    if not prefix:
        return []
    seen, out = set(), []
    for w in WORDS:
        if w.startswith(prefix.lower()) and w != prefix.lower() and w not in seen:
            seen.add(w)
            out.append(w)
            if len(out) == limit:
                break
    return out


class KeyboardState:
    def __init__(self, dwell_s=0.9):
        self.dwell_s = dwell_s
        self.layer = "letters"
        self.shift = False
        self.word = ""                           # palabra en curso, para las sugerencias
        self.hover = None                        # tecla bajo el puntero
        self._since = 0.0
        self._armed = True
        self._last_repeat = 0.0

    # --- geometría ---------------------------------------------------------------------------
    @property
    def rows(self):
        return LETTERS if self.layer == "letters" else SYMBOLS

    def layout(self):
        """[(tecla, etiqueta, x0, y0, x1, y1)] con coordenadas 0..1 (las sugerencias ocupan SUGGEST_H arriba)."""
        out, rows = [], self.rows
        row_h = (1 - SUGGEST_H) / len(rows)
        for r, row in enumerate(rows):
            total = sum(w for _, _, w in row)
            x = 0.0
            for label, key, w in row:
                x1 = x + w / total
                out.append((key, label, x, SUGGEST_H + r * row_h, x1, SUGGEST_H + (r + 1) * row_h))
                x = x1
        for i, word in enumerate(suggestions(self.word)):
            out.append((f"suggest:{word}", word, i / 3, 0.0, (i + 1) / 3, SUGGEST_H))
        return out

    def key_at(self, u, v):
        if u is None or not (0 <= u <= 1 and 0 <= v <= 1):
            return None
        for key, _, x0, y0, x1, y1 in self.layout():
            if x0 <= u < x1 and y0 <= v < y1:
                return key
        return None

    def label_of(self, key):
        for k, label, *_ in self.layout():
            if k == key:
                return self.display(label)

    def display(self, label):
        return label.upper() if self.shift and len(label) == 1 and label.isalpha() else label

    # --- pulsaciones -------------------------------------------------------------------------
    def press(self, key):
        """Ejecuta una tecla y devuelve acciones: ("type", texto) | ("key", nombre) | ("close",)."""
        if key is None:
            return []
        if key.startswith("suggest:"):
            rest = key[8:][len(self.word):]
            self.word = ""
            return [("type", rest + " ")]
        if key == "shift":
            self.shift = not self.shift
            return []
        if key in ("symbols", "letters"):
            self.layer = key
            return []
        if key == "close":
            return [("close",)]
        if key == "space":
            self.word = ""
            return [("type", " ")]
        if key in ("enter", "left", "right"):
            self.word = ""
            return [("key", key)]
        if key == "backspace":
            self.word = self.word[:-1]
            return [("key", "backspace")]
        char = key.upper() if self.shift else key
        self.shift = False
        self.word = self.word + char.lower() if re.match(r"\w", char) else ""
        return [("type", char)]

    def update(self, uv, now, pinch=False):
        """Un fotograma. `uv` = posición sobre el teclado o None. `pinch`=True pulsa al instante la tecla bajo el
        puntero (mano); si no, la tecla se pulsa tras `dwell_s` de permanencia (mirada)."""
        key = self.key_at(*uv) if uv else None
        if key != self.hover:
            self.hover, self._since, self._armed = key, now, True
        if key is None:
            return []
        if pinch:
            self._armed = False
            return self.press(key)
        if self._armed and now - self._since >= self.dwell_s:
            self._armed, self._last_repeat = False, now
            return self.press(key)
        if not self._armed and key in REPEAT_KEYS and now - self._last_repeat >= REPEAT_S:
            self._last_repeat = now                    # mantener la mirada en ⌫ o las flechas repite
            return self.press(key)
        return []

    def dwell_progress(self, now):
        if self.hover is None or not self._armed:
            return 0.0
        return min(1.0, (now - self._since) / self.dwell_s)

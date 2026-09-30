"""Respaldo con un modelo local (Ollama) para órdenes que las reglas no entienden ("ponme algo más alto el sonido").
El modelo NUNCA ejecuta nada: solo propone una acción en texto, que se valida con el mismo parser de los perfiles."""
import json

from ..actions import ActionError, parse_action

PROMPT = """Eres el intérprete de órdenes de voz de AirControl, que controla el ordenador. Traduce la orden del usuario
a UNA acción. Responde SOLO con JSON: {"action": "<acción>"} o {"action": null} si no es una orden para el ordenador.

Acciones posibles:
  click | right_click | double_click
  scroll:down:10 | scroll:up:10
  key:mod+c (atajo; mod = Comando/Ctrl; otras teclas: enter esc tab space backspace left right up down f1..f12)
  media:play_pause | media:next | media:prev | media:volume_up | media:volume_down | media:mute
  open:Safari | open:https://ejemplo.com
  text:texto a escribir
  pause | resume | keyboard | mode:gaze | mode:hand

Ejemplos:
"ponme la siguiente canción" -> {"action": "media:next"}
"abre la página de la wikipedia" -> {"action": "open:https://es.wikipedia.org"}
"quiero ver lo que hay más abajo" -> {"action": "scroll:down:10"}
"hola qué tal el día" -> {"action": null}"""

PREFERRED = ("llama3.2", "qwen", "gemma", "phi", "mistral", "llama3")


def pick_model(installed, wanted=""):
    """Modelo a usar: el pedido si está instalado; si no uno de texto (mejor que uno de visión); si no el primero."""
    names = [m for m in installed if m]
    if wanted:
        for n in names:
            if n == wanted or n.split(":")[0] == wanted:
                return n
    text_models = [n for n in names if "vision" not in n and "embed" not in n]
    for pref in PREFERRED:
        for n in text_models:
            if n.startswith(pref):
                return n
    return (text_models or names or [None])[0]


def validate(raw):
    """Respuesta del modelo -> acción válida (str) o None. Descarta todo lo que no pase el parser de acciones."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    action = data.get("action") if isinstance(data, dict) else None
    if not isinstance(action, str) or not action.strip() or len(action) > 300:
        return None
    try:
        parse_action(action)
    except (ActionError, ValueError):
        return None
    return action.strip()


class Interpreter:
    def __init__(self, wanted="", host=None, client=None, log=print):
        self.wanted, self.log = wanted, log
        self._client = client
        self._host = host
        self._model = None
        self._disabled_until = 0.0

    def _ensure(self):
        if self._client is None:
            import ollama
            self._client = ollama.Client(host=self._host, timeout=20) if self._host else ollama.Client(timeout=20)
        if self._model is None:
            listing = self._client.list()
            models = getattr(listing, "models", None) or listing.get("models", [])
            names = [getattr(m, "model", None) or m.get("model") or m.get("name") for m in models]
            self._model = pick_model(names, self.wanted)
            if self._model is None:
                raise RuntimeError("Ollama no tiene ningún modelo instalado (ollama pull llama3.2)")

    def interpret(self, text, now=0.0):
        """Texto hablado -> acción válida o None. Un fallo de Ollama desactiva el respaldo 60 s, sin romper nada."""
        if now < self._disabled_until:
            return None
        try:
            self._ensure()
            reply = self._client.chat(
                model=self._model, format="json", options={"temperature": 0},
                messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": text}])
            content = reply["message"]["content"] if isinstance(reply, dict) else reply.message.content
            return validate(content)
        except Exception as e:
            self._disabled_until = now + 60
            self.log(f"⚠️  Ollama no disponible ({type(e).__name__}: {e}); solo órdenes por reglas durante 1 min.")
            return None

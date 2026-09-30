import queue
import threading
import time

from . import commands
from .audio import Microphone, Segmenter
from .llm import Interpreter

PUSH_WINDOW_S = 8.0          # tras "voice" se escucha una orden durante este tiempo


class VoiceListener:
    """Hilo que escucha, transcribe e interpreta. No ejecuta nada: deja las órdenes en una cola (`poll`) para que el
    bucle principal las ejecute. Modos: push (solo tras `listen_once`) | wake (frases que empiezan por la palabra)."""

    def __init__(self, settings, transcriber=None, interpreter=None, microphone=None, log=print, clock=time.monotonic):
        self.cfg, self.log, self.clock = settings, log, clock
        self._transcriber, self._interpreter, self._mic = transcriber, interpreter, microphone
        self._segmenter = Segmenter()
        self._commands = queue.Queue()
        self._armed_until = 0.0
        self._stop = threading.Event()
        self._thread = None
        self.state = "idle"                  # idle | listening | thinking  (para el HUD)
        self.last_heard = ""

    # --- control -----------------------------------------------------------------------------
    def listen_once(self):
        self._armed_until = self.clock() + PUSH_WINDOW_S
        self.log("🎙  Te escucho...")

    def poll(self):
        """Siguiente (texto_oído, acción) o None."""
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    # --- proceso -----------------------------------------------------------------------------
    def handle_utterance(self, audio):
        """Una frase ya segmentada -> acción en cola. Separado del hilo para poder probarlo."""
        mode = self.cfg.voice_mode
        now = self.clock()
        if mode == "push" and now >= self._armed_until:
            return None
        self.state = "thinking"
        text = self._transcriber.transcribe(audio)
        if not text:
            return None
        stripped = commands.strip_wake_word(text, self.cfg.voice_wake_word)
        if mode == "wake":
            if stripped is None:
                return None                   # no era para nosotros
            text = stripped
        elif stripped:                        # en push se tolera "control, abre Safari"
            text = stripped
        self.last_heard = text
        action = commands.parse(text)
        source = "reglas"
        if action is None and self._interpreter is not None:
            action, source = self._interpreter.interpret(text, now), "Ollama"
        if action is None:
            self.log(f"🎙  «{text}» → no entendido")
            return None
        self._armed_until = 0.0
        self.log(f"🎙  «{text}» → {action if isinstance(action, str) else 'acción por plataforma'} ({source})")
        self._commands.put((text, action))
        return action

    def _run(self):
        try:
            warm = getattr(self._transcriber, "warm", None)
            if warm:
                warm()                                   # descarga/carga el modelo antes de abrir el micrófono
            if self._mic is None:
                self._mic = Microphone()
            self._mic.start()
        except Exception as e:
            self.log(f"⚠️  No se pudo abrir el micrófono: {e}")
            self.state = "idle"
            return
        try:
            while not self._stop.is_set():
                block = self._mic.read()
                if block is None:
                    continue
                self.state = "listening" if (self._segmenter.speaking or self.cfg.voice_mode == "wake"
                                             or self.clock() < self._armed_until) else "idle"
                audio = self._segmenter.feed(block)
                if audio is not None:
                    try:
                        self.handle_utterance(audio)
                    except Exception as e:
                        self.log(f"⚠️  Error de voz: {type(e).__name__}: {e}")
        finally:
            self.state = "idle"
            try:
                self._mic.stop()
            except Exception:
                pass

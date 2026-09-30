"""Reconocimiento de voz con faster-whisper (Whisper en CPU, cuantizado int8). El modelo se descarga la primera vez."""
import sys
import types

import numpy as np


def _avoid_pyav():
    """faster-whisper importa PyAV solo para decodificar ficheros de audio (nosotros le damos arrays). PyAV trae su
    propia libavdevice, con las mismas clases de Objective-C que la de OpenCV: cargar las dos en el mismo proceso da
    avisos de 'clase duplicada' y puede provocar crashes en la captura de cámara de macOS. Un módulo vacío basta."""
    if "av" not in sys.modules:
        sys.modules["av"] = types.ModuleType("av")


# Whisper inventa estas frases con silencio o ruido: se descartan.
HALLUCINATIONS = ("subtitulos", "amara", "gracias por ver", "suscribete", "thanks for watching", "subtitles by",
                  "transcribed by", "www.")
HINT = {"es": "Órdenes para el ordenador: abre Safari, escribe hola, haz clic, doble clic, copia, pega, sube, baja, "
              "volumen, teclado, mirada, pausa.",
        "en": "Computer commands: open Safari, type hello, click, double click, copy, paste, scroll up, scroll down, "
              "volume, keyboard, gaze, pause."}


class Transcriber:
    def __init__(self, size="base", language="es", log=print):
        self.size, self.language, self.log = size, language, log
        self._model = None

    def _load(self):
        if self._model is None:
            _avoid_pyav()
            from faster_whisper import WhisperModel
            self.log(f"⏳ Cargando modelo de voz '{self.size}' (la primera vez se descarga)...")
            self._model = WhisperModel(self.size, device="cpu", compute_type="int8")
        return self._model

    def warm(self):
        """Carga (y si hace falta descarga) el modelo de antemano, en el hilo de voz, sin bloquear el bucle principal."""
        self._load()

    def transcribe(self, audio):
        """float32 mono 16 kHz -> texto ('' si no hay voz fiable)."""
        segments, _ = self._load().transcribe(
            np.asarray(audio, dtype=np.float32), language=self.language, beam_size=1, vad_filter=False,
            initial_prompt=HINT.get(self.language), condition_on_previous_text=False, temperature=0.0)
        kept = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 or seg.avg_logprob < -1.2:
                continue
            kept.append(seg.text.strip())
        text = " ".join(kept).strip()
        low = text.lower()
        return "" if any(h in low for h in HALLUCINATIONS) else text

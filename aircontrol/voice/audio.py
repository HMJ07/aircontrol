"""Captura de micrófono y detección de voz (VAD por energía con suelo de ruido adaptativo)."""
from collections import deque

import numpy as np

RATE = 16000
BLOCK = 512                      # 32 ms por bloque


class Segmenter:
    """Corta el audio continuo en frases: empieza tras unos bloques con voz y termina tras `end_silence` s de
    silencio o al llegar a `max_s`. Incluye `pre_roll` s anteriores para no cortar el inicio de la primera palabra."""

    def __init__(self, rate=RATE, start_blocks=3, end_silence=0.7, max_s=10.0, pre_roll=0.3, min_s=0.25,
                 min_rms=0.008, ratio=3.0):
        self.rate, self.start_blocks, self.end_silence = rate, start_blocks, end_silence
        self.max_s, self.min_s, self.min_rms, self.ratio = max_s, min_s, min_rms, ratio
        self._pre = deque(maxlen=max(1, int(pre_roll * rate / BLOCK)))
        self._noise = 0.003
        self._seen = 0
        self._calibration_blocks = int(0.7 * rate / BLOCK)       # el primer instante se asume sin voz: mide el ruido
        self._loud = 0
        self._chunks = None
        self._silent = 0
        self.speaking = False

    def threshold(self):
        return max(self.min_rms, self._noise * self.ratio)

    def feed(self, block):
        """Un bloque float32 mono. Devuelve la frase (array float32) cuando termina, o None."""
        rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
        self._seen += 1
        if self._seen <= self._calibration_blocks:
            self._noise = rms if self._seen == 1 else 0.8 * self._noise + 0.2 * rms
            self._pre.append(block)
            return None
        voiced = rms > self.threshold()
        if not voiced and self._chunks is None:
            self._noise = 0.95 * self._noise + 0.05 * rms             # el suelo de ruido solo se aprende en silencio
        if self._chunks is None:
            self._pre.append(block)
            self._loud = self._loud + 1 if voiced else 0
            if self._loud >= self.start_blocks:
                self._chunks, self._silent, self.speaking = list(self._pre), 0, True
            return None
        self._chunks.append(block)
        self._silent = 0 if voiced else self._silent + 1
        seconds = len(self._chunks) * BLOCK / self.rate
        if self._silent * BLOCK / self.rate >= self.end_silence or seconds >= self.max_s:
            audio = np.concatenate(self._chunks)
            self._chunks, self._loud, self.speaking = None, 0, False
            self._pre.clear()
            return audio if len(audio) / self.rate >= self.min_s else None
        return None


class Microphone:
    """Flujo de bloques del micrófono por sounddevice (en macOS pide permiso de Micrófono la primera vez)."""

    def __init__(self, device=None):
        import queue

        import sounddevice as sd
        self._queue = queue.Queue(maxsize=200)
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK, device=device,
                                      callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except Exception:
            pass                                                      # si nadie consume, se descarta (nunca bloquear)

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()

    def read(self, timeout=0.5):
        import queue
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

import json
import threading
import time
import unittest

import numpy as np

from aircontrol.actions import parse_action
from aircontrol.config import Settings
from aircontrol.voice import commands
from aircontrol.voice.audio import BLOCK, RATE, Segmenter
from aircontrol.voice.listener import VoiceListener
from aircontrol.voice.llm import Interpreter, pick_model, validate


class ParseTests(unittest.TestCase):
    def test_common_commands_es_and_en(self):
        cases = {"Haz clic.": "click", "doble clic": "double_click", "Clic derecho": "right_click",
                 "Baja": "scroll:down:12", "scroll up": "scroll:up:12", "Copia": "key:mod+c", "pegar": "key:mod+v",
                 "Sube el volumen": "media:volume_up", "Siguiente canción": "media:next", "silencio": "media:mute",
                 "Descansa": "pause", "Reanuda": "resume", "abre el teclado": "keyboard",
                 "usa la mirada": "mode:gaze", "Modo mano": "mode:hand", "Play": "media:play_pause",
                 "intro": "key:enter", "escape": "key:esc", "Deshacer": "key:mod+z"}
        for heard, spec in cases.items():
            self.assertEqual(commands.parse(heard), spec, heard)

    def test_open_and_text_keep_content(self):
        self.assertEqual(commands.parse("Abre Safari."), "open:Safari")
        self.assertEqual(commands.parse("abre chrome"), "open:Google Chrome")
        self.assertEqual(commands.parse("Escribe Hola, ¿qué tal?"), "text:Hola, ¿qué tal?")
        self.assertEqual(commands.parse("type Hello World"), "text:Hello World")
        self.assertIsNone(commands.parse("escribe"))

    def test_misheard_variants_are_tolerated(self):
        self.assertEqual(commands.parse("Abres a Safari."), "open:Safari")
        self.assertEqual(commands.parse("abre el navegador"), "open:Safari")
        self.assertEqual(commands.parse("Use la mirada"), "mode:gaze")
        self.assertEqual(commands.parse("sube al volumen"), "media:volume_up")

    def test_fuzzy_does_not_create_false_positives(self):
        for heard in ("hola buenas tardes", "qué tiempo hace hoy", "abrigo", "copas", "no me gusta", "uno", "tabla",
                      "bajo la mesa", "arriba españa"):
            self.assertIsNone(commands.parse(heard), heard)

    def test_unknown_and_empty(self):
        self.assertIsNone(commands.parse("qué buen día hace hoy"))
        self.assertIsNone(commands.parse(""))
        self.assertIsNone(commands.parse("   ."))

    def test_every_rule_is_a_valid_action(self):
        for _, spec in commands.EXACT:
            parse_action(spec)                                        # ninguna regla puede producir una acción inválida
        for alias in set(commands.APP_ALIASES.values()):
            parse_action(f"open:{alias}")

    def test_back_is_platform_specific(self):
        spec = commands.parse("atrás")
        self.assertEqual(parse_action(spec, "darwin").arg, ["cmd", "["])
        self.assertEqual(parse_action(spec, "win32").arg, ["alt", "left"])

    def test_wake_word(self):
        self.assertEqual(commands.strip_wake_word("Control, abre Safari", "control"), "abre Safari")
        self.assertEqual(commands.strip_wake_word("control baja", "Control"), "baja")
        self.assertIsNone(commands.strip_wake_word("controlador de ruido", "control"))
        self.assertIsNone(commands.strip_wake_word("abre Safari", "control"))
        self.assertEqual(commands.strip_wake_word("ok aircontrol haz clic", "ok aircontrol"), "haz clic")


class PyAvTests(unittest.TestCase):
    def test_faster_whisper_loads_without_pyav(self):
        import importlib.util
        import subprocess
        import sys
        if importlib.util.find_spec("faster_whisper") is None:
            self.skipTest("faster-whisper no instalado")
        code = ("import sys\nfrom aircontrol.voice import stt\nstt._avoid_pyav()\nimport faster_whisper\n"
                "print(hasattr(sys.modules['av'], 'open'))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)
        self.assertNotIn("implemented in both", out.stderr)


class LlmTests(unittest.TestCase):
    def test_validate_accepts_only_valid_actions(self):
        self.assertEqual(validate('{"action": "media:next"}'), "media:next")
        self.assertEqual(validate('{"action": "key:mod+c"}'), "key:mod+c")
        for bad in ('{"action": null}', '{"action": "rm -rf /"}', '{"action": "media:teleport"}', "no es json",
                    '{"action": 5}', '[]', '{"action": ""}', json.dumps({"action": "text:" + "x" * 400})):
            self.assertIsNone(validate(bad), bad)

    def test_pick_model_prefers_text_models(self):
        self.assertEqual(pick_model(["llama3.2-vision:latest", "llama3.2:3b"]), "llama3.2:3b")
        self.assertEqual(pick_model(["llama3.2-vision:latest"]), "llama3.2-vision:latest")
        self.assertEqual(pick_model(["qwen2.5:7b", "llama3.2:3b"], wanted="qwen2.5"), "qwen2.5:7b")
        self.assertEqual(pick_model(["foo:1b", "nomic-embed-text"]), "foo:1b")
        self.assertIsNone(pick_model([]))

    class FakeOllama:
        def __init__(self, reply, fail=False):
            self.reply, self.fail, self.calls = reply, fail, 0

        def list(self):
            return {"models": [{"model": "llama3.2:3b"}]}

        def chat(self, **kw):
            self.calls += 1
            if self.fail:
                raise ConnectionError("sin servidor")
            self.kw = kw
            return {"message": {"content": self.reply}}

    def test_interpreter_uses_model_and_validates(self):
        client = self.FakeOllama('{"action": "scroll:down:10"}')
        it = Interpreter(client=client, log=lambda *_: None)
        self.assertEqual(it.interpret("quiero ver más abajo"), "scroll:down:10")
        self.assertEqual(client.kw["model"], "llama3.2:3b")
        self.assertEqual(client.kw["format"], "json")
        bad = Interpreter(client=self.FakeOllama('{"action": "format c:"}'), log=lambda *_: None)
        self.assertIsNone(bad.interpret("haz algo raro"))

    def test_interpreter_failure_disables_temporarily_without_raising(self):
        client = self.FakeOllama("", fail=True)
        logs = []
        it = Interpreter(client=client, log=logs.append)
        self.assertIsNone(it.interpret("x", now=0.0))
        self.assertIsNone(it.interpret("x", now=10.0))
        self.assertEqual(client.calls, 1)                               # no insiste durante el minuto de espera
        self.assertEqual(len(logs), 1)
        self.assertIsNone(it.interpret("x", now=100.0))
        self.assertEqual(client.calls, 2)


def tone(seconds, amp=0.2, freq=300):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def blocks(audio):
    return [audio[i:i + BLOCK] for i in range(0, len(audio) - BLOCK + 1, BLOCK)]


class SegmenterTests(unittest.TestCase):
    def run_seg(self, audio, seg=None):
        seg, out = seg or Segmenter(), []
        for b in blocks(audio):
            r = seg.feed(b)
            if r is not None:
                out.append(r)
        return out

    def test_one_phrase_between_silences(self):
        rng = np.random.default_rng(0)
        noise = lambda s: (rng.normal(0, 0.002, int(s * RATE))).astype(np.float32)
        out = self.run_seg(np.concatenate([noise(1), tone(1.2), noise(1.5)]))
        self.assertEqual(len(out), 1)
        self.assertGreater(len(out[0]) / RATE, 1.2)                     # incluye el final, con el silencio de cierre
        self.assertLess(len(out[0]) / RATE, 2.6)

    def test_two_phrases(self):
        silence = np.zeros(int(1.2 * RATE), np.float32)
        out = self.run_seg(np.concatenate([silence, tone(0.8), silence, tone(0.6), silence]))
        self.assertEqual(len(out), 2)

    def test_silence_and_short_clicks_produce_nothing(self):
        self.assertEqual(self.run_seg(np.zeros(3 * RATE, np.float32)), [])
        self.assertEqual(self.run_seg(np.concatenate([np.zeros(RATE, np.float32), tone(0.05), np.zeros(RATE, np.float32)])), [])

    def test_long_speech_is_cut_at_max(self):
        out = self.run_seg(np.concatenate([np.zeros(RATE, np.float32), tone(15)]))
        self.assertEqual(len(out), 1)
        self.assertLessEqual(len(out[0]) / RATE, 10.5)

    def test_adapts_to_noisy_room(self):
        rng = np.random.default_rng(1)
        noisy = lambda s: rng.normal(0, 0.02, int(s * RATE)).astype(np.float32)        # ruido de fondo alto constante
        out = self.run_seg(np.concatenate([noisy(3), noisy(1) + tone(1, amp=0.3), noisy(2)]))
        self.assertEqual(len(out), 1)


class FakeTranscriber:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


class ListenerTests(unittest.TestCase):
    def make(self, text, mode="push", interpreter=None):
        cfg = Settings(voice_mode=mode)
        clock = [0.0]
        tr = FakeTranscriber(text)
        vl = VoiceListener(cfg, transcriber=tr, interpreter=interpreter, log=lambda *_: None, clock=lambda: clock[0])
        return vl, tr, clock

    def test_push_mode_ignores_speech_until_armed_and_does_not_even_transcribe(self):
        vl, tr, clock = self.make("haz clic")
        self.assertIsNone(vl.handle_utterance(tone(1)))
        self.assertEqual(tr.calls, 0)
        vl.listen_once()
        self.assertEqual(vl.handle_utterance(tone(1)), "click")
        self.assertEqual(vl.poll(), ("haz clic", "click"))
        self.assertIsNone(vl.poll())
        self.assertIsNone(vl.handle_utterance(tone(1)))                 # solo una orden por activación

    def test_push_mode_tolerates_wake_word(self):
        vl, _, _ = self.make("Control, haz clic")
        vl.listen_once()
        self.assertEqual(vl.handle_utterance(tone(1)), "click")

    def test_push_window_expires(self):
        vl, tr, clock = self.make("haz clic")
        vl.listen_once()
        clock[0] = 20
        self.assertIsNone(vl.handle_utterance(tone(1)))

    def test_wake_mode_requires_wake_word(self):
        vl, _, _ = self.make("abre Safari", mode="wake")
        self.assertIsNone(vl.handle_utterance(tone(1)))
        vl2, _, _ = self.make("Control, abre Safari", mode="wake")
        self.assertEqual(vl2.handle_utterance(tone(1)), "open:Safari")

    def test_falls_back_to_llm_only_when_rules_fail(self):
        class Llm:
            calls = 0

            def interpret(self, text, now):
                Llm.calls += 1
                return "media:next"

        vl, _, _ = self.make("pon algo más alegre", mode="wake", interpreter=Llm())
        vl.cfg.voice_wake_word = "pon"
        self.assertEqual(vl.handle_utterance(tone(1)), "media:next")
        vl2, _, _ = self.make("control copia", mode="wake", interpreter=Llm())
        before = Llm.calls
        self.assertEqual(vl2.handle_utterance(tone(1)), "key:mod+c")
        self.assertEqual(Llm.calls, before)                             # las reglas no consultan al modelo

    def test_unknown_without_llm_is_dropped(self):
        vl, _, _ = self.make("control hace buen día", mode="wake")
        self.assertIsNone(vl.handle_utterance(tone(1)))
        self.assertIsNone(vl.poll())

    def test_thread_end_to_end_with_fake_microphone(self):
        class Mic:
            def __init__(self, audio):
                self.items = blocks(audio)
                self.started = False

            def start(self):
                self.started = True

            def stop(self):
                pass

            def read(self, timeout=0.5):
                if self.items:
                    return self.items.pop(0)
                time.sleep(0.01)
                return None

        audio = np.concatenate([np.zeros(RATE, np.float32), tone(0.8), np.zeros(RATE, np.float32)])
        cfg = Settings(voice_mode="wake")
        vl = VoiceListener(cfg, transcriber=FakeTranscriber("control baja"), microphone=Mic(audio), log=lambda *_: None)
        vl.start()
        got = None
        for _ in range(200):
            got = vl.poll()
            if got:
                break
            time.sleep(0.02)
        vl.stop()
        self.assertEqual(got, ("baja", "scroll:down:12"))

    def test_microphone_failure_is_reported_not_raised(self):
        class Broken:
            def start(self):
                raise OSError("sin permiso")

        logs = []
        vl = VoiceListener(Settings(voice_mode="wake"), transcriber=FakeTranscriber(""), microphone=Broken(),
                           log=logs.append)
        vl.start()
        vl._thread.join(2)
        self.assertTrue(any("micrófono" in l for l in logs))


if __name__ == "__main__":
    unittest.main()

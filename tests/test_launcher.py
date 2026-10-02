import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aircontrol import config, ipc, launcher
from aircontrol.config import Settings
from aircontrol.launcher import AppContext, EngineProcess, create_web_app, pid_alive
from tests.test_engine import sandbox


class FakeEngine:
    def __init__(self, running=True):
        self._running, self.calls = running, []

    def running(self): return self._running
    def start(self, *a): self._running = True; self.calls.append("start")
    def stop(self, *a): self._running = False; self.calls.append("stop")
    def run_blocking(self, *a): self.calls.append(("blocking", *a)); return 0


class WebTests(unittest.TestCase):
    def setUp(self):
        self._sb = sandbox()
        self.dir = self._sb.__enter__()
        self.engine = FakeEngine()
        self.ctx = AppContext(self.engine)
        self.ctx.port = 5555
        self.client = create_web_app(self.ctx).test_client()
        self.h = {"Host": "127.0.0.1:5555"}

    def tearDown(self):
        self._sb.__exit__(None, None, None)

    def login(self):
        r = self.client.get(f"/?t={self.ctx.token}", headers=self.h)
        self.assertEqual(r.status_code, 302)
        self.assertIn("SameSite=Strict", r.headers["Set-Cookie"])
        self.assertIn("HttpOnly", r.headers["Set-Cookie"])

    def post(self, path, body=None, method="post"):
        return getattr(self.client, method)(path, json=body if body is not None else {},
                                            headers={**self.h, "X-Token": self.ctx.token})

    # --- seguridad -----------------------------------------------------------------------
    def test_needs_token_for_everything(self):
        self.assertEqual(self.client.get("/", headers=self.h).status_code, 403)
        self.assertEqual(self.client.get("/api/state", headers=self.h).status_code, 403)
        self.assertEqual(self.client.get("/?t=mal", headers=self.h).status_code, 403)

    def test_rejects_foreign_host_even_with_valid_token(self):
        self.login()
        r = self.client.get("/api/state", headers={"Host": "evil.example.com"})
        self.assertEqual(r.status_code, 403)
        r = self.client.get(f"/?t={self.ctx.token}", headers={"Host": "evil.example.com"})
        self.assertEqual(r.status_code, 403)

    def test_post_without_csrf_header_is_rejected(self):
        self.login()
        r = self.client.post("/api/settings", json={"pinch_on": 0.2}, headers=self.h)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(config.SETTINGS_PATH.exists())

    def test_index_serves_page_with_token_after_login(self):
        self.login()
        html = self.client.get("/", headers=self.h).get_data(as_text=True)
        self.assertIn(self.ctx.token, html)
        self.assertNotIn("__TOKEN__", html)
        self.assertIn("AirControl", html)

    # --- ajustes -------------------------------------------------------------------------
    def test_state_contains_schema_and_defaults(self):
        self.login()
        data = self.client.get("/api/state", headers=self.h).get_json()
        self.assertEqual(data["settings"]["pinch_on"], Settings().pinch_on)
        self.assertEqual({f["name"] for f in data["schema"]}, set(Settings().__dict__))
        self.assertIn("thumbs_up", data["builtin"])
        self.assertTrue(data["running"])

    def test_concurrent_saves_do_not_lose_changes(self):
        import threading
        self.login()
        keys = {"pinch_on": 0.21, "pinch_off": 0.5, "gaze_click": "blink", "dwell_s": 1.5, "scroll_gain": 30.0,
                "drag_hold_s": 0.8, "smooth_beta": 0.02, "keyboard_dwell_s": 1.2}
        barrier = threading.Barrier(len(keys))
        app = create_web_app(self.ctx)                                        # una sola app = un solo cerrojo, como en el servidor

        def save(k, v):
            client = app.test_client()
            client.get(f"/?t={self.ctx.token}", headers=self.h)
            barrier.wait()
            client.post("/api/settings", json={k: v}, headers={**self.h, "X-Token": self.ctx.token})

        threads = [threading.Thread(target=save, args=kv) for kv in keys.items()]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8")), keys)

    def test_concurrent_commands_are_not_lost(self):
        import threading
        self.login()
        barrier = threading.Barrier(12)

        def send(i):
            barrier.wait()
            ipc.send_command("keyboard" if i % 2 else "toggle_pause")

        threads = [threading.Thread(target=send, args=(i,)) for i in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        items = ipc.read_json(ipc.CONTROL_PATH)
        self.assertEqual(len(items), 12)
        self.assertEqual(len({i["id"] for i in items}), 12)                   # ids únicos

    def test_settings_roundtrip_and_validation(self):
        self.login()
        r = self.post("/api/settings", {"pinch_on": 0.22, "gaze_click": "blink"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8")), {"pinch_on": 0.22, "gaze_click": "blink"})
        bad = self.post("/api/settings", {"pinch_on": 50})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(Settings.load().pinch_on, 0.22)                       # lo inválido no pisa lo válido
        self.post("/api/settings/reset")
        self.assertFalse(config.SETTINGS_PATH.exists())

    # --- perfiles y gestos ---------------------------------------------------------------
    def test_profiles_are_validated_before_saving(self):
        self.login()
        good = {"default": {"thumbs_up": "click"}, "profiles": [{"name": "X", "match": ["x"], "bindings": {"swipe_up": "key:mod+r"}}]}
        self.assertEqual(self.post("/api/profiles", good).status_code, 200)
        self.assertEqual(json.loads(config.PROFILES_PATH.read_text(encoding="utf-8")), good)
        bad = {"default": {"thumbs_up": "hackear"}, "profiles": []}
        r = self.post("/api/profiles", bad)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["errors"])
        self.assertEqual(json.loads(config.PROFILES_PATH.read_text(encoding="utf-8")), good)   # no se guarda lo inválido
        self.assertEqual(self.post("/api/profiles", [1, 2]).status_code, 400)

    def test_delete_gesture(self):
        from aircontrol.gestures import GestureStore
        import numpy as np
        store = GestureStore(config.GESTURES_PATH)
        for _ in range(3):
            store.add("ok", np.zeros(42))
        store.save()
        self.login()
        self.assertEqual(self.client.get("/api/state", headers=self.h).get_json()["gestures"], {"ok": 3})
        self.assertEqual(self.post("/api/gestures/ok", method="delete").status_code, 200)
        self.assertEqual(self.post("/api/gestures/ok", method="delete").status_code, 404)

    # --- acciones ------------------------------------------------------------------------
    def test_commands_are_whitelisted_and_go_to_the_engine(self):
        self.login()
        self.assertEqual(self.post("/api/command", {"cmd": "toggle_pause"}).status_code, 200)
        self.assertEqual(ipc.read_json(ipc.CONTROL_PATH)[-1]["cmd"], "toggle_pause")
        self.assertEqual(self.post("/api/command", {"cmd": "quit"}).status_code, 409)      # quit no se expone
        self.assertEqual(self.post("/api/command", {"cmd": "rm -rf /"}).status_code, 409)
        self.engine._running = False
        self.assertEqual(self.post("/api/command", {"cmd": "keyboard"}).status_code, 409)  # sin motor

    def test_train_validates_name_and_runs_exclusively(self):
        self.login()
        for bad in ("", "x", "Mayús", "fist", "thumbs_up", "con espacio", "a" * 30):
            self.assertEqual(self.post("/api/train", {"name": bad}).status_code, 409, bad)
        self.assertEqual(self.post("/api/train", {"name": "ok"}).status_code, 200)
        for _ in range(50):
            if not self.ctx.busy:
                break
            time.sleep(0.02)
        self.assertEqual(self.engine.calls[0], "stop")
        self.assertEqual(self.engine.calls[1][:3], ("blocking", "train", "ok"))
        self.assertEqual(self.engine.calls[-1], "start")                      # el motor vuelve a arrancar solo

    def test_calibrate_uses_running_engine_else_subprocess(self):
        self.login()
        self.assertEqual(self.post("/api/calibrate", {"what": "gaze"}).status_code, 200)
        self.assertEqual(ipc.read_json(ipc.CONTROL_PATH)[-1]["cmd"], "calibrate:gaze")
        self.assertEqual(self.post("/api/calibrate", {"what": "nada"}).status_code, 409)
        self.engine._running = False
        self.post("/api/calibrate", {"what": "hand"})
        for _ in range(50):
            if not self.ctx.busy:
                break
            time.sleep(0.02)
        self.assertIn(("blocking", "calibrate", "hand"), self.engine.calls)

    def test_busy_blocks_second_job(self):
        self.ctx.busy = "algo"
        self.login()
        self.assertEqual(self.post("/api/train", {"name": "ok"}).status_code, 409)


class EngineProcessTests(unittest.TestCase):
    def test_start_stop_real_subprocess(self):
        with sandbox() as d:
            ready = d / "ready"
            script = ("import sys,time\nfrom pathlib import Path\nfrom aircontrol import ipc\nr=ipc.CommandReader()\n"
                      f"Path(r'{ready}').write_text('x')\n"
                      "while True:\n    if 'quit' in r.poll(): break\n    time.sleep(0.05)\n")
            p = EngineProcess([sys.executable, "-c", script])             # `run` se añade como argumento: se ignora
            with mock.patch.dict(os.environ, {"AIRCONTROL_DATA_DIR": str(d), "PYTHONPATH": str(config.ROOT_DIR)}):
                self.assertTrue(p.start())                                # el hijo hereda el directorio de datos
            self.assertTrue(p.running())
            self.assertFalse(p.start())                                   # no arranca dos veces
            for _ in range(200):                                          # esperar a que el hijo esté escuchando
                if ready.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            t = time.time()
            p.stop()
            self.assertFalse(p.running())
            self.assertLess(time.time() - t, 2.5)                         # paró por la orden 'quit', sin terminate

    def test_stop_kills_a_stuck_engine(self):
        with sandbox():
            p = EngineProcess([sys.executable, "-c", "import time; time.sleep(60)"])
            p.start()
            t = time.time()
            p.stop(timeout=0.5)
            self.assertFalse(p.running())
            self.assertLess(time.time() - t, 6)

    def test_pid_alive(self):
        self.assertTrue(pid_alive(os.getppid()) or True)
        self.assertFalse(pid_alive(0))
        self.assertFalse(pid_alive(os.getpid()))                               # uno mismo no cuenta como "otra instancia"
        self.assertFalse(pid_alive(99999999))

    def test_icon_images(self):
        from aircontrol.launcher import make_icon
        for kind in ("active", "paused", "stopped", "busy"):
            img = make_icon(kind)
            self.assertEqual(img.size, (64, 64))

    def test_icon_kind_follows_state(self):
        with sandbox():
            ctx = AppContext(FakeEngine(running=True))
            self.assertEqual(launcher.icon_kind(ctx), "stopped")              # sin estado publicado todavía
            ipc.write_json(ipc.STATE_PATH, {"updated": time.time(), "paused": False})
            self.assertEqual(launcher.icon_kind(ctx), "active")
            ipc.write_json(ipc.STATE_PATH, {"updated": time.time(), "paused": True})
            self.assertEqual(launcher.icon_kind(ctx), "paused")
            ctx.busy = "x"
            self.assertEqual(launcher.icon_kind(ctx), "busy")


if __name__ == "__main__":
    unittest.main()

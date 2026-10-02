import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from aircontrol import config, geometry, remote
from aircontrol.config import Settings
from aircontrol.engine import Engine
from aircontrol.gaze import extract_features
from aircontrol.remote import FACE_KEYS, RemoteCamera, RemoteFaces, RemoteHands, RemoteHub, create_app, parse_payload
from aircontrol.system.backend import DryRunBackend
from tests.handfactory import point
from tests.test_engine import FakeWindow, sandbox
from tests.test_gaze import SCREEN, face as synthetic_face, looking_at

WEB = Path(__file__).resolve().parent.parent / "aircontrol" / "web" / "remote"
MODELS = Path(__file__).resolve().parent.parent / "models"


def phone_payload(hand=None, face=None, w=640, h=480):
    """Lo que enviaría el móvil: imagen SIN espejar (el ordenador la espeja), así que x se invierte respecto a lo que
    el usuario ve. `hand`/`face` son arrays con las coordenadas que se quieren ver en el ordenador."""
    out = {"t": 1234, "w": w, "h": h, "hand": None, "face": None}
    if hand is not None:
        raw = np.array(hand, dtype=np.float64)
        raw[:, 0] = 1 - raw[:, 0]
        out["hand"] = raw.flatten().round(5).tolist()
    if face is not None:
        out["face"] = {str(k): [round(1 - float(face[k][0]), 5), round(float(face[k][1]), 5), 0.0] for k in FACE_KEYS}
    return out


class ParseTests(unittest.TestCase):
    def test_hand_is_mirrored_like_a_laptop_webcam(self):
        lm = point()
        frame = parse_payload(phone_payload(hand=lm))
        np.testing.assert_allclose(frame.hand, lm, atol=1e-4)
        self.assertIsNone(frame.face)
        self.assertAlmostEqual(frame.aspect, 640 / 480)

    def test_face_keeps_only_allowed_points_and_is_mirrored(self):
        f = looking_at(0.3, 0.6)
        frame = parse_payload(phone_payload(face=f))
        self.assertEqual(frame.face.shape, (478, 3))
        for k in FACE_KEYS:
            np.testing.assert_allclose(frame.face[k][:2], f[k][:2], atol=1e-4)
        other = [i for i in range(478) if i not in FACE_KEYS]
        self.assertFalse(frame.face[other].any())                       # lo demás queda vacío
        a, b = extract_features(frame.face)[0], extract_features(f)[0]
        np.testing.assert_allclose(a, b, atol=1e-3)                    # y la mirada se calcula igual

    def test_a_point_at_x_zero_is_still_mirrored(self):
        payload = phone_payload(face=looking_at(0.5, 0.5))
        payload["face"][str(FACE_KEYS[0])][0] = 0.0
        self.assertAlmostEqual(float(parse_payload(payload).face[FACE_KEYS[0]][0]), 1.0)

    def test_invalid_payloads_are_rejected(self):
        good = phone_payload(hand=point())
        bad = [
            None, [], "x", {**good, "w": 0}, {**good, "h": "480"}, {**good, "w": True}, {**good, "hand": [0.1] * 62},
            {**good, "hand": [float("nan")] * 63}, {**good, "hand": [99.0] * 63}, {**good, "hand": "hola"},
            {**good, "face": {}}, {**good, "face": {"1": [0, 0, 0]}}, {**good, "face": {"999": [0, 0, 0]}},
            {**good, "face": {"x": [0, 0, 0]}}, {**good, "face": [1, 2]},
        ]
        for payload in bad:
            with self.assertRaises(ValueError, msg=str(payload)[:60]):
                parse_payload(payload)

    def test_no_hand_and_no_face_is_valid(self):
        frame = parse_payload({"w": 640, "h": 360, "hand": None, "face": None})
        self.assertIsNone(frame.hand)
        self.assertAlmostEqual(frame.aspect, 16 / 9)


class HubTests(unittest.TestCase):
    def test_push_and_wait(self):
        hub = RemoteHub()
        self.assertIsNone(hub.wait_frame(0.01))
        self.assertTrue(hub.push("a", parse_payload(phone_payload(hand=point()))))
        self.assertIsNotNone(hub.wait_frame(0.1))
        self.assertIsNone(hub.wait_frame(0.01))                          # cada fotograma se entrega una sola vez
        self.assertTrue(hub.connected())

    def test_wait_wakes_up_when_a_frame_arrives(self):
        hub = RemoteHub()
        threading.Timer(0.05, lambda: hub.push("a", parse_payload(phone_payload()))).start()
        t = time.time()
        self.assertIsNotNone(hub.wait_frame(2.0))
        self.assertLess(time.time() - t, 1.0)

    def test_only_one_phone_at_a_time_until_it_goes_silent(self):
        now = [100.0]
        hub = RemoteHub(clock=lambda: now[0])
        frame = parse_payload(phone_payload())
        self.assertTrue(hub.push("movil-1", frame))
        self.assertFalse(hub.push("movil-2", frame))                     # ocupado
        self.assertTrue(hub.push("movil-1", frame))
        now[0] += remote.CLAIM_S + 0.1                                   # el primero calla
        self.assertTrue(hub.push("movil-2", frame))
        self.assertFalse(hub.push("movil-1", frame))

    def test_connected_expires(self):
        now = [0.0]
        hub = RemoteHub(clock=lambda: now[0])
        self.assertFalse(hub.connected())
        hub.push("a", parse_payload(phone_payload()))
        self.assertTrue(hub.connected())
        now[0] += 3
        self.assertFalse(hub.connected())

    def test_commands_are_whitelisted(self):
        hub = RemoteHub()
        hub.command("toggle_pause")
        hub.command("keyboard")
        self.assertEqual(hub.pop_commands(), ["toggle_pause", "keyboard"])
        self.assertEqual(hub.pop_commands(), [])
        for bad in ("quit", "calibrate:gaze", "rm -rf /", ""):
            with self.assertRaises(ValueError):
                hub.command(bad)


class AdapterTests(unittest.TestCase):
    def tearDown(self):
        geometry.set_aspect(640, 480)

    def test_camera_hands_and_faces_follow_the_phone(self):
        hub = RemoteHub()
        cam = RemoteCamera(hub)
        hands, faces = RemoteHands(cam), RemoteFaces(cam)
        self.assertEqual(cam.read(), (False, None))                      # sin móvil, sin fotograma
        hub.push("a", parse_payload(phone_payload(hand=point(), face=looking_at(0.5, 0.5), w=640, h=360)))
        ok, canvas = cam.read()
        self.assertTrue(ok)
        self.assertEqual(canvas.shape, (360, 640, 3))                    # lienzo con la forma del vídeo del móvil
        self.assertIsNotNone(hands.detect(canvas, 0))
        self.assertIsNotNone(faces.detect(canvas, 0))
        self.assertAlmostEqual(geometry.aspect_scale(), (16 / 9) / (4 / 3))      # y se corrige la forma 16:9
        hub.push("a", parse_payload(phone_payload(w=640, h=480)))        # el móvil deja de ver la mano
        ok, canvas = cam.read()
        self.assertIsNone(hands.detect(canvas, 1))
        self.assertIsNone(faces.detect(canvas, 1))


class ServerAppTests(unittest.TestCase):
    def setUp(self):
        self.hub = RemoteHub()
        self.app = create_app(self.hub, "secreto", WEB, MODELS, info={"name": "pc"})
        self.c = self.app.test_client()
        self.h = {"X-Token": "secreto", "X-Client": "m1"}

    def test_page_and_assets_are_public_but_the_api_needs_the_token(self):
        self.assertEqual(self.c.get("/remote/").status_code, 200)
        self.assertEqual(self.c.get("/remote/remote.js").status_code, 200)
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.c.get("/api/hello").status_code, 403)
        self.assertEqual(self.c.get("/api/hello", headers={"X-Token": "otro"}).status_code, 403)
        self.assertEqual(self.c.post("/api/frame", json=phone_payload()).status_code, 403)
        self.assertEqual(self.c.post("/api/command", json={"cmd": "pause"}).status_code, 403)
        self.assertIsNone(self.hub.wait_frame(0.01))                     # nada se coló sin token

    def test_relative_directories_work_too(self):
        import os
        root = Path(__file__).resolve().parent.parent
        old = os.getcwd()
        os.chdir(root)
        try:
            c = create_app(RemoteHub(), "t", Path("aircontrol/web/remote"), Path("models")).test_client()
            self.assertEqual(c.get("/remote/").status_code, 200)
        finally:
            os.chdir(old)

    def test_hello_tells_the_phone_which_face_points_to_send(self):
        data = self.c.get("/api/hello", headers=self.h).get_json()
        self.assertEqual(data["face_keys"], FACE_KEYS)
        self.assertEqual(data["name"], "pc")

    def test_frame_roundtrip_and_status_comes_back(self):
        self.hub.status = {"paused": True, "need_face": False}
        r = self.c.post("/api/frame", json=phone_payload(hand=point()), headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["paused"], True)
        self.assertIsNotNone(self.hub.wait_frame(0.1).hand)

    def test_bad_and_oversized_frames_and_second_phone(self):
        self.assertEqual(self.c.post("/api/frame", data="no es json", headers=self.h).status_code, 400)
        self.assertEqual(self.c.post("/api/frame", json={"w": 640, "h": 480, "hand": [1] * 5}, headers=self.h).status_code, 400)
        self.assertEqual(self.c.post("/api/frame", data=b"x" * (remote.MAX_BODY + 1), headers=self.h).status_code, 413)
        self.assertEqual(self.c.post("/api/frame", json=phone_payload(), headers=self.h).status_code, 200)
        other = {"X-Token": "secreto", "X-Client": "m2"}
        self.assertEqual(self.c.post("/api/frame", json=phone_payload(), headers=other).status_code, 409)

    def test_commands(self):
        self.assertEqual(self.c.post("/api/command", json={"cmd": "toggle_pause"}, headers=self.h).status_code, 200)
        self.assertEqual(self.hub.pop_commands(), ["toggle_pause"])
        self.assertEqual(self.c.post("/api/command", json={"cmd": "quit"}, headers=self.h).status_code, 400)

    def test_only_the_two_models_are_served_from_the_models_folder(self):
        if (MODELS / "hand_landmarker.task").exists():
            self.assertEqual(self.c.get("/remote/models/hand_landmarker.task").status_code, 200)
        for name in ("../README.md", "secret.txt", "..%2Fsettings.json", "hand_landmarker.task.bak"):
            self.assertEqual(self.c.get(f"/remote/models/{name}").status_code, 404, name)
        self.assertEqual(self.c.get("/remote/../../README.md").status_code in (301, 308, 404), True)


class RealTlsTests(unittest.TestCase):
    def test_https_with_self_signed_cert_end_to_end(self):
        d = Path(tempfile.mkdtemp())
        hub = RemoteHub()
        server = remote.RemoteServer(hub, d, WEB, MODELS, port=18500, ip="127.0.0.1")
        self.addCleanup(server.stop)
        ctx = ssl._create_unverified_context()

        def call(path, body=None, token=server.token):
            req = urllib.request.Request(f"https://127.0.0.1:{server.port}{path}", headers={"X-Token": token, "X-Client": "t"},
                                         data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST")
            return urllib.request.urlopen(req, context=ctx, timeout=5)

        self.assertEqual(call("/api/hello").status, 200)
        self.assertEqual(call("/api/frame", phone_payload(hand=point())).status, 200)
        self.assertIsNotNone(hub.wait_frame(0.5).hand)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            call("/api/hello", token="mal")
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(OSError):                                 # sin TLS no hay servicio en claro (reset o URLError)
            urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/hello", timeout=3)
        self.assertIn("#t=" + server.token, server.url)                  # el token va en el fragmento (no se envía al servidor)
        self.assertTrue(server.url.startswith("https://"))

    def test_port_in_use_falls_through_to_the_next_one(self):
        d = Path(tempfile.mkdtemp())
        a = remote.RemoteServer(RemoteHub(), d, WEB, MODELS, port=18520, ip="127.0.0.1")
        b = remote.RemoteServer(RemoteHub(), d, WEB, MODELS, port=18520, ip="127.0.0.1")
        self.addCleanup(a.stop)
        self.addCleanup(b.stop)
        self.assertEqual((a.port, b.port), (18520, 18521))
        self.assertNotEqual(a.token, b.token)

    def test_certificate_covers_the_lan_ip_and_is_reused_until_the_ip_changes(self):
        from cryptography import x509
        d = Path(tempfile.mkdtemp())
        cert, key = remote.ensure_cert(d, "192.168.1.50")
        parsed = x509.load_pem_x509_certificate(cert.read_bytes())
        sans = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = [str(i) for i in sans.get_values_for_type(x509.IPAddress)]
        self.assertIn("192.168.1.50", ips)
        self.assertIn("127.0.0.1", ips)
        self.assertLessEqual((parsed.not_valid_after_utc - parsed.not_valid_before_utc).days, 825)     # límite de Apple
        before = cert.read_bytes()
        self.assertEqual(remote.ensure_cert(d, "192.168.1.50")[0].read_bytes(), before)               # reutiliza
        self.assertNotEqual(remote.ensure_cert(d, "10.0.0.7")[0].read_bytes(), before)                # IP nueva: regenera


class QrTests(unittest.TestCase):
    def test_qr_svg_and_terminal(self):
        url = "https://192.168.1.50:8443/remote/#t=abc"
        self.assertIn("<svg", remote.qr_svg(url))
        self.assertGreater(len(remote.qr_terminal(url).splitlines()), 10)


@unittest.skipUnless(shutil.which("node"), "Node no instalado")
class JavaScriptContractTests(unittest.TestCase):
    """Lo que construye el JavaScript del móvil lo tiene que entender el Python del ordenador."""

    def build(self, with_face):
        script = f"""
        import {{ buildPayload, demoHand }} from "{(WEB / 'payload.js').as_uri()}";
        const keys = {json.dumps(FACE_KEYS)};
        const face = Array.from({{ length: 478 }}, (_, i) => ({{ x: 0.3 + i / 5000, y: 0.4 + i / 7000, z: -0.01 }}));
        const p = buildPayload({{ t: 1234.6, width: 640, height: 360, handLandmarks: demoHand(0.5, 0.5, true),
                                  faceLandmarks: {str(with_face).lower()} ? face : null, faceKeys: keys }});
        console.log(JSON.stringify(p));
        """
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_js_payload_is_accepted_by_python(self):
        frame = parse_payload(self.build(with_face=True))
        self.assertEqual(frame.hand.shape, (21, 3))
        self.assertEqual(frame.face.shape, (478, 3))
        self.assertAlmostEqual(frame.aspect, 16 / 9)
        self.assertLess(geometry.pinch_ratio(frame.hand, 8), 0.3)        # la mano demo con pellizco se mide como pellizco

    def test_js_without_face_is_accepted(self):
        self.assertIsNone(parse_payload(self.build(with_face=False)).face)


class EngineWithPhoneTests(unittest.TestCase):
    def make(self, **kw):
        hub = RemoteHub()
        backend = DryRunBackend(log=lambda *_: None)
        backend.screen_size = lambda: SCREEN
        eng = Engine(dry_run=True, window=FakeWindow(), backend=backend, remote=hub, log=lambda *_: None, **kw)
        self.assertTrue(eng.setup())
        return eng, hub, backend

    def feed(self, hub, hands, fps=60):
        """Un 'móvil' en un hilo: manda una mano por fotograma."""
        def run():
            for lm in hands:
                hub.push("movil", parse_payload(phone_payload(hand=lm)))
                time.sleep(1 / fps)
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_the_phone_moves_the_cursor_and_clicks(self):
        with sandbox():
            eng, hub, backend = self.make()
            frames = [point()] * 15 + [point(pinch="index")] * 8 + [point()] * 12
            t = self.feed(hub, frames)
            eng.run(max_frames=len(frames))
            t.join()
            kinds = [c[0] for c in backend.calls]
            self.assertIn("move", kinds)
            self.assertIn(("click", "left", 1), backend.calls)

    def test_phone_buttons_are_commands_for_the_engine(self):
        with sandbox():
            eng, hub, backend = self.make()
            hub.command("toggle_pause")
            t = self.feed(hub, [point()] * 6)
            eng.run(max_frames=6)
            t.join()
            self.assertTrue(eng.ctl.paused)

    def test_phone_is_told_the_state_and_when_to_send_the_face(self):
        with sandbox():
            eng, hub, backend = self.make()
            t = self.feed(hub, [point()] * 4)
            eng.run(max_frames=4)
            t.join()
            self.assertFalse(hub.status["need_face"])
            self.assertFalse(hub.status["paused"])
            self.assertEqual(hub.status["input_mode"], "hand")
            eng.ctl.input_mode = "gaze"
            eng.ctl.gaze = object()                                      # mirada calibrada
            eng._tell_phone()
            self.assertTrue(hub.status["need_face"])
            eng.ctl.gaze = None
            eng.start_calibration("gaze")
            eng._tell_phone()
            self.assertTrue(hub.status["need_face"])                     # calibrando la mirada también necesita la cara

    def test_waiting_banner_until_the_phone_connects(self):
        with sandbox():
            eng, hub, backend = self.make()
            eng._check_camera(False, None)
            self.assertEqual(eng._camera_issue, remote.WAITING)
            hub.push("movil", parse_payload(phone_payload()))
            eng._check_camera(True, None)
            self.assertIsNone(eng._camera_issue)

    def test_state_file_publishes_the_remote_status(self):
        with sandbox():
            from aircontrol import ipc
            eng, hub, backend = self.make()
            hub.push("movil", parse_payload(phone_payload()))
            eng.step(np.zeros((480, 640, 3), np.uint8), 10.0)
            eng._publish(10.0, eng.ctl.status)
            state = ipc.read_json(ipc.STATE_PATH)
            self.assertTrue(state["remote"]["connected"])

    def test_gaze_calibration_and_pointing_work_with_the_phone_face(self):
        """El móvil manda la cara (solo 15 puntos): la calibración y el cursor de mirada funcionan igual que con la webcam."""
        with sandbox() as d:
            eng, hub, backend = self.make()
            eng.faces = RemoteFaces(eng.camera)
            eng.start_calibration("gaze")
            t, now = 0.0, 0.0
            while eng.cal is not None and t < 40:
                tx, ty = eng.cal["obj"].target_px() if eng.cal["done_at"] is None else (SCREEN[0] / 2, SCREEN[1] / 2)
                hub.push("movil", parse_payload(phone_payload(face=looking_at(tx / SCREEN[0], ty / SCREEN[1]))))
                eng.step(eng.camera.read()[1], now)
                now += 1 / 30
                t += 1 / 30
            self.assertIsNotNone(eng.ctl.gaze)
            self.assertTrue((d / "gaze.json").exists())


if __name__ == "__main__":
    unittest.main()

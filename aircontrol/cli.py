import argparse
import json
import re
import sys
import time

from . import __version__, system
from .config import APP_NAME, DATA_DIR, GESTURES_PATH, IS_FROZEN, MODEL_PATH, PROFILES_PATH, Settings
from .gestures import RESERVED, GestureStore, too_similar
from .profiles import Profiles
from .runtime import setup_logging, show_error

WINDOW = APP_NAME
NAME_RE = re.compile(r"^[a-z0-9_]{2,24}$")


def _window_closed():
    import cv2
    return cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1


def cmd_run(args):
    from .engine import Engine
    return Engine(dry_run=args.dry_run, preview=not args.no_preview, camera_index=args.camera).run()


def cmd_calibrate(args):
    from .engine import Engine
    return Engine(dry_run=True, preview=True, camera_index=args.camera, calibrate=args.what,
                  exit_after_calibration=True).run()


def cmd_record(args):
    import cv2
    from . import recording
    from .camera import Camera
    from .hands import HandTracker

    settings = Settings.load()
    camera, tracker = Camera(settings), HandTracker()
    times, hands, start = [], [], time.monotonic()
    print(f"Grabando {args.seconds:g} s: haz los gestos que te dan problemas (Esc para parar).")
    try:
        while time.monotonic() - start < args.seconds:
            ok, frame = camera.read()
            if not ok:
                continue
            now = time.monotonic() - start
            lm = tracker.detect(frame, now * 1000)
            times.append(now)
            hands.append(lm)
            cv2.putText(frame, f"REC {now:4.1f}s", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, frame)
            if cv2.waitKey(1) & 0xFF == 27:
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()
    recording.save(args.file, times, hands)
    seen = sum(h is not None for h in hands)
    print(f"✅ {len(times)} fotogramas ({seen} con mano) guardados en {args.file}")
    print(f"Reprodúcelo con otros ajustes:  aircontrol replay {args.file} --set pinch_on=0.25")
    return 0


def cmd_replay(args):
    from . import recording

    times, hands = recording.load(args.file)
    settings = Settings.load()
    changes = {}
    for item in args.set or []:
        key, _, raw = item.partition("=")
        try:
            changes[key] = json.loads(raw)
        except ValueError:
            changes[key] = raw
    errors = settings.apply(changes)
    if errors:
        print("\n".join(errors))
        return 1
    events, summary = recording.replay(times, hands, settings, screen=system.screen_size())
    shown = [e for e in events if e[0] != "move"]
    duration = times[-1] - times[0] if len(times) > 1 else 0
    print(f"{len(times)} fotogramas · {duration:.1f} s · ajustes cambiados: {changes or 'ninguno'}")
    for kind, n in sorted(summary.items()):
        print(f"  {kind:14s} {n}")
    print("Eventos (sin movimientos):", shown[:40] if shown else "ninguno")
    return 0


def cmd_voice_test(args):
    """Prueba el micrófono y la voz SIN ejecutar nada: muestra lo que entendería."""
    from .voice import commands
    from .voice.audio import Microphone, Segmenter
    from .voice.llm import Interpreter
    from .voice.stt import Transcriber

    settings = Settings.load()
    stt = Transcriber(settings.whisper_model, settings.voice_language)
    stt.warm()
    llm = Interpreter(settings.ollama_model) if not args.no_llm else None
    mic, seg = Microphone(), Segmenter()
    mic.start()
    print("Habla (Ctrl+C para salir). Solo muestra lo entendido, no ejecuta nada.")
    try:
        while True:
            block = mic.read()
            audio = seg.feed(block) if block is not None else None
            if audio is None:
                continue
            text = stt.transcribe(audio)
            if not text:
                continue
            action = commands.parse(text)
            source = "reglas"
            if action is None and llm:
                action, source = llm.interpret(text, time.monotonic()), "Ollama"
            print(f"«{text}» -> {action or 'no entendido'}  ({source if action else '-'})")
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop()
    return 0


def cmd_check_input(args):
    """Comprueba de verdad (no en simulación) que el ratón, el teclado, las teclas multimedia y abrir apps funcionan."""
    import subprocess
    import cv2
    from .system.backend import PynputBackend

    if not system.input_trusted(prompt=True):
        print(system.ACCESSIBILITY_HELP)
        return 2
    backend, results = PynputBackend(), []

    def report(name, ok, detail=""):
        results.append(ok)
        print(f"{'✅' if ok else '❌'} {name}{' — ' + detail if detail else ''}", flush=True)

    def cursor():
        if sys.platform == "darwin":
            import Quartz
            p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            return int(p.x), int(p.y)
        import ctypes
        import ctypes.wintypes
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def pump(seconds=0.25):
        end = time.time() + seconds
        codes = []
        while time.time() < end:
            k = cv2.waitKey(10)
            if k != -1:
                codes.append(k & 0xFF)
        return codes

    print("Esta prueba mueve el ratón y pulsa teclas durante ~15 s sobre una ventana de prueba. No toques nada.\n")
    # 1) mover el cursor de verdad
    ok = True
    for target in ((300, 300), (600, 400), (450, 250)):
        backend.move(*target)
        time.sleep(0.15)
        ok = ok and cursor() == target
    report("mover el cursor", ok, f"último leído {cursor()}")

    # 2) clics sobre una ventana que los registra
    seen = []
    win = "AirControl · prueba de entrada"
    canvas = __import__("numpy").full((300, 500, 3), 40, "uint8")
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.imshow(win, canvas)
    cv2.moveWindow(win, 200, 150)
    names = {cv2.EVENT_LBUTTONDOWN: "izq-abajo", cv2.EVENT_LBUTTONUP: "izq-arriba", cv2.EVENT_LBUTTONDBLCLK: "doble",
             cv2.EVENT_RBUTTONDOWN: "der-abajo", cv2.EVENT_MOUSEMOVE: "mover"}
    pos = {}

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_MOUSEMOVE:
            pos["last"] = (x, y)
        elif event in names:
            seen.append((names[event], bool(flags & cv2.EVENT_FLAG_LBUTTON)))

    cv2.setMouseCallback(win, on_mouse)
    pump(0.8)
    origin = None                                         # esquina de la imagen en coordenadas de pantalla
    for sx, sy in ((400, 300), (500, 350), (350, 280), (420, 330), (600, 400)):
        pos.clear()
        backend.move(sx, sy)
        pump(0.3)
        if "last" in pos:
            origin = (sx - pos["last"][0], sy - pos["last"][1])
            break
    report("la ventana de prueba recibe el cursor", origin is not None,
           f"origen {origin}" if origin else "no llegó ningún evento: ¿la terminal tiene permiso de Accesibilidad?")
    if origin:
        at = (origin[0] + 250, origin[1] + 150)
        backend.move(*at)
        pump(0.3)
        seen.clear(); backend.click("left", 1); pump(0.4)
        report("clic izquierdo (click left 1)", ("izq-abajo", True) in seen and any(n == "izq-arriba" for n, _ in seen), str(seen))
        counts = []
        monitor = None
        if sys.platform == "darwin":
            # El backend de ventanas de OpenCV en macOS nunca emite EVENT_LBUTTONDBLCLK: se lee el clickCount que
            # recibe la propia app, que es lo que ve cualquier programa.
            from AppKit import NSEvent, NSEventMaskLeftMouseDown

            def on_down(event):
                counts.append(int(event.clickCount()))
                return event
            monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskLeftMouseDown, on_down)
        seen.clear(); backend.click("left", 2); pump(0.5)
        if monitor is not None:
            NSEvent.removeMonitor_(monitor)
            report("doble clic (click left 2)", counts == [1, 2], f"clickCount recibido por la app: {counts}")
        else:
            report("doble clic (click left 2)", any(n == "doble" for n, _ in seen), str(seen))
        seen.clear(); backend.click("right", 1); pump(0.4)
        report("clic derecho", any(n == "der-abajo" for n, _ in seen), str(seen))
        seen.clear(); backend.press("left"); pump(0.15)
        backend.move(at[0] + 40, at[1] + 20); pump(0.15)
        backend.release("left"); pump(0.3)
        report("arrastrar (press + move + release)", ("izq-abajo", True) in seen and any(n == "izq-arriba" for n, _ in seen),
               str(seen))
        # 3) teclado: la ventana tiene el foco tras el clic
        backend.click("left", 1); pump(0.3)
        codes = []
        backend.type_text("hola"); codes += pump(0.6)
        report("escribir texto (type_text)", bytes(codes).decode("latin1").count("hola") >= 1 or
               bytes(c for c in codes if 32 <= c < 127).decode().endswith("hola"), f"la ventana recibió {codes}")
        backend.key_combo(["enter"]); codes = pump(0.5)
        report("tecla suelta (key_combo enter)", 13 in codes or 10 in codes, f"recibió {codes}")
    cv2.destroyWindow(win)
    cv2.waitKey(1)

    # 4) teclas multimedia: se observa el volumen real del sistema (y se restaura)
    if sys.platform == "darwin":
        def vol():
            out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True)
            return int(out.stdout.strip()) if out.stdout.strip().isdigit() else None

        def muted():
            out = subprocess.run(["osascript", "-e", "output muted of (get volume settings)"], capture_output=True, text=True)
            return out.stdout.strip()
        v0 = vol()
        backend.media("volume_down"); time.sleep(0.5)
        v1 = vol()
        report("media: bajar volumen", v0 is not None and v1 is not None and v1 < v0 or v0 == 0, f"{v0} -> {v1}")
        backend.media("volume_up"); time.sleep(0.5)
        v2 = vol()
        report("media: subir volumen", v1 is not None and v2 is not None and v2 > v1, f"{v1} -> {v2}")
        m0 = muted(); backend.media("mute"); time.sleep(0.5); m1 = muted(); backend.media("mute"); time.sleep(0.5)
        report("media: silenciar", m0 != m1, f"{m0} -> {m1}")
        print("ℹ️  play_pause no se puede observar sin un reproductor abierto.")
    # 5) abrir aplicaciones y direcciones
    from .system import open_target
    if sys.platform == "darwin":
        app = "Calculator"
        subprocess.run(["osascript", "-e", f'tell application "{app}" to quit'], capture_output=True)
        time.sleep(0.5)
        open_target(app); time.sleep(2.0)
        running = subprocess.run(["pgrep", "-x", app], capture_output=True).returncode == 0
        report("abrir una aplicación (open:Calculator)", running)
        subprocess.run(["osascript", "-e", f'tell application "{app}" to quit'], capture_output=True)
    if not args.no_url:
        try:
            open_target("https://example.com")
            report("abrir una dirección (open:https://example.com)", True, "se abre una pestaña en tu navegador")
        except Exception as e:
            report("abrir una dirección", False, str(e))
    failed = results.count(False)
    print(f"\n{'TODO FUNCIONA' if not failed else f'{failed} comprobación(es) fallaron'} ({len(results)} hechas)")
    return 0 if not failed else 1


def cmd_check_hand(args):
    """Prueba guiada con TU mano (en simulación: no mueve nada): qué gestos se reconocen y, si no, por qué."""
    import cv2
    from .camera import Camera
    from .controller import Controller
    from .diagnose import STEPS, StepStats, measures, suggest
    from .hands import HandTracker
    from .system.backend import DryRunBackend
    from .ui import FONT, draw

    steps = [st for st in STEPS if not args.only or st[0] in args.only.split(",")]
    settings = Settings.load()
    backend = DryRunBackend(log=lambda *_: None)
    ctl = Controller(backend, settings, GestureStore(GESTURES_PATH), Profiles.load(PROFILES_PATH), log=lambda *_: None)
    camera, tracker = Camera(settings), HandTracker()
    countdown, timeout, start, report = 4.0, 16.0, time.monotonic(), []

    def passed(step, calls):
        kinds = [c[0] for c in calls]
        if step == "point":
            return kinds.count("move") >= 15
        if step == "click":
            return ("click", "left", 1) in calls
        if step == "double":
            return ("click", "left", 2) in calls
        if step == "drag":
            return "press" in kinds and "release" in kinds
        if step == "right":
            return ("click", "right", 1) in calls
        if step == "scroll":
            return "scroll" in kinds
        return any(c[0] == "media" for c in calls)

    def show(frame, lm, status, n, text, banner, color=(0, 200, 255)):
        img = draw(frame, lm, status, settings)
        cv2.rectangle(img, (0, 40), (img.shape[1], 110), (20, 20, 20), -1)
        cv2.putText(img, f"Paso {n}/{len(steps)}  {banner}", (10, 66), FONT, 0.6, color, 2, cv2.LINE_AA)
        cv2.putText(img, text.encode("ascii", "replace").decode()[:72], (10, 98), FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(WINDOW, img)
        return cv2.waitKey(1) & 0xFF == 27

    print("Prueba de tu mano: lee la instrucción, espera la cuenta atrás y haz el gesto (Esc para salir).")
    print("No se mueve nada de verdad.\n")
    try:
        for n, (step, text) in enumerate(steps, 1):
            ctl.mouse.update(None, time.monotonic() + 1, active=False)
            t0 = time.monotonic()
            while time.monotonic() - t0 < countdown:                           # cuenta atrás para prepararse
                good, frame = camera.read()
                if not good:
                    continue
                now = time.monotonic()
                lm = tracker.detect(frame, (now - start) * 1000)
                if show(frame, lm, ctl.process(lm, now), n, text, f"prepárate... {countdown - (now - t0):.0f}", (120, 200, 120)):
                    return 1
            backend.calls.clear()
            stats, t0, ok = StepStats(), time.monotonic(), False
            while time.monotonic() - t0 < timeout and not ok:
                good, frame = camera.read()
                if not good:
                    continue
                now = time.monotonic()
                lm = tracker.detect(frame, (now - start) * 1000)
                before = len(backend.calls)
                status = ctl.process(lm, now)
                stats.add(lm, status.mode, ctl.mouse.debug if lm is not None else None, backend.calls[before:], now)
                ok = passed(step, list(backend.calls))
                if show(frame, lm, status, n, text, f"¡AHORA! {timeout - (now - t0):.0f}s"):
                    return 1
            report.append((step, text, ok, stats))
            print(f"{'✅' if ok else '❌'} {n}. {text}", flush=True)
            print(f"     medido: {measures(stats)}")
            for line in ([] if ok else suggest(step, stats, settings)):
                print(f"     → {line}")
            t_end = time.monotonic() + 1.0
            while time.monotonic() < t_end:
                camera.read(); cv2.waitKey(1)
    finally:
        camera.release()
        cv2.destroyAllWindows()
    failed = [r for r in report if not r[2]]
    print(f"\n{len(report) - len(failed)}/{len(report)} gestos reconocidos." +
          ("" if not failed else " Repite solo los que fallaron:  check-hand --only " + ",".join(r[0] for r in failed)))
    return 0 if not failed else 1


def cmd_playground(_args):
    """Abre una página local con botones, arrastre, scroll, texto y casillas que muestra cada evento que recibe."""
    import webbrowser
    from .launcher import WEB_DIR
    page = WEB_DIR / "playground.html"
    print(f"Zona de pruebas: {page}")
    webbrowser.open(page.as_uri())
    return 0


def cmd_app(args):
    from .launcher import run_app
    return run_app(start_engine=not args.no_engine, open_settings=args.open_settings)


def cmd_train(args):
    import cv2
    from .camera import Camera
    from .geometry import gesture_features
    from .hands import HandTracker
    from .ui import FONT

    name = args.name.lower()
    if name in RESERVED:
        print(f"'{name}' es un nombre reservado ({', '.join(RESERVED)}).")
        return 1
    if not NAME_RE.match(name):
        print("El nombre debe tener 2-24 caracteres: letras minúsculas, números o _ (ej. 'ok', 'tres_dedos').")
        return 1

    settings = Settings.load()
    store = GestureStore(GESTURES_PATH)
    if args.replace:
        store.remove(name)
    camera, tracker = Camera(settings), HandTracker()
    new, countdown_until, last, start = [], time.monotonic() + 3.0, 0.0, time.monotonic()
    print(f"Entrenando '{name}': pon la mano en el gesto y muévela un poco entre muestras ({args.samples}).")
    try:
        while len(new) < args.samples:
            ok, frame = camera.read()
            if not ok:
                continue
            now = time.monotonic()
            lm = tracker.detect(frame, (now - start) * 1000)
            if now < countdown_until:
                text = f"Prepara el gesto... {countdown_until - now:.0f}"
            else:
                text = f"Muestra {len(new)}/{args.samples}" if lm is not None else "No veo la mano"
                if lm is not None and now - last > 0.25:
                    new.append(gesture_features(lm))
                    last = now
            cv2.putText(frame, text, (20, 40), FONT, 0.9, (0, 255, 200), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27) or _window_closed():
                print("Cancelado: no se ha guardado nada.")
                return 1
    finally:
        camera.release()
        cv2.destroyAllWindows()

    for feat in new:
        store.add(name, feat)
    store.save()
    print(f"✅ '{name}' guardado con {len(store.samples[name])} muestras ({GESTURES_PATH}).")
    clash = too_similar(store, name, Settings.load().gesture_threshold)
    if clash:
        print(f"⚠️  Se parece mucho a '{clash}': podrían confundirse. Reentrena con --replace o elige otra pose.")
    print(f"Asócialo a una acción en {PROFILES_PATH}:  \"{name}\": \"key:mod+c\"")
    return 0


def cmd_gestures(args):
    store = GestureStore(GESTURES_PATH)
    if args.remove:
        print("Borrado." if store.remove(args.remove) else f"No existe '{args.remove}'.")
        store.save()
        return 0
    for n in store.names():
        print(f"  {n}  ({len(store.samples[n])} muestras)")
    if not store.names():
        print("  (sin gestos propios)")
    print(f"Integrados: {', '.join(g for g in RESERVED if g != 'fist')} · puño sostenido = pausa")
    return 0


def cmd_doctor(_args):
    settings, ok = Settings.load(), True

    def line(good, text):
        nonlocal ok
        ok = ok and good
        print(f"{'✅' if good else '❌'} {text}")

    print(f"{APP_NAME} {__version__} · Python {sys.version.split()[0]} · {sys.platform}")
    line(MODEL_PATH.exists(), f"modelo de manos: {MODEL_PATH}")
    trusted = system.input_trusted(prompt=False)
    line(trusted, "permiso para controlar ratón y teclado" + ("" if trusted else "\n" + system.ACCESSIBILITY_HELP))
    try:
        from .camera import Camera
        cam = Camera(settings)
        got, _ = cam.read()
        cam.release()
        line(got, f"cámara {settings.camera_index}")
    except Exception as e:
        line(False, f"cámara: {e}")
    print(f"ℹ️  pantalla {system.screen_size()} · app en primer plano: {system.active_app() or '?'}")
    profiles = Profiles.load(PROFILES_PATH)
    line(not profiles.errors, f"perfiles: {PROFILES_PATH}" + "".join(f"\n     {e}" for e in profiles.errors))
    import importlib.util
    from .config import GAZE_PATH
    defaults = Settings()
    calibrated = any(getattr(settings, k) != getattr(defaults, k) for k in ("region_x0", "region_y0", "region_x1", "region_y1"))
    print(f"ℹ️  mano: región {'calibrada' if calibrated else 'por defecto (aircontrol calibrate hand)'} · "
          f"mirada: {'calibrada' if GAZE_PATH.exists() else 'sin calibrar (aircontrol calibrate gaze)'}")
    voice_deps = all(importlib.util.find_spec(m) for m in ("faster_whisper", "sounddevice"))
    if settings.voice_mode == "off" and not voice_deps:
        print("ℹ️  voz: desactivada y sin instalar (pip install -r requirements-voice.txt)")
    else:
        line(voice_deps, f"voz ({settings.voice_mode}): paquetes instalados")
        if voice_deps:
            try:
                import sounddevice as sd
                inputs = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
                line(bool(inputs), f"micrófono: {inputs[0] if inputs else 'no se encontró ninguno (¿permiso de Micrófono?)'}")
            except Exception as e:
                line(False, f"micrófono: {e}")
            try:
                import ollama
                from .voice.llm import pick_model
                models = [getattr(m, "model", None) or m.get("model") for m in
                          (getattr(ollama.Client(timeout=2).list(), "models", None) or [])]
                print(f"ℹ️  Ollama: {pick_model(models, settings.ollama_model) or 'sin modelos (ollama pull llama3.2)'} "
                      "(solo respaldo para órdenes que las reglas no entienden)")
            except Exception:
                print("ℹ️  Ollama: no disponible (las órdenes por reglas funcionan igual)")
    print(f"ℹ️  datos en {DATA_DIR}")
    return 0 if ok else 1


def cmd_selftest(_args):
    """Comprobación sin cámara ni permisos (para el CI sobre la app empaquetada): todo lo empaquetado carga y funciona."""
    import importlib.util

    import numpy as np
    from . import launcher
    from .controller import Controller
    from .faces import FaceTracker
    from .gaze import GazeCalibration, extract_features
    from .hands import HandTracker
    from .system.backend import DryRunBackend, PynputBackend
    from .voice import commands
    from .voice.llm import validate

    def step(name, detail=""):
        print(f"  ✔ {name}{' — ' + str(detail) if detail else ''}")

    PynputBackend()                   # comprueba que pynput y su backend del SO van dentro del instalador
    system.input_trusted(prompt=False)
    step("entrada del sistema (pynput)")
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    assert HandTracker().detect(blank, 0) is None, "el detector vio una mano en negro"
    step("rastreador de manos (MediaPipe)")
    assert FaceTracker().detect(blank, 0) is None, "el detector vio una cara en negro"
    step("rastreador de caras + iris (MediaPipe)")
    backend = DryRunBackend(log=lambda *_: None)
    profiles = Profiles()
    assert not profiles.errors, profiles.errors
    ctl = Controller(backend, Settings(), GestureStore(), profiles, log=lambda *_: None)
    lm = np.full((21, 3), 0.5, dtype=np.float32)
    ctl.process(lm, 0.0)
    ctl.set_keyboard(True)
    ctl.process(lm, 0.1)
    ctl.shutdown(1.0)
    step("controlador, teclado aéreo y perfiles")
    cal = GazeCalibration((1000, 800), settle_s=0.0, collect_s=0.1, points=[(0.5, 0.5)])
    feats, ear = extract_features(np.zeros((478, 3), dtype=np.float32) + 0.5)
    assert feats.shape == (6,)
    step("núcleo de la mirada")
    assert commands.parse("haz clic") == "click" and validate('{"action": "media:next"}') == "media:next"
    voice = all(importlib.util.find_spec(m) for m in ("faster_whisper", "sounddevice", "ctranslate2"))
    if voice:
        from .voice import stt
        stt._avoid_pyav()
        import faster_whisper  # noqa: F401
        import sounddevice  # noqa: F401
    step("voz: órdenes y respaldo", "motor de voz incluido" if voice else "motor de voz NO incluido en esta compilación")
    assert (launcher.WEB_DIR / "index.html").exists(), "falta la página de ajustes dentro del instalador"
    app = launcher.create_web_app(launcher.AppContext(launcher.EngineProcess(["true"])))
    client = app.test_client()
    assert client.get("/api/state", headers={"Host": "127.0.0.1"}).status_code == 403, "la web no exige token"
    assert launcher.make_icon("active").size == (64, 64)
    import pystray  # noqa: F401
    step("bandeja y página de ajustes", "con token")
    print(f"SELFTEST OK ({APP_NAME} {__version__}, {sys.platform}, pantalla {system.screen_size()})")
    return 0


def main(argv=None):
    setup_logging()
    parser = argparse.ArgumentParser(prog="aircontrol", description="Controla el ordenador con manos, sin tocarlo.")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="arranca el control (por defecto)")
    run.add_argument("--dry-run", action="store_true", help="no mueve el ratón ni pulsa teclas: solo lo muestra")
    run.add_argument("--no-preview", action="store_true", help="sin ventana de vista previa (Ctrl+C para salir)")
    run.add_argument("--camera", type=int, help="índice de la cámara")
    run.set_defaults(func=cmd_run)

    cal = sub.add_parser("calibrate", help="calibración guiada de la mano o de la mirada")
    cal.add_argument("what", choices=["hand", "gaze"])
    cal.add_argument("--camera", type=int)
    cal.set_defaults(func=cmd_calibrate)

    rec = sub.add_parser("record", help="graba tu mano para afinar ajustes sin cámara")
    rec.add_argument("file")
    rec.add_argument("--seconds", type=float, default=30)
    rec.set_defaults(func=cmd_record)

    rep = sub.add_parser("replay", help="reproduce una grabación con otros ajustes (--set clave=valor)")
    rep.add_argument("file")
    rep.add_argument("--set", action="append", metavar="CLAVE=VALOR")
    rep.set_defaults(func=cmd_replay)

    vt = sub.add_parser("voice-test", help="prueba micrófono y voz sin ejecutar nada")
    vt.add_argument("--no-llm", action="store_true")
    vt.set_defaults(func=cmd_voice_test)

    ci = sub.add_parser("check-input", help="prueba REAL de ratón, teclado, teclas multimedia y abrir apps")
    ci.add_argument("--no-url", action="store_true", help="no abrir una pestaña del navegador")
    ci.set_defaults(func=cmd_check_input)

    ch = sub.add_parser("check-hand", help="prueba guiada con tu mano: qué gestos se reconocen y por qué no")
    ch.add_argument("--only", metavar="PASOS", help="solo estos pasos, separados por comas: point,click,double,drag,right,scroll,thumb")
    ch.set_defaults(func=cmd_check_hand)

    sub.add_parser("playground", help="abre una página de pruebas que muestra cada clic, arrastre, scroll y tecla").set_defaults(func=cmd_playground)

    app = sub.add_parser("app", help="icono en la barra de menús + página de ajustes")
    app.add_argument("--no-engine", action="store_true", help="no arrancar el control automáticamente")
    app.add_argument("--open-settings", action="store_true")
    app.set_defaults(func=cmd_app)

    train = sub.add_parser("train", help="enseña un gesto nuevo con unas muestras")
    train.add_argument("name")
    train.add_argument("--samples", type=int, default=15)
    train.add_argument("--replace", action="store_true", help="descarta las muestras anteriores de ese nombre")
    train.set_defaults(func=cmd_train)

    gest = sub.add_parser("gestures", help="lista o borra gestos entrenados")
    gest.add_argument("--remove", metavar="NOMBRE")
    gest.set_defaults(func=cmd_gestures)

    sub.add_parser("doctor", help="comprueba cámara, permisos y configuración").set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    if args.selftest:
        return cmd_selftest(args)
    if args.command is None:                                       # doble clic en la app: bandeja + control
        args = parser.parse_args(["app"] if IS_FROZEN else ["run"])
    return args.func(args)

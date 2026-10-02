"""Vista previa: esqueleto de la mano, región activa, modo actual y progreso de los gestos mantenidos.
Solo ASCII: las fuentes de OpenCV no dibujan tildes."""
import cv2
import numpy as np

from .geometry import CONNECTIONS
from .keyboard import TEXT_H

MODE_COLORS = {"idle": (170, 170, 170), "point": (0, 255, 120), "pinch": (0, 200, 255),
               "drag": (0, 140, 255), "scroll": (255, 200, 0), "paused": (80, 80, 255)}
MODE_LABELS = {"idle": "REPOSO", "point": "PUNTERO", "pinch": "CLIC", "drag": "ARRASTRE",
               "scroll": "SCROLL", "paused": "PAUSA"}
FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw(frame, lm, status, settings):
    h, w = frame.shape[:2]
    color = MODE_COLORS.get(status.mode, (255, 255, 255))

    # Región de la cámara que cubre toda la pantalla.
    cv2.rectangle(frame, (int(settings.region_x0 * w), int(settings.region_y0 * h)),
                  (int(settings.region_x1 * w), int(settings.region_y1 * h)), (90, 90, 90), 1)

    if lm is not None:
        pts = [(int(p[0] * w), int(p[1] * h)) for p in lm]
        for a, b in CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], color, 1, cv2.LINE_AA)
        for pt in pts:
            cv2.circle(frame, pt, 2, (255, 255, 255), -1)
        if status.hold > 0.15:                                       # anillo de progreso del gesto mantenido
            cx, cy = pts[9]
            radius = max(30, int(abs(pts[0][1] - pts[9][1]) * 1.2))
            cv2.circle(frame, (cx, cy), radius, (60, 60, 60), 4, cv2.LINE_AA)
            cv2.ellipse(frame, (cx, cy), (radius, radius), -90, 0, int(360 * status.hold), (0, 200, 255), 6, cv2.LINE_AA)

    if status.dwell > 0.02:                                          # clic por permanencia en curso (mirada)
        cx, cy = w - 40, 76
        cv2.circle(frame, (cx, cy), 22, (60, 60, 60), 4, cv2.LINE_AA)
        cv2.ellipse(frame, (cx, cy), (22, 22), -90, 0, int(360 * status.dwell), (0, 220, 255), 5, cv2.LINE_AA)

    cv2.rectangle(frame, (0, 0), (w, 34), (20, 20, 20), -1)
    label = MODE_LABELS.get(status.mode, status.mode)
    if status.input_mode == "gaze" and status.mode != "paused":
        label, color = "MIRADA", (255, 160, 60)
    cv2.putText(frame, label, (10, 23), FONT, 0.7, color, 2, cv2.LINE_AA)
    cv2.putText(frame, f"{status.app or '-'}  [{status.profile}]", (150, 23), FONT, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
    if status.pose:
        cv2.putText(frame, status.pose, (w - 160, 23), FONT, 0.55, (0, 200, 255), 1, cv2.LINE_AA)
    if status.last_action:
        cv2.putText(frame, status.last_action.encode("ascii", "replace").decode(), (10, h - 40), FONT, 0.55,
                    (0, 255, 200), 1, cv2.LINE_AA)
    hint = "Puno 1s = reanudar" if status.paused else "Puno = pausa  |  Menique arriba = teclado"
    cv2.putText(frame, f"{hint}   |   q / ESC = salir", (10, h - 12), FONT, 0.45, (170, 170, 170), 1, cv2.LINE_AA)
    return frame


def _center_text(img, text, cx, cy, scale, color, thickness=1):
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, thickness)
    cv2.putText(img, text, (int(cx - tw / 2), int(cy + th / 2)), FONT, scale, color, thickness, cv2.LINE_AA)


def draw_keyboard(kb, uv, now, size=(1100, 400), mode="hand"):
    """Teclado aéreo como imagen: lo escrito y la app de destino arriba, sugerencias, y teclas con la tecla bajo el
    puntero resaltada y su barra de progreso de permanencia."""
    w, h = size
    img = np.full((h, w, 3), 28, "uint8")
    progress = kb.dwell_progress(now)

    # Franja de texto: lo último escrito (se ve aunque el foco esté en otra app) y adónde va.
    th = int(TEXT_H * h)
    cv2.rectangle(img, (0, 0), (w, th), (20, 20, 20), -1)
    target = kb.target or "la app en primer plano"
    cv2.putText(img, f"Escribiendo en: {target}", (12, int(th * 0.68)), FONT, 0.55, (0, 200, 255), 1, cv2.LINE_AA)
    shown = kb.typed[-34:] or ("pellizca una tecla" if mode == "hand" else "mira una tecla 1 s")
    (tw, _), _ = cv2.getTextSize(shown, FONT, 0.7, 2)
    cv2.putText(img, shown, (max(w - tw - 14, int(w * 0.42)), int(th * 0.7)), FONT, 0.7,
                (240, 240, 240) if kb.typed else (120, 120, 120), 2 if kb.typed else 1, cv2.LINE_AA)

    for key, label, x0, y0, x1, y1 in kb.layout():
        p0, p1 = (int(x0 * w) + 3, int(y0 * h) + 3), (int(x1 * w) - 3, int(y1 * h) - 3)
        hover = key == kb.hover
        special = key in ("shift", "symbols", "letters", "backspace", "enter", "close", "space", "left", "right")
        if key.startswith("suggest:"):
            base = (70, 55, 35)
        elif key == "shift" and kb.shift:
            base = (0, 130, 220)
        else:
            base = (62, 62, 62) if special else (48, 48, 48)
        cv2.rectangle(img, p0, p1, (90, 140, 40) if hover else base, -1)
        if hover and progress > 0:
            cv2.rectangle(img, (p0[0], p1[1] - 10), (p0[0] + int((p1[0] - p0[0]) * progress), p1[1]), (0, 220, 255), -1)
        cv2.rectangle(img, p0, p1, (110, 110, 110), 1)
        text = kb.display(label)
        scale = 0.9 if len(text) > 3 else 1.3
        ascii_text = text.encode("ascii", "replace").decode() if not text.isalnum() and len(text) > 1 else text
        _center_text(img, {"⌫": "<-", "⏎": "OK", "⇧": "^", "←": "<", "→": ">", "✕": "X", "123": "123", "abc": "abc"}.get(text, ascii_text),
                     (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, scale, (240, 240, 240), 2)
    if uv is not None and 0 <= uv[0] <= 1 and 0 <= uv[1] <= 1:
        cv2.circle(img, (int(uv[0] * w), int(uv[1] * h)), 9, (0, 220, 255), 2, cv2.LINE_AA)
    return img


def draw_debug(frame, mouse, status):
    """HUD de diagnóstico (tecla d): valores en vivo para afinar los umbrales con tu mano."""
    d = mouse.debug or {}
    lines = [f"modo {status.mode}  |  entrada {status.input_mode}"]
    if d:
        ext = d["ext"]
        lines += [f"pellizco indice {d['left']:.2f}  corazon {d['right']:.2f}   (activa < pinch_on, suelta > pinch_off)",
                  f"extension ind {ext[0]:.2f} cor {ext[1]:.2f} anu {ext[2]:.2f} men {ext[3]:.2f}   (>1.08 extendido)",
                  f"dedos {d['fingers']}  (pulgar, ind, cor, anu, men)"]
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (10, 60 + i * 20), FONT, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return frame


def draw_calibration(size, title, subtitle, target_px, progress, phase, camera=None, face_ok=True, footer=""):
    """Pantalla completa de calibración: objetivo con anillo de progreso, instrucciones y miniatura de la cámara."""
    w, h = size
    img = np.full((h, w, 3), 18, "uint8")
    _center_text(img, title, w / 2, h * 0.42, 1.1, (230, 230, 230), 2)
    _center_text(img, subtitle, w / 2, h * 0.42 + 40, 0.7, (170, 170, 170), 1)
    if footer:
        _center_text(img, footer, w / 2, h - 40, 0.6, (120, 200, 255), 1)
    if target_px is not None:
        cx, cy = int(target_px[0]), int(target_px[1])
        color = (0, 200, 255) if phase == "collect" else (120, 120, 120)
        cv2.circle(img, (cx, cy), 34, (70, 70, 70), 3, cv2.LINE_AA)
        cv2.ellipse(img, (cx, cy), (34, 34), -90, 0, int(360 * progress), color, 6, cv2.LINE_AA)
        cv2.circle(img, (cx, cy), 7, (255, 255, 255), -1, cv2.LINE_AA)
    if camera is not None:
        small = cv2.resize(camera, (240, 180))
        x0, y0 = w // 2 - 120, int(h * 0.62)
        img[y0:y0 + 180, x0:x0 + 240] = small
        cv2.rectangle(img, (x0, y0), (x0 + 240, y0 + 180), (0, 200, 0) if face_ok else (0, 0, 220), 2)
    return img

// Funciones puras (sin DOM) para construir lo que el móvil envía al ordenador. Se prueban con Node.

/** Landmarks de MediaPipe [{x,y,z}, ...] -> lista plana de 63 números, o null. */
export function flattenHand(landmarks) {
  if (!landmarks || landmarks.length !== 21) return null;
  const out = [];
  for (const p of landmarks) out.push(round(p.x), round(p.y), round(p.z));
  return out;
}

/** Solo los puntos de la cara que usa la mirada: {índice: [x, y, z]}. null si falta alguno. */
export function pickFace(landmarks, keys) {
  if (!landmarks || landmarks.length < 478 || !keys || !keys.length) return null;
  const out = {};
  for (const k of keys) {
    const p = landmarks[k];
    if (!p) return null;
    out[k] = [round(p.x), round(p.y), round(p.z)];
  }
  return out;
}

export function buildPayload({ t, width, height, handLandmarks, faceLandmarks, faceKeys }) {
  return { t: Math.round(t), w: width, h: height, hand: flattenHand(handLandmarks), face: pickFace(faceLandmarks, faceKeys) };
}

/** Mano sintética "señalando" (misma forma que la de las pruebas de Python) para el modo demostración. */
export function demoHand(cx, cy, pinch) {
  const lm = Array.from({ length: 21 }, () => ({ x: 0.5, y: 0.5, z: 0 }));
  lm[0] = { x: 0.5, y: 0.9, z: 0 };
  const fingers = [[5, 6, 8, 0.44, true], [9, 10, 12, 0.5, false], [13, 14, 16, 0.56, false], [17, 18, 20, 0.62, false]];
  for (const [base, pip, tip, x, up] of fingers) {
    lm[base] = { x, y: 0.65, z: 0 };
    lm[pip] = { x, y: 0.55, z: 0 };
    lm[tip] = { x, y: up ? 0.3 : 0.7, z: 0 };
  }
  lm[2] = { x: 0.4, y: 0.8, z: 0 };
  lm[3] = { x: 0.38, y: 0.8, z: 0 };
  lm[4] = pinch ? { x: lm[8].x + 0.01, y: lm[8].y + 0.02, z: 0 } : { x: 0.52, y: 0.78, z: 0 };
  return lm.map((p) => ({ x: p.x + (cx - 0.44), y: p.y + (cy - 0.5), z: p.z }));
}

function round(v) { return Math.round(v * 100000) / 100000; }

import { buildPayload, demoHand } from "./payload.js";

const $ = (id) => document.getElementById(id);
const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const params = new URLSearchParams(location.search);
const token = new URLSearchParams(location.hash.slice(1)).get("t") || "";
const demo = params.has("demo");
let clientId = null;
try { clientId = sessionStorage.getItem("cid"); } catch (e) {}
if (!clientId) { clientId = Math.random().toString(36).slice(2) + Date.now().toString(36); try { sessionStorage.setItem("cid", clientId); } catch (e) {} }
const headers = { "X-Token": token, "X-Client": clientId, "Content-Type": "application/json" };

let faceKeys = [], status = {}, sending = false, running = false;
let video = $("video"), canvas = $("overlay"), ctx2d = canvas.getContext("2d");
let handModel = null, faceModel = null, vision = null, fileset = null, lastTs = 0, lastVideoTime = -1, fps = 0, lastFrameAt = 0;

function say(text, kind = "") { const el = $("state"); el.textContent = text; el.className = "pill " + kind; }

async function api(path, body) {
  const res = await fetch(path, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = {}; try { data = await res.json(); } catch (e) {}
  return { ok: res.ok, status: res.status, data };
}

async function createModel(Cls, file, extra) {
  let lastError = null;
  for (const delegate of ["GPU", "CPU"]) {           // la GPU falla en algunos móviles: se reintenta en CPU
    try {
      return await Cls.createFromOptions(fileset, { baseOptions: { modelAssetPath: `/remote/models/${file}`, delegate }, runningMode: "VIDEO", ...extra });
    } catch (e) { lastError = e; }
  }
  throw lastError;
}

async function loadMediaPipe() {
  say("Cargando MediaPipe (solo la primera vez)…");
  vision = await import(`${CDN}/vision_bundle.mjs`);
  fileset = await vision.FilesetResolver.forVisionTasks(`${CDN}/wasm`);
  handModel = await createModel(vision.HandLandmarker, "hand_landmarker.task", { numHands: 1 });
}

async function ensureFaceModel() {
  if (!faceModel && vision) faceModel = await createModel(vision.FaceLandmarker, "face_landmarker.task", { numFaces: 1 });
}

async function start() {
  $("start").disabled = true;
  try {
    if (!token) throw new Error("Falta el token: abre la dirección completa del QR.");
    const hello = await api("/api/hello");
    if (!hello.ok) throw new Error(hello.status === 403 ? "Token no válido: escanea el QR otra vez." : "No se pudo hablar con el ordenador.");
    faceKeys = hello.data.face_keys;
    $("pc").textContent = `Conectado a ${hello.data.name || "tu ordenador"}`;
    if (!demo) {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
        throw new Error("Este navegador no da acceso a la cámara en esta dirección (necesita HTTPS: acepta el certificado).");
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      video.srcObject = stream;
      await video.play();
      await loadMediaPipe();
    }
    try { if (navigator.wakeLock) await navigator.wakeLock.request("screen"); } catch (e) {}    // que la pantalla no se apague
    running = true;
    $("start").hidden = true;
    $("controls").hidden = false;
    say("Enviando al ordenador", "ok");
    schedule();
  } catch (e) {
    say(e.message || String(e), "bad");
    $("start").disabled = false;
  }
}

function draw(hand, face) {
  const w = canvas.width = video.videoWidth || 640, h = canvas.height = video.videoHeight || 480;
  ctx2d.clearRect(0, 0, w, h);
  ctx2d.fillStyle = "#00ffb4";
  for (const lm of [hand, face && [face[468], face[473]]]) {
    if (!lm) continue;
    for (const p of lm) { if (p) { ctx2d.beginPath(); ctx2d.arc(p.x * w, p.y * h, 4, 0, 6.3); ctx2d.fill(); } }
  }
}

// Un aviso por cada fotograma real de la cámara (no depende de la pantalla); sin eso, requestAnimationFrame; en la demo, un temporizador.
function schedule() {
  if (!running) return;
  if (demo) setTimeout(tick, 33);
  else if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(tick);
  else requestAnimationFrame(tick);
}

async function tick() {
  if (!running) return;
  try {
    const now = performance.now();
    let handLandmarks = null, faceLandmarks = null;
    let w = 640, h = 480;
    if (demo) {
      const t = now / 1000;
      handLandmarks = demoHand(0.5 + 0.2 * Math.cos(t), 0.5 + 0.15 * Math.sin(t), Math.floor(t) % 4 === 3);
    } else if (video.readyState >= 2 && video.currentTime !== lastVideoTime) {
      lastVideoTime = video.currentTime;
      w = video.videoWidth; h = video.videoHeight;
      lastTs = Math.max(lastTs + 1, now);
      const res = handModel.detectForVideo(video, lastTs);
      handLandmarks = res.landmarks && res.landmarks[0] || null;
      if (status.need_face) {                         // la cara solo hace falta con la mirada: ahorra batería y fotogramas
        await ensureFaceModel();
        const fr = faceModel.detectForVideo(video, lastTs + 0.5);
        faceLandmarks = fr.faceLandmarks && fr.faceLandmarks[0] || null;
      }
      draw(handLandmarks, faceLandmarks);
    } else { schedule(); return; }
    if (!sending) {
      sending = true;
      const body = buildPayload({ t: now, width: w, height: h, handLandmarks, faceLandmarks, faceKeys });
      api("/api/frame", body).then((r) => {
        if (r.ok) { status = r.data; render(); say(status.paused ? "En pausa" : "Enviando al ordenador", status.paused ? "warn" : "ok"); }
        else say(r.status === 409 ? "Otro móvil está conectado" : "Error del ordenador", "bad");
      }).catch(() => say("Sin conexión con el ordenador… reintentando", "bad")).finally(() => { sending = false; });
    }
    fps = 0.9 * fps + 0.1 * (1000 / Math.max(now - lastFrameAt, 1)); lastFrameAt = now;
    $("fps").textContent = `${fps.toFixed(0)} fps · ${handLandmarks ? "mano ✓" : "sin mano"}${status.need_face ? (faceLandmarks ? " · cara ✓" : " · sin cara") : ""}`;
  } catch (e) { say("Error: " + (e.message || e), "bad"); }
  schedule();
}

function render() {
  $("mode").textContent = status.input_mode === "gaze" ? "Puntero: mirada" : "Puntero: mano";
  $("pause").textContent = status.paused ? "Reanudar" : "Pausar";
  $("kb").classList.toggle("on", !!status.keyboard);
  $("action").textContent = status.last_action || "";
}

async function command(cmd) { await api("/api/command", { cmd }); }
$("start").addEventListener("click", start);
$("pause").addEventListener("click", () => command("toggle_pause"));
$("mode").addEventListener("click", () => command("mode:toggle"));
$("kb").addEventListener("click", () => command("keyboard"));
if (demo) $("demo").hidden = false;

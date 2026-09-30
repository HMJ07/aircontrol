# AirControl

Controla el ordenador con la mano, la mirada y la voz, sin tocar nada: la webcam hace de ratón y de teclado, el micrófono de mando a distancia. Todo se ejecuta **en local**: ni las imágenes ni el audio salen del equipo ni se guardan.

Pensado para **accesibilidad** (movilidad reducida, fatiga, manos ocupadas) y como alternativa abierta a las soluciones de pago y cerradas que existen hoy.

## Funciones

| | |
|---|---|
| **Ratón con la mano** | mover, clic, doble clic, clic derecho, arrastrar y scroll |
| **Ratón con la mirada** | cursor con el iris + la cabeza; clic por permanencia, parpadeo largo o pellizco |
| **Teclado aéreo** | teclado en pantalla con sugerencias de palabras; se maneja con la mano (pellizco) o la mirada (permanencia) |
| **Voz** | "abre Safari", "haz clic", "escribe hola…", "baja"; Whisper local + Ollama como respaldo |
| **Gestos propios** | los enseñas con unas muestras y los asocias a una acción |
| **Perfiles por aplicación** | el mismo gesto hace cosas distintas en el navegador, en una presentación o en un vídeo |
| **Calibración guiada** | esquinas para la mano, 9 puntos para la mirada |
| **Barra de menús + ajustes** | icono en la barra de menús (macOS) / bandeja (Windows) y una página de ajustes: sin editar JSON |
| **Afinado con tus datos** | HUD de diagnóstico, `record` y `replay` |

## Empezar

Requiere Python 3.10–3.12 (MediaPipe aún no soporta 3.13+).

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-voice.txt      # opcional: voz
python main.py doctor                      # cámara, micrófono, permisos, calibraciones
python main.py app                         # icono en la barra de menús + control + página de ajustes
```

Sin bandeja: `python main.py run` (añade `--dry-run` para probar sin mover el ratón, solo muestra lo que haría).

Desde el icono: **Ajustes…** abre la página (sliders para todos los umbrales, gestos, entrenamiento, calibraciones y un editor visual de perfiles). Los cambios se aplican al instante, sin reiniciar.

## Poses de la mano

| Pose | Acción |
|---|---|
| ☝️ índice extendido, anular y meñique plegados | mueve el cursor |
| 🤏 pulgar + índice | clic izquierdo · dos seguidos = doble clic · mantener o mover = arrastrar |
| 🤏 pulgar + corazón | clic derecho |
| ✌️ índice y corazón **claramente** extendidos, 0,25 s | scroll con el movimiento vertical de la mano |
| 🖐️ palma abierta / ✊ puño | reposo: el cursor no se toca |
| ✊ puño **sostenido 1,2 s** | **pausa / reanuda** todo el control (siempre disponible) |

La ventana de vista previa flota sobre las demás. Teclas en ella: `d` valores de diagnóstico · `k` teclado aéreo · `q`/Esc salir.

## Mirada

```bash
python main.py calibrate gaze      # 9 puntos en pantalla completa: mira fijamente cada uno
```

Al terminar muestra el **error medio en píxeles** (validado dejando fuera cada punto), y se guarda. Después, `mode:gaze` (menú, voz o gesto) cambia el puntero. Cómo hacer clic (`gaze_click` en ajustes): `dwell` (quedarse 1 s), `blink` (cerrar los ojos 0,6–2 s), `pinch` (la mano da el clic) u `off`.

Una webcam da precisión de unos centímetros en pantalla: los objetivos deben ser grandes, y el teclado aéreo usa teclas grandes por eso. Mejora con buena luz, cabeza de frente y recalibrando si cambias de postura o de pantalla.

## Teclado aéreo

`keyboard` (menú, voz, `k` o un gesto) abre un teclado acoplado abajo, sobre las demás ventanas. Con la **mano**: apunta a la tecla y pellizca. Con la **mirada**: mantén la vista en la tecla (`keyboard_dwell_s`); ⌫ y las flechas repiten si sigues mirándolas. Muestra 3 sugerencias de palabras; tocar una escribe lo que falta.

## Voz

Activa la voz en **Ajustes → Voz** (`voice_mode`):

- `push`: di la orden después de activar la escucha (menú "Escuchar una orden", o el gesto/acción `voice`).
- `wake`: siempre atento; solo actúa si empiezas por la palabra de activación ("**control**, abre Safari").

El reconocimiento es [faster-whisper](https://github.com/SYSTRAN/faster-whisper) en CPU (la primera vez descarga el modelo). Las órdenes comunes se resuelven **por reglas** al instante (español e inglés, tolerando pequeñas confusiones de oído); lo que las reglas no entienden se le pregunta a un modelo de **Ollama**, que solo *propone* una acción en texto: se valida con el mismo parser que los perfiles y se descarta si no es una acción conocida, así que no puede ejecutar nada raro. Sin Ollama, las reglas siguen funcionando.

```bash
python main.py voice-test          # prueba micrófono y voz sin ejecutar nada
```

## Gestos propios y perfiles

```bash
python main.py train ok            # 15 muestras de tu gesto (o desde Ajustes → Gestos)
python main.py gestures            # lista
```

Asócialos en **Ajustes → Perfiles**. Un perfil se activa si el nombre de la app en primer plano **contiene** alguna de sus palabras; lo que no define lo toma de "Cualquier app".

Acciones: `key:mod+c` (`mod` = ⌘ en macOS, Ctrl en Windows) · `click` · `right_click` · `double_click` · `scroll:down:5` · `media:play_pause|next|prev|volume_up|volume_down|mute` · `open:Safari` / `open:https://…` · `text:hola` · `pause` / `resume` / `toggle_pause` · `mode:hand|gaze|toggle` · `keyboard` · `voice`. Un valor puede ser `{ "darwin": …, "default": … }` para que el mismo perfil sirva en ambos sistemas.

## Calibrar y afinar

```bash
python main.py calibrate hand      # dedo a las cuatro esquinas: ajusta la región a tu alcance
python main.py record sesion.npz --seconds 30     # haz los gestos que te dan problemas
python main.py replay sesion.npz --set pinch_on=0.25 --set scroll_enter_s=0.4
```

`replay` pasa tu grabación por el motor con otros ajustes y cuenta los eventos (clics, arrastres, scroll…), para encontrar el umbral sin volver a hacer los gestos. Con la vista previa en marcha, `d` muestra en vivo las distancias de pellizco y la extensión de cada dedo junto a sus umbrales.

## Si algo no va

Dos pruebas **reales** (no simuladas), para ejecutar en tu Terminal con los permisos concedidos:

```bash
python main.py check-input      # mueve el cursor, clics, doble clic, arrastrar, teclas, volumen y abre Calculator
python main.py check-hand       # te guía gesto a gesto con tu mano y, si uno falla, te dice por qué y qué ajuste cambiar
```

`check-input` verifica el sistema operativo (permisos, pynput): usa una ventana propia que registra lo que recibe y lee el volumen real del sistema. `check-hand` va en simulación y mide lo que ve el reconocedor con tu mano (distancia de pellizco, extensión de cada dedo).

## Permisos

**macOS**: **Accesibilidad** (ratón y teclado), **Cámara** y, si usas voz, **Micrófono**. Sin Accesibilidad macOS descarta los eventos **sin avisar**; AirControl lo pide al arrancar y se cierra con instrucciones. Si lo ejecutas con Python, el permiso es de la Terminal/editor. Con la app firmada ad-hoc cada compilación cambia la firma y macOS vuelve a pedirlo.

**Windows**: no hay permisos que conceder, pero un programa normal no puede controlar ventanas ejecutadas como administrador (UIPI).

## Arquitectura

```
app (bandeja + web de ajustes)  ──órdenes/estado por ficheros──►  motor (proceso aparte)
                                                                   cámara → manos / cara → Controller
  Controller ─► AirMouse (mano) · GazePointer (mirada) · KeyboardState · gestos · Profiles ─► Action
  voz (hilo): micrófono → VAD → Whisper → reglas | Ollama → Action
  InputBackend: PynputBackend (macOS/Windows) · DryRunBackend
```

La lógica (puntero, mirada, teclado, gestos, perfiles, voz, calibraciones, motor) no depende de cámara ni del SO y se prueba con manos y caras sintéticas, audio generado y una cámara y ventana simuladas:

```bash
python -m unittest discover -s tests -v
```

## Instaladores

```bash
bash packaging/build_mac.sh            # dist/AirControl-macOS.dmg (~285 MB con voz)
./packaging/build_windows.ps1          # dist\AirControl-Setup.exe  (necesita Inno Setup)
AIRCONTROL_NO_VOICE=1 bash packaging/build_mac.sh    # sin voz: ~150 MB menos
```

El CI (`.github/workflows/build.yml`) ejecuta las pruebas (en Windows, además, las que usan la API real: mover el cursor, teclas, DPI), construye ambos instaladores y lanza `--selftest` sobre la app empaquetada; al subir una etiqueta `v*` los publica en Releases.

## Limitaciones conocidas

- **Mirada**: precisión de centímetros; el clic por permanencia no tiene anillo de progreso junto al cursor (hay un pitido de confirmación y el anillo aparece en la vista previa).
- **Varios monitores**: se controla la pantalla principal.
- **Voz**: en `wake` se transcribe toda frase detectada (CPU); `tiny` es más ligero que `base`.
- La calibración y los umbrales por defecto están razonados y probados con datos sintéticos; afínalos con tu mano y tu cara (ver *Calibrar y afinar*).

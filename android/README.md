# AirControl para Android

Controla **este mismo teléfono** con la mano que ve la cámara frontal: cursor, toques, doble toque, arrastre, desplazamiento,
Atrás / Inicio / Recientes / Notificaciones. Todo se procesa en el teléfono; no se envía ni se guarda nada.

> Estado: **primera versión, sin probar en un teléfono real** (se compila en la CI y la lógica de Python está probada con un móvil
> simulado, pero el servicio de Accesibilidad y la cámara solo se pueden comprobar en un dispositivo). Mirada, teclado y voz: pendientes.

## Instalar
1. Descarga `AirControl-Android.apk` de [Releases](https://github.com/HMJ07/aircontrol/releases) (o del artefacto de la CI) y ábrelo en el teléfono (permite instalar apps de orígenes desconocidos si lo pide).
2. Abre **AirControl** → **1. Conceder la cámara**.
3. **2. Activar AirControl en Accesibilidad** → AirControl → activar.
   - En **Android 13 o superior** el sistema bloquea esto en apps instaladas fuera de Google Play: pulsa «Ajustes restringidos»,
     abre ⋮ (arriba a la derecha) → **Permitir ajustes restringidos** y vuelve a activarlo.
4. **Iniciar control**. Aparece una notificación con «Detener».

## Gestos
| Gesto | Acción |
|---|---|
| ☝️ índice extendido | mueve el cursor |
| 🤏 pulgar + índice | toque · dos seguidos = doble toque · mantener y mover = arrastrar |
| 🤏 pulgar + corazón | pulsación larga (menús contextuales) |
| ✌️ y mover la mano arriba/abajo | desplazar |
| 🖐️ palma abierta deslizándose | izquierda = Atrás · derecha = Recientes · arriba = Inicio · abajo = Notificaciones |
| 👍 | reproducir / pausar |
| ✊ sostenido 1,2 s | pausa / reanuda |

## Cómo está hecho
- **Kotlin**: pantalla de permisos, servicio en primer plano con CameraX (cámara frontal) y MediaPipe Hand Landmarker, y un
  `AccessibilityService` que ejecuta gestos (`dispatchGesture`), acciones globales y dibuja el cursor.
- **Python (Chaquopy)**: el mismo motor del ordenador (`aircontrol`: puntero, pellizco, gestos). Solo se empaqueta lo que lista
  `aircontrol-python-files.txt`; hay una prueba que lo ejecuta aislado, sin OpenCV ni librerías de escritorio.
- `android_main.py` recibe los 21 puntos de cada fotograma; `android_backend.py` traduce los eventos a llamadas de `PhoneBridge.kt`.

## Compilar
Con JDK 17, Python 3.11 y el SDK de Android (la CI lo hace: `.github/workflows/android.yml`):
```bash
gradle -p android assembleDebug      # APK en android/app/build/outputs/apk/debug/
```

## Limitaciones conocidas
- Mirada, teclado aéreo y voz aún no están en la app Android.
- Un gesto nuevo de Accesibilidad cancela el anterior: el desplazamiento se agrupa en deslizamientos cada ~0,2 s.
- En iOS no es posible controlar el teléfono entero (reglas de Apple): ver [docs/MOBILE.md](../docs/MOBILE.md).

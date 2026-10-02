# AirControl en móviles

Qué se puede hacer en Android y en iOS, qué no, y en qué estado está cada cosa.

## Resumen

| | Android | iPhone / iPad |
|---|---|---|
| **Móvil como cámara y mando del ordenador** | ✅ página web (sin instalar) | ✅ página web en Safari (sin instalar) |
| **Controlar el propio móvil** (mano, mirada, voz) | 🛠️ app nativa en preparación (Servicio de Accesibilidad) | ❌ no es posible: iOS no deja a una app tocar ni manejar otras apps |

### Por qué iOS no puede controlar el iPhone entero
Apple no permite que una app genere toques ni controle otras aplicaciones. Para eso iOS trae sus propias funciones (Seguimiento ocular, Control por cabeza, Control por voz, AssistiveTouch en *Ajustes → Accesibilidad*). Una app nuestra solo podría funcionar dentro de sí misma. Además, distribuir una app de iOS exige una cuenta de Apple Developer de pago (99 USD/año) para TestFlight o App Store; sin ella solo se puede instalar desde Xcode durante 7 días. Por eso en iOS la vía es la **página web**, que no necesita ni cuenta ni instalación.

## 1. Móvil como cámara (hecho)

El móvil abre una página servida por el ordenador (`python main.py run --remote`, o el botón en Ajustes):

```
móvil: cámara → MediaPipe (en el navegador, WebAssembly) → 15 puntos de cara + 21 de mano
   └─ HTTPS + token ──►  ordenador: RemoteHub → mismo motor que con la webcam → ratón/teclado
                     ◄── estado (pausa, modo, ¿enviar cara?) y órdenes (pausa, teclado, mano/mirada)
```

- No sale vídeo del móvil. Solo puntos.
- Reutiliza todo el motor: puntero, gestos, mirada, calibraciones, teclado aéreo, perfiles.
- HTTPS con certificado autofirmado generado en el ordenador (se reutiliza mientras valga para la IP). El navegador avisa una vez; hay que aceptarlo.
- Token aleatorio en el fragmento de la URL (`#t=…`, no se envía al servidor ni queda en registros), cabecera `X-Token`, comparación en tiempo constante, un solo móvil a la vez, cuerpo máximo de 64 KB, órdenes en lista blanca.

**Probado:** pruebas automáticas (formato de datos, servidor, TLS real, un "móvil" simulado contra el motor, y el JavaScript contra el Python) y la página en modo demostración (`?demo=1`, sin cámara) en un navegador real, con envío a ~28 fps, movimiento, arrastre y botón de pausa de ida y vuelta.
**Sin probar:** la cámara y MediaPipe en un móvil real, la rapidez de detección en un móvil de gama baja y el aviso de certificado en Safari de iOS (algunos navegadores bloquean la cámara o `fetch` con certificados autofirmados; si pasa, la alternativa es instalar el certificado en el móvil).

## 2. App Android nativa (plan)

Controlar el propio móvil con la cámara frontal.

- **Entrada:** CameraX (cámara frontal) + MediaPipe Tasks para Android (manos y cara con iris), como aquí.
- **Lógica:** el mismo código Python del motor (puntero, gestos, mirada, calibración) ejecutado dentro de la app con Chaquopy; Kotlin solo le pasa los landmarks.
- **Salida:** un *Servicio de Accesibilidad* que hace toques, deslizamientos, desplazamiento, Atrás, Inicio y Recientes, y dibuja un cursor encima de todo.
- **Pantallas:** permisos (Cámara, Accesibilidad, mostrar sobre otras apps), calibración de mano y de mirada, ajustes.
- **Distribución:** `.apk` firmado con una clave de depuración, generado por la CI; sin Google Play de momento.
- **Hitos:** 1) cámara + manos + cursor + toque; 2) scroll, gestos y pausa; 3) mirada y calibración; 4) teclado/voz; 5) ajustes.
- **Necesita probarse en un teléfono real** en cada hito: sin dispositivo no se puede comprobar el servicio de Accesibilidad.

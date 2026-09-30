## Instalar

| Sistema | Descarga | Cómo |
|---|---|---|
| **macOS** (Apple Silicon, macOS 12+) | `AirControl-macOS.dmg` | Abre el `.dmg` y **arrastra AirControl a Aplicaciones** |
| **Windows** 10/11 | `AirControl-Setup.exe` (o el `.zip` portátil) | Doble clic → Siguiente → Instalar |

### Primer arranque
- **macOS**: la app no está firmada con un Developer ID de Apple (requiere cuenta de pago), así que macOS avisará la primera vez. Es normal. Ejecuta **una vez** en la Terminal y ábrela con normalidad:
  `xattr -dr com.apple.quarantine /Applications/AirControl.app`
  (o: intenta abrirla → *Ajustes del Sistema → Privacidad y seguridad → Abrir igualmente*).
- Concede **Cámara**, **Accesibilidad** (para mover el ratón y pulsar teclas) y, si usas voz, **Micrófono**. En macOS, cada versión nueva pide de nuevo Accesibilidad.
- **Windows**: si SmartScreen avisa, *Más información → Ejecutar de todas formas*.

Aparece un icono en la barra de menús (macOS) / bandeja (Windows): desde ahí, **Ajustes…** para calibrar y configurar todo.
La voz descarga su modelo la primera vez que se activa (~140 MB) y necesita internet una sola vez.

---

#!/usr/bin/env bash
# Genera dist/AirControl-macOS.dmg. Ejecutar desde cualquier sitio: bash packaging/build_mac.sh
#
# Se compila y firma en una carpeta temporal FUERA del proyecto: si el proyecto vive en una carpeta
# sincronizada con iCloud (Documentos/Escritorio), macOS añade atributos protegidos a los archivos y
# codesign falla con "resource fork, Finder information, or similar detritus not allowed".
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

PY="${PYTHON:-python3}"
WORK="${AIRCONTROL_BUILD_DIR:-${TMPDIR:-/tmp}/aircontrol-build}"
APP="$WORK/dist/AirControl.app"

mkdir -p models
[ -f models/hand_landmarker.task ] || curl -fL -o models/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
[ -f models/face_landmarker.task ] || curl -fL -o models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

rm -rf "$WORK"; mkdir -p "$WORK"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$WORK/dist" --workpath "$WORK/work" packaging/aircontrol.spec

xattr -cr "$APP" 2>/dev/null || true
# Firma ad-hoc: obligatoria para que arranque en Apple Silicon. OJO: cada compilación genera una firma
# distinta y macOS vuelve a pedir el permiso de Accesibilidad (con un Developer ID el permiso se conserva).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

mkdir -p "$ROOT/dist"
rm -f "$ROOT/dist/AirControl-macOS.dmg"
hdiutil create -volname "AirControl" -srcfolder "$APP" -ov -format UDZO "$WORK/AirControl-macOS.dmg"
cp "$WORK/AirControl-macOS.dmg" "$ROOT/dist/"
echo "✅ dist/AirControl-macOS.dmg  (app sin empaquetar en: $APP)"

# PyInstaller: `pyinstaller packaging/aircontrol.spec` (usar los scripts build_mac.sh / build_windows.ps1)
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
APP_NAME = "AirControl"

import importlib.util
import os

# AIRCONTROL_NO_VOICE=1 compila sin el motor de voz (faster-whisper + ctranslate2 + onnxruntime: ~150 MB menos).
WITH_VOICE = not os.environ.get("AIRCONTROL_NO_VOICE") and importlib.util.find_spec("faster_whisper") is not None

datas = [(str(ROOT / "models" / "hand_landmarker.task"), "models"),
         (str(ROOT / "models" / "face_landmarker.task"), "models"),
         (str(ROOT / "aircontrol" / "web"), "aircontrol/web")]
binaries, hiddenimports = [], []
packages = ["mediapipe"] + (["faster_whisper", "ctranslate2", "onnxruntime", "sounddevice", "_sounddevice_data",
                            "tokenizers", "huggingface_hub", "ollama"] if WITH_VOICE else [])
for pkg in packages:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# pynput elige su backend (darwin/win32) al importar: PyInstaller no lo ve por análisis estático.
hiddenimports += collect_submodules("pynput") + collect_submodules("pystray") + ["PIL._tkinter_finder"]
if sys.platform == "darwin":
    hiddenimports += ["Quartz", "ApplicationServices", "AppKit", "Foundation"]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # PyAV (lo trae faster-whisper solo para decodificar ficheros) duplica libavdevice con OpenCV: fuera.
    excludes=["tkinter", "IPython", "pytest", "av"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,          # sin ventana negra; el registro va a aircontrol.log
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        bundle_identifier="com.aircontrol.app",
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # Sin esta clave macOS deniega la cámara sin ni siquiera preguntar.
            "NSCameraUsageDescription": "AirControl usa la cámara para seguir tu mano y tu mirada y controlar el "
                                        "ordenador. Las imágenes se procesan en tu equipo y nunca se envían ni se guardan.",
            # Sin esta clave macOS deniega el micrófono sin preguntar (órdenes de voz, solo si las activas).
            "NSMicrophoneUsageDescription": "AirControl usa el micrófono para entender tus órdenes de voz. El audio "
                                            "se procesa en tu equipo y nunca se envía ni se guarda.",
        },
    )

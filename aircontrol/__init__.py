import os
import sys
import warnings

__version__ = "0.1.2"

# MediaPipe llama a una función obsoleta de protobuf en cada fotograma y la consola se llenaba de avisos idénticos.
warnings.filterwarnings("ignore", message=r"SymbolDatabase\.GetPrototype", category=UserWarning)

# Media Foundation (Windows) con transformaciones por hardware tarda 3 s en abrir y 12 s más en fijar la resolución en
# algunas webcams. OpenCV lee esta variable al importarse, así que tiene que fijarse aquí, antes de cualquier `import cv2`.
if sys.platform == "win32":
    os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

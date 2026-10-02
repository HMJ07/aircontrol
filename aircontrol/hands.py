import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from . import geometry
from .config import MODEL_PATH, MODEL_URL


class HandTracker:
    """MediaPipe Hand Landmarker (una mano, en CPU). Devuelve un array (21, 3) normalizado o None."""

    def __init__(self, detection_confidence=0.55):
        if not MODEL_PATH.exists():
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        options = vision.HandLandmarkerOptions(
            # CPU explícito: MediaPipe intenta iniciar Metal/GPU por defecto y en algunos Mac aborta el proceso.
            base_options=python.BaseOptions(model_asset_path=str(MODEL_PATH),
                                            delegate=python.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=detection_confidence,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.detector = vision.HandLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, frame_bgr, timestamp_ms):
        ts = max(int(timestamp_ms), self._last_ts + 1)             # MediaPipe exige marcas crecientes
        self._last_ts = ts
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self.detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        geometry.set_aspect(frame_bgr.shape[1], frame_bgr.shape[0])          # umbrales iguales en 4:3 y 16:9
        if not result.hand_landmarks:
            return None
        return np.array([(p.x, p.y, p.z) for p in result.hand_landmarks[0]], dtype=np.float32)

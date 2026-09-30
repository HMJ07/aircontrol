import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from .config import FACE_MODEL_PATH, FACE_MODEL_URL


class FaceTracker:
    """MediaPipe Face Landmarker con iris (478 puntos). Devuelve un array (478, 3) normalizado o None."""

    def __init__(self):
        if not FACE_MODEL_PATH.exists():
            FACE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL_PATH)
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(FACE_MODEL_PATH),
                                            delegate=python.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            min_face_detection_confidence=0.5, min_face_presence_confidence=0.5, min_tracking_confidence=0.5,
        )
        self.detector = vision.FaceLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, frame_bgr, timestamp_ms):
        ts = max(int(timestamp_ms), self._last_ts + 1)
        self._last_ts = ts
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self.detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        if not result.face_landmarks:
            return None
        return np.array([(p.x, p.y, p.z) for p in result.face_landmarks[0]], dtype=np.float32)

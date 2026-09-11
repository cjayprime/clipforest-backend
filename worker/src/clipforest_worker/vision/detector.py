"""CPU face detection (PRD §9.4): OpenCV YuNet when the model is available,
otherwise the bundled Haar cascade. Boxes are returned in normalized coordinates."""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from ..log import get_logger

log = get_logger(__name__)


@dataclass
class Box:
    """Normalized (0..1) box relative to the analyzed frame."""

    x: float
    y: float
    w: float
    h: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


def box_iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


class FaceDetector:
    def __init__(self, model_path: str, score_threshold: float = 0.6):
        import cv2

        self.cv2 = cv2
        self.kind = "none"
        self._yunet = None
        self._haar = None
        if model_path and os.path.exists(model_path) and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(model_path, "", (320, 320), score_threshold, 0.3, 50)
                self.kind = "yunet"
            except Exception as exc:  # noqa: BLE001
                log.warning("YuNet unavailable, falling back to Haar", extra={"error": str(exc)})
        if self._yunet is None:
            cascade = os.path.join(getattr(cv2, "data", None).haarcascades, "haarcascade_frontalface_default.xml") if hasattr(cv2, "data") else ""
            if cascade and os.path.exists(cascade):
                self._haar = cv2.CascadeClassifier(cascade)
                self.kind = "haar"
        if self.kind == "none":
            log.warning("No face detector available; renders will use the fallback framing")

    def detect(self, frame: np.ndarray) -> list[Box]:
        h, w = frame.shape[:2]
        if self._yunet is not None:
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(frame)
            if faces is None:
                return []
            return [Box(float(f[0]) / w, float(f[1]) / h, float(f[2]) / w, float(f[3]) / h, float(f[14])) for f in faces if f[2] > 0 and f[3] > 0]
        if self._haar is not None:
            gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
            rects = self._haar.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(max(24, w // 30), max(24, w // 30)))
            return [Box(x / w, y / h, bw / w, bh / h, 0.8) for (x, y, bw, bh) in rects]
        return []

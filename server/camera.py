"""Local webcam capture — one frame per call, no continuous stream, no recording."""

from __future__ import annotations

import base64

import cv2

# ponytail: hardcoded default camera + warmup count. Fine for one webcam;
# add a config knob (server.yaml: camera.index) if a second camera is ever needed.
DEFAULT_CAMERA_INDEX = 0
WARMUP_FRAMES = 3  # first frame(s) after opening are often stale/dark on some webcams


def capture_jpeg_base64(camera_index: int = DEFAULT_CAMERA_INDEX) -> str:
    """Grab one frame from the local webcam, return base64-encoded JPEG.
    Raises RuntimeError with a Spanish, speakable message on any failure."""
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    try:
        if not cap.isOpened():
            raise RuntimeError("No pude abrir la cámara. ¿Está conectada o la está usando otra app?")
        for _ in range(WARMUP_FRAMES):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("La cámara no devolvió ninguna imagen.")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("No pude codificar la imagen de la cámara.")
        return base64.b64encode(buf.tobytes()).decode("ascii")
    finally:
        cap.release()

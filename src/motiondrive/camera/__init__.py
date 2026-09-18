"""Webcam enumeration + threaded capture. Keeps OpenCV isolated from the UI
thread so the GUI never blocks on a frame read."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from motiondrive.logging_ import get_logger

log = get_logger(__name__)

RESOLUTIONS = [(640, 480), (1280, 720), (1920, 1080)]
FPS_OPTIONS = [24, 30, 60]


@dataclass
class CameraDevice:
    index: int
    name: str


def list_cameras(max_probe: int = 5) -> list[CameraDevice]:
    """Probe camera indices 0..max_probe-1. Cheap best-effort enumeration --
    OpenCV/DirectShow don't expose friendly names portably, so we label them
    generically; this is still enough for a dropdown."""
    import cv2

    devices: list[CameraDevice] = []
    backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
    for idx in range(max_probe):
        cap = cv2.VideoCapture(idx, backend)
        try:
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    label = "Integrated Webcam" if idx == 0 else f"Camera {idx}"
                    devices.append(CameraDevice(index=idx, name=label))
        finally:
            cap.release()
    return devices


class CameraUnavailableError(RuntimeError):
    pass


class CameraStream:
    """Runs cv2.VideoCapture.read() on a background thread and hands the
    latest frame to a callback. Frames are BGR numpy arrays as OpenCV
    produces them; callers convert/mirror as needed."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self._cap = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._on_frame: Optional[Callable] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._lock = threading.Lock()
        self.actual_fps = 0.0
        self._frame_times: list[float] = []

    def start(self, on_frame: Callable, on_error: Optional[Callable[[str], None]] = None) -> None:
        import cv2

        self._on_frame = on_frame
        self._on_error = on_error
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
            raise CameraUnavailableError(f"Could not open camera index {self.index}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CameraStream")
        self._thread.start()
        log.info("Camera %s started at %sx%s@%s", self.index, self.width, self.height, self.fps)

    def _loop(self) -> None:
        consecutive_failures = 0
        while self._running:
            with self._lock:
                cap = self._cap
            if cap is None:
                break
            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    log.error("Camera read failing repeatedly; reporting error")
                    if self._on_error:
                        self._on_error("Camera disconnected or unavailable.")
                    break
                time.sleep(0.02)
                continue
            consecutive_failures = 0
            now = time.time()
            self._frame_times.append(now)
            self._frame_times = [t for t in self._frame_times if now - t < 1.0]
            self.actual_fps = float(len(self._frame_times))
            if self._on_frame:
                self._on_frame(frame)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
        log.info("Camera %s stopped", self.index)

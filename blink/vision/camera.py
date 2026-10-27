"""
CameraManager for Linux using V4L2 and OpenCV.
Captures frames asynchronously in a dedicated worker thread, preventing I/O blocking.
"""

from __future__ import annotations

import glob
import threading
import time
from typing import List, Optional, Tuple
import cv2
import numpy as np


class CameraManager:
    def __init__(self, device_index: int = 0):
        self.device_index = device_index
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

        self._current_frame: Optional[np.ndarray] = None
        self._frame_id: int = 0
        self.error_message: Optional[str] = None

    @staticmethod
    def list_devices() -> List[int]:
        """Enumerates available /dev/video* devices."""
        devices = []
        for dev in sorted(glob.glob("/dev/video*")):
            try:
                idx = int(dev.replace("/dev/video", ""))
                # Quick probe without hanging
                cap = cv2.VideoCapture(idx)
                if cap.isOpened():
                    devices.append(idx)
                    cap.release()
            except Exception:
                continue
        return devices if devices else [0]

    def start(self, device_index: Optional[int] = None) -> bool:
        if device_index is not None:
            self.device_index = device_index

        if self._running:
            return True

        self.error_message = None
        self._cap = cv2.VideoCapture(self.device_index)
        if not self._cap.isOpened():
            self.error_message = f"Could not open camera /dev/video{self.device_index}."
            print(f"[CameraManager] {self.error_message}")
            return False

        # Request 640x480 at 30fps
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name="BlinkCameraWorker")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        with self._lock:
            self._current_frame = None

    def get_latest_frame(self) -> Optional[Tuple[int, np.ndarray]]:
        with self._lock:
            if self._current_frame is None:
                return None
            return (self._frame_id, self._current_frame.copy())

    def _capture_loop(self) -> None:
        while self._running and self._cap is not None:
            ret, frame = self._cap.read()
            if ret and frame is not None:
                with self._lock:
                    self._frame_id += 1
                    self._current_frame = frame
            else:
                time.sleep(0.01)
            time.sleep(0.01)  # Throttle to ~30-40 fps

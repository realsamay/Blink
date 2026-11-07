"""
CameraManager for Linux using V4L2 and OpenCV.
Captures frames asynchronously in a dedicated worker thread, preventing I/O blocking.
"""

from __future__ import annotations

import glob
import sys
import threading
import time
from typing import List, Optional, Tuple
import cv2
import numpy as np

from blink.system.platform_info import IS_WINDOWS


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

    @property
    def is_running(self) -> bool:
        return self._running

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        """Blocks until the camera is running and captures its first valid frame."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._running and self._current_frame is not None and self._frame_id > 0:
                    return True
            time.sleep(0.02)
        return False

    @staticmethod
    def list_devices() -> List[int]:
        """Enumerates available video capture devices on Linux and Windows without disrupting active streams."""
        devices = []
        if IS_WINDOWS:
            # Probe indices 0..3 using DirectShow for fast check
            for idx in range(4):
                try:
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        devices.append(idx)
                        cap.release()
                except Exception:
                    continue
            return devices if devices else [0]

        # Linux: non-disruptive check using V4L2 capability query without allocation
        try:
            import fcntl
            import struct
            for dev in sorted(glob.glob("/dev/video*")):
                try:
                    idx = int(dev.replace("/dev/video", ""))
                    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
                    buf = bytearray(104)
                    fcntl.ioctl(fd, 0x80685600, buf)
                    os.close(fd)
                    _, dev_caps = struct.unpack_from("II", buf, 80)
                    if dev_caps & 0x00000001:  # V4L2_CAP_VIDEO_CAPTURE
                        devices.append(idx)
                except Exception:
                    continue
        except Exception:
            pass

        return devices if devices else [0]

    def reset(self) -> bool:
        """Safely resets and reopens the camera device to clear any driver stalls."""
        with self._lock:
            self._current_frame = None
        return self._reopen_capture()

    def _reopen_capture(self) -> bool:
        try:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            time.sleep(0.08)
            available = self.list_devices()
            if available and self.device_index not in available:
                self.device_index = available[0]
            backend = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_ANY
            self._cap = cv2.VideoCapture(self.device_index, backend)
            if self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self._cap.set(cv2.CAP_PROP_FPS, 30)
                return True
            elif self.device_index != 0:
                self.device_index = 0
                self._cap = cv2.VideoCapture(0, backend)
                if self._cap.isOpened():
                    self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    self._cap.set(cv2.CAP_PROP_FPS, 30)
                    return True
        except Exception as e:
            print(f"[CameraManager] Error resetting camera: {e}")
        return False

    def start(self, device_index: Optional[int] = None) -> bool:
        if device_index is not None:
            self.device_index = device_index

        if self._running:
            return True

        self.error_message = None
        available = self.list_devices()
        if available and self.device_index not in available:
            self.device_index = available[0]

        backend = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_ANY
        self._cap = cv2.VideoCapture(self.device_index, backend)
        if not self._cap.isOpened():
            if self.device_index != 0:
                print(f"[CameraManager] Camera index {self.device_index} failed to open. Falling back to device 0.")
                self.device_index = 0
                self._cap = cv2.VideoCapture(0, backend)
            if not self._cap.isOpened():
                dev_str = f"index {self.device_index}" if IS_WINDOWS else f"/dev/video{self.device_index}"
                self.error_message = f"Could not open camera {dev_str}."
                print(f"[CameraManager] {self.error_message}")
                return False

        # Request 640x480 at 30fps
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)

        self._running = True
        with self._lock:
            self._current_frame = None
            self._frame_id = 0
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
        consecutive_errors = 0
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                time.sleep(0.04)
                continue
            try:
                ret, frame = self._cap.read()
            except Exception:
                ret, frame = False, None

            if ret and frame is not None:
                consecutive_errors = 0
                with self._lock:
                    self._frame_id += 1
                    self._current_frame = frame
            else:
                consecutive_errors += 1
                if consecutive_errors >= 12:
                    # Auto-recover camera device if driver drops frame buffer
                    self._reopen_capture()
                    consecutive_errors = 0
                time.sleep(0.02)
            time.sleep(0.01)  # Throttle to ~30-40 fps

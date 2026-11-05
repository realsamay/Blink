"""
Blink Lockscreen Overlay Card for Windows 10/11.
Native Win32 squircle card (100x100, 22px rounded corners) floating under the camera notch.
Hardware rounded region clipping, zero focus stealing (WS_EX_NOACTIVATE), 60 FPS animation playback.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from enum import Enum
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable, Optional, List, Tuple

import cv2
import numpy as np


RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"

# Win32 Constants
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000

SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = ctypes.c_void_p(-1)

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_LBUTTONDOWN = 0x0201
WM_USER = 0x0400
WM_UPDATE_FRAME = WM_USER + 1
WM_SET_PHASE = WM_USER + 2

SM_CXSCREEN = 0
SM_CYSCREEN = 1
BI_RGB = 0
DIB_RGB_COLORS = 0


class OverlayPhase(Enum):
    CLOSED = "closed"
    SCANNING = "scanning"
    SUCCESS = "success"
    FAILURE = "failure"


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class AnimationAssets:
    """Pre-decodes and caches 60 FPS BGRA frames for smooth Win32 blitting."""
    _loaded = False
    _loading_lock = threading.Lock()

    idle_buffers: List[np.ndarray] = []
    unlock_buffers: List[np.ndarray] = []
    failure_buffers: List[np.ndarray] = []

    # Compatibility aliases for cross-platform interface
    idle_surfaces: List[Any] = []
    unlock_surfaces: List[Any] = []
    failure_surfaces: List[Any] = []

    @classmethod
    def load_all(cls, target_size: int = 136) -> None:
        with cls._loading_lock:
            if cls._loaded:
                return

            def _load_video(filename: str) -> List[np.ndarray]:
                path = RESOURCES_DIR / filename
                bufs = []
                if not path.is_file():
                    return bufs

                cap = cv2.VideoCapture(str(path))
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    resized = cv2.resize(frame, (target_size, target_size), interpolation=cv2.INTER_AREA)
                    bgra = cv2.cvtColor(resized, cv2.COLOR_BGR2BGRA)
                    bufs.append(bgra)
                cap.release()
                return bufs

            cls.idle_buffers = _load_video("idleanimation.mp4")
            cls.unlock_buffers = _load_video("unlockanimation.mp4")
            cls.failure_buffers = _load_video("unsuccessfulunlockanimation.mp4")

            cls.idle_surfaces = cls.idle_buffers
            cls.unlock_surfaces = cls.unlock_buffers
            cls.failure_surfaces = cls.failure_buffers

            cls._loaded = True
            print(f"[AnimationAssets-Win] Cached {len(cls.idle_buffers)} idle, {len(cls.unlock_buffers)} unlock, {len(cls.failure_buffers)} failure frames (size: {target_size}x{target_size}).")


class WindowsNotchOverlayWindow:
    """
    Native Win32 floating squircle overlay positioned under the camera notch.
    100x100 size, 22px hardware rounded corners via SetWindowRgn, 60 FPS animation.
    """

    def __init__(self, on_retry_clicked: Optional[Callable[[], None]] = None):
        self.on_retry_clicked = on_retry_clicked
        self._is_dead = False
        self._is_desktop_preview = False

        self.card_size = 148.0
        self.corner_radius = 28.0
        self.video_size = 104.0

        AnimationAssets.load_all(target_size=int(self.video_size))

        self.phase = OverlayPhase.CLOSED
        self.idle_frame_idx = 0
        self.unlock_frame_idx = 0
        self.failure_frame_idx = 0

        self.anim_tick = 0.0
        self.shake_decay = 0.0

        # Vertical pop-out physics from webcam bezel
        self.target_y = 14.0
        self.current_y = self.target_y - 35.0

        self._hwnd: Optional[int] = None
        self._ui_thread: Optional[threading.Thread] = None
        self._anim_running = False
        self._ready_event = threading.Event()

        # Start Win32 window message pump in dedicated thread
        self._ui_thread = threading.Thread(target=self._run_window, daemon=True, name="BlinkWinOverlayUI")
        self._ui_thread.start()
        self._ready_event.wait(timeout=2.0)

    def _is_allowed_to_show(self) -> bool:
        if self._is_desktop_preview:
            return True
        try:
            from blink.system.lock_monitor import LockMonitor
            return LockMonitor.is_screen_actually_locked()
        except Exception:
            return False

    def _run_window(self) -> None:
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
            self._ready_event.set()
            return

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            gdi32 = ctypes.windll.gdi32

            WNDPROC = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            def wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_PAINT:
                    ps = (ctypes.c_byte * 72)()  # PAINTSTRUCT
                    hdc = user32.BeginPaint(hwnd, ps)
                    self._render_current_frame(hdc)
                    user32.EndPaint(hwnd, ps)
                    return 0

                elif msg == WM_LBUTTONDOWN:
                    if self.on_retry_clicked:
                        self.on_retry_clicked()
                    return 0

                elif msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0

                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wnd_proc = WNDPROC(wnd_proc)

            class WNDCLASSEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HICON),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON),
                ]

            hinst = kernel32.GetModuleHandleW(None)
            cls_name = "BlinkNotchOverlayWindowClass"

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.style = 0x0008  # CS_DBLCLKS
            wc.lpfnWndProc = self._wnd_proc
            wc.hInstance = hinst
            wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
            wc.hbrBackground = gdi32.GetStockObject(4)  # BLACK_BRUSH
            wc.lpszClassName = cls_name

            user32.RegisterClassExW(ctypes.byref(wc))

            cx_screen = user32.GetSystemMetrics(SM_CXSCREEN)
            x_pos = int((cx_screen - int(self.card_size)) / 2)
            y_pos = int(self.current_y)

            # WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE prevents stealing focus
            ex_style = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            style = WS_POPUP

            self._hwnd = user32.CreateWindowExW(
                ex_style,
                cls_name,
                "Blink Dynamic Card",
                style,
                x_pos,
                y_pos,
                int(self.card_size),
                int(self.card_size),
                None,
                None,
                hinst,
                None,
            )

            if self._hwnd:
                # Apply hardware 22px rounded corner region mask
                self._apply_round_region()

            self._ready_event.set()

            # Win32 Message Pump
            msg = wintypes.MSG()
            while not self._is_dead and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        except Exception as e:
            print(f"[WindowsNotchOverlay] Window creation error: {e}")
            self._ready_event.set()

    def _apply_round_region(self) -> None:
        """Applies Win32 CreateRoundRectRgn to carve 22px rounded corners into window hardware."""
        if not self._hwnd or not hasattr(ctypes, "windll"):
            return
        try:
            gdi32 = ctypes.windll.gdi32
            user32 = ctypes.windll.user32
            w = int(self.card_size)
            h = int(self.card_size)
            # Corner radius is 22px; diameter is 44px
            diam = int(self.corner_radius * 2)
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, diam, diam)
            user32.SetWindowRgn(self._hwnd, rgn, True)
        except Exception as e:
            print(f"[WindowsNotchOverlay] Region clipping error: {e}")

    def _render_current_frame(self, hdc: int) -> None:
        """Composites obsidian black glass background, active animation frame, and colored border."""
        if not hasattr(ctypes, "windll"):
            return

        w = int(self.card_size)
        h = int(self.card_size)
        v_size = int(getattr(self, "video_size", 104))
        vx = (w - v_size) // 2
        vy = 8

        # Base obsidian canvas: RGBA (8, 5, 5, 255)
        canvas = np.full((h, w, 4), (8, 5, 5, 255), dtype=np.uint8)

        # Blit active animation surface
        frame: Optional[np.ndarray] = None
        if self.phase == OverlayPhase.SUCCESS and AnimationAssets.unlock_buffers:
            idx = min(self.unlock_frame_idx, len(AnimationAssets.unlock_buffers) - 1)
            frame = AnimationAssets.unlock_buffers[idx]
        elif self.phase == OverlayPhase.FAILURE and AnimationAssets.failure_buffers:
            idx = min(self.failure_frame_idx, len(AnimationAssets.failure_buffers) - 1)
            frame = AnimationAssets.failure_buffers[idx]
        elif AnimationAssets.idle_buffers:
            idx = self.idle_frame_idx % len(AnimationAssets.idle_buffers)
            frame = AnimationAssets.idle_buffers[idx]

        if frame is not None and frame.shape[:2] == (v_size, v_size):
            alpha = frame[:, :, 3:4] / 255.0
            target_roi = canvas[vy:vy + v_size, vx:vx + v_size, :3]
            canvas[vy:vy + v_size, vx:vx + v_size, :3] = (frame[:, :, :3] * alpha + target_roi * (1.0 - alpha)).astype(np.uint8)

        # Status Pill Banner (Website Style)
        if self.phase == OverlayPhase.SUCCESS:
            banner_text = "VERIFIED • UNLOCKED"
            text_color = (102, 230, 0)  # Apple Green in BGR
            border_color = (88, 209, 48)  # Apple Green in BGR
        elif self.phase == OverlayPhase.FAILURE:
            banner_text = "NOT RECOGNIZED"
            text_color = (58, 69, 255)  # Apple Red in BGR
            border_color = (58, 69, 255)  # Apple Red in BGR
        else:
            banner_text = "FACE ID • SCANNING"
            text_color = (64, 217, 0)  # Subtle Green in BGR
            border_color = (76, 64, 64)  # Subtle dark slate in BGR

        # Draw status pill background at bottom
        pill_y1 = 118
        pill_y2 = 134
        pill_w = 120
        pill_x1 = (w - pill_w) // 2
        pill_x2 = pill_x1 + pill_w
        cv2.rectangle(canvas, (pill_x1, pill_y1), (pill_x2, pill_y2), (32, 28, 28, 255), thickness=-1)
        cv2.putText(
            canvas,
            banner_text,
            (pill_x1 + 8, pill_y1 + 11),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.30,
            text_color + (255,),
            1,
            cv2.LINE_AA
        )

        # Draw smooth outer perimeter ring
        cv2.rectangle(canvas, (0, 0), (w - 1, h - 1), border_color + (255,), thickness=2)

        # Blit directly to HDC via SetDIBitsToDevice (<0.2ms)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # Negative = Top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        ctypes.windll.gdi32.SetDIBitsToDevice(
            hdc,
            0,
            0,
            w,
            h,
            0,
            0,
            0,
            h,
            canvas.ctypes.data,
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
        )

    def _reposition(self) -> None:
        if not self._hwnd or not hasattr(ctypes, "windll"):
            return
        user32 = ctypes.windll.user32
        cx_screen = user32.GetSystemMetrics(SM_CXSCREEN)
        shake_x = math.sin(self.anim_tick * 26.0) * 5.0 * self.shake_decay
        x = int((cx_screen - int(self.card_size)) / 2 + shake_x)
        y = int(self.current_y)

        user32.SetWindowPos(
            self._hwnd,
            HWND_TOPMOST,
            x,
            y,
            int(self.card_size),
            int(self.card_size),
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _anim_worker(self) -> None:
        """60 FPS animation loop with spring translation and frame stepping."""
        while self._anim_running and not self._is_dead:
            start_time = time.time()
            try:
                self.anim_tick += 0.05

                # 1. Smooth sliding pop-out from under camera bezel
                dy = (self.target_y - self.current_y) * 0.24
                if abs(dy) > 0.3:
                    self.current_y += dy
                    self._reposition()
                else:
                    self.current_y = self.target_y

                # 2. Shake decay
                if self.shake_decay > 0:
                    self.shake_decay = max(0.0, self.shake_decay - 0.05)
                    self._reposition()

                # 3. Advance active animation frame index
                if self.phase == OverlayPhase.SCANNING:
                    if AnimationAssets.idle_buffers:
                        self.idle_frame_idx = (self.idle_frame_idx + 1) % len(AnimationAssets.idle_buffers)

                elif self.phase == OverlayPhase.SUCCESS:
                    if AnimationAssets.unlock_buffers and self.unlock_frame_idx < len(AnimationAssets.unlock_buffers) - 1:
                        self.unlock_frame_idx += 1

                elif self.phase == OverlayPhase.FAILURE:
                    if AnimationAssets.failure_buffers and self.failure_frame_idx < len(AnimationAssets.failure_buffers) - 1:
                        self.failure_frame_idx += 1

                # Redraw
                if self._hwnd and hasattr(ctypes, "windll"):
                    ctypes.windll.user32.InvalidateRect(self._hwnd, None, False)

            except Exception:
                pass

            elapsed = time.time() - start_time
            sleep_time = max(0.001, 0.0166 - elapsed)
            time.sleep(sleep_time)

    def _start_anim_loop(self) -> None:
        if not self._anim_running and not self._is_dead:
            self._anim_running = True
            threading.Thread(target=self._anim_worker, daemon=True, name="BlinkWinAnimWorker").start()

    def _stop_anim_loop(self) -> None:
        self._anim_running = False

    def arm(self) -> None:
        """Arms state without popping out onto screen before camera is ready."""
        self.phase = OverlayPhase.SCANNING
        self.idle_frame_idx = 0

    def begin_scanning(self) -> None:
        """Smoothly reveals overlay and springs out from behind camera notch."""
        if not self._is_allowed_to_show():
            self.hide()
            return
        if self.phase == OverlayPhase.SCANNING and self._hwnd and ctypes.windll.user32.IsWindowVisible(self._hwnd):
            return
        self.phase = OverlayPhase.SCANNING
        self.idle_frame_idx = 0
        self.current_y = self.target_y - 35.0
        self.show()
        self._reposition()
        self._start_anim_loop()

    def finish_success(self) -> None:
        self.phase = OverlayPhase.SUCCESS
        self.unlock_frame_idx = 0
        self.current_y = self.target_y
        self.show()
        self._reposition()
        self._start_anim_loop()

    def finish_failure(self, reason: str = "", title: str = "", auto_collapse_ms: int = 1800) -> None:
        self.phase = OverlayPhase.FAILURE
        self.failure_frame_idx = 0
        self.shake_decay = 1.0
        self.show()
        self._reposition()
        self._start_anim_loop()

        def _delayed_collapse():
            time.sleep(auto_collapse_ms / 1000.0)
            if self.phase == OverlayPhase.FAILURE:
                self.clean_close()

        threading.Thread(target=_delayed_collapse, daemon=True).start()

    def show(self) -> None:
        if not self._is_allowed_to_show():
            return
        if self._hwnd and hasattr(ctypes, "windll"):
            ctypes.windll.user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            self._reposition()

    def hide(self) -> None:
        self._stop_anim_loop()
        if self._hwnd and hasattr(ctypes, "windll"):
            ctypes.windll.user32.ShowWindow(self._hwnd, SW_HIDE)

    def clean_close(self) -> None:
        self._is_dead = True
        self._stop_anim_loop()
        self.hide()
        if self._hwnd and hasattr(ctypes, "windll"):
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
            self._hwnd = None

    def dismiss_and_close(self) -> None:
        self.clean_close()

    def attach_to_screensaver(self, ss_xid: int) -> None:
        """On Windows, HWND_TOPMOST keeps the overlay in front of all lock screens."""
        self.show()

    def detach_from_screensaver(self) -> None:
        self.hide()

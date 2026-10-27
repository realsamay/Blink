"""
Blink Lockscreen Overlay Card.
Presents a compact, black squarish card with rounded corners in the center top under the camera.
Renders smooth 60 FPS animations with fluid spring physics and zero text.
"""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
import ctypes
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, List, Tuple

import cairo
import cv2
import numpy as np

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

try:
    x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XReparentWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
    x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XFlush.argtypes = [ctypes.c_void_p]
    _X11_DISP = x11.XOpenDisplay(None)

    # Resilient X11 error handler
    XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _py_x_error_handler(display, error_event):
        return 0
    _c_handler = XErrorHandler(_py_x_error_handler)
    x11.XSetErrorHandler(_c_handler)
except Exception:
    x11 = None
    _X11_DISP = None

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"


class OverlayPhase(Enum):
    CLOSED = "closed"
    SCANNING = "scanning"
    SUCCESS = "success"
    FAILURE = "failure"


class AnimationAssets:
    """Pre-decodes and caches 60 FPS Cairo surfaces for seamless, low-overhead playback."""
    _loaded = False
    _loading_lock = threading.Lock()

    idle_surfaces: List[cairo.ImageSurface] = []
    idle_buffers: List[np.ndarray] = []

    unlock_surfaces: List[cairo.ImageSurface] = []
    unlock_buffers: List[np.ndarray] = []

    failure_surfaces: List[cairo.ImageSurface] = []
    failure_buffers: List[np.ndarray] = []

    @classmethod
    def load_all(cls, target_size: int = 116) -> None:
        with cls._loading_lock:
            if cls._loaded:
                return

            def _load_video(filename: str) -> Tuple[List[cairo.ImageSurface], List[np.ndarray]]:
                path = RESOURCES_DIR / filename
                surfs = []
                bufs = []
                if not path.is_file():
                    return surfs, bufs

                cap = cv2.VideoCapture(str(path))
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    resized = cv2.resize(frame, (target_size, target_size), interpolation=cv2.INTER_AREA)
                    bgra = cv2.cvtColor(resized, cv2.COLOR_BGR2BGRA)
                    surf = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, target_size, target_size, target_size * 4)
                    surfs.append(surf)
                    bufs.append(bgra)
                cap.release()
                return surfs, bufs

            cls.idle_surfaces, cls.idle_buffers = _load_video("idleanimation.mp4")
            cls.unlock_surfaces, cls.unlock_buffers = _load_video("unlockanimation.mp4")
            cls.failure_surfaces, cls.failure_buffers = _load_video("unsuccessfulunlockanimation.mp4")

            cls._loaded = True
            print(f"[AnimationAssets] Cached {len(cls.idle_surfaces)} idle, {len(cls.unlock_surfaces)} unlock, {len(cls.failure_surfaces)} failure surfaces (size: {target_size}x{target_size}).")


class NotchOverlayWindow(Gtk.Window):
    def __init__(self, on_retry_clicked: Optional[Callable[[], None]] = None):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.on_retry_clicked = on_retry_clicked
        self._is_dead = False
        self._current_parent_xid = None

        # Compact, balanced proportions: 116x116 card inside a 150x150 composited canvas
        self.card_size = 116.0
        self.corner_radius = 24.0
        self.window_size = 150.0

        # Preload animations into memory
        AnimationAssets.load_all(target_size=int(self.card_size))

        # Window configuration
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.phase = OverlayPhase.CLOSED
        self.is_hovered = False

        # Animation tracking
        self.idle_frame_idx = 0
        self.unlock_frame_idx = 0
        self.failure_frame_idx = 0

        self.anim_tick = 0.0
        self.shake_decay = 0.0

        # Authentic Apple spring dynamics (smooth bounce-in, liquid dismiss)
        self.current_scale = 0.2
        self.scale_velocity = 0.0
        self.target_scale = 1.0
        self.spring_k = 0.28
        self.spring_damping = 0.68

        self.target_y = 16.0

        # Event handling
        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
        )
        self.connect("draw", self.on_draw)
        self.connect("enter-notify-event", self.on_enter)
        self.connect("leave-notify-event", self.on_leave)
        self.connect("button-press-event", self.on_click)
        self.connect("destroy", self._on_destroy)

        self.set_size_request(int(self.window_size), int(self.window_size))
        self.realize()
        self.reposition()

        self._anim_running = False
        self._is_desktop_preview = False

    def _is_allowed_to_show(self) -> bool:
        """Enforces strict lockscreen-only display policy: Never appears on desktop."""
        if getattr(self, "_is_desktop_preview", False):
            return True
        try:
            from blink.system.lock_monitor import LockMonitor
            return LockMonitor.is_screen_actually_locked()
        except Exception:
            return False

    def _start_anim_loop(self) -> None:
        """Starts 60 FPS animation timer on-demand only when overlay is active."""
        if getattr(self, "_is_dead", False) or not self._is_allowed_to_show():
            return
        if not getattr(self, "_anim_running", False):
            self._anim_running = True
            GLib.timeout_add(16, self._on_anim_frame)

    def _stop_anim_loop(self) -> None:
        self._anim_running = False

    def clean_close(self) -> None:
        self._is_dead = True
        self._stop_anim_loop()
        try:
            self.hide()
            self.detach_from_screensaver()
            self.destroy()
        except Exception:
            pass

    def dismiss_and_close(self) -> None:
        """Smoothly shrinks the card back into the camera notch before closing."""
        if getattr(self, "_is_dead", False):
            return
        self.target_scale = 0.0
        self.spring_k = 0.32
        self.spring_damping = 0.65
        # Fallback timeout in case animation loop ends
        GLib.timeout_add(220, self.clean_close)

    def _on_destroy(self, widget):
        self._is_dead = True

    def raise_to_top(self) -> None:
        if getattr(self, "_is_dead", False):
            return
        gdk_win = self.get_window()
        if not gdk_win or gdk_win.is_destroyed():
            return
        try:
            gdk_win.raise_()
            if x11 and _X11_DISP:
                xid = gdk_win.get_xid()
                x11.XRaiseWindow(_X11_DISP, xid)
                x11.XFlush(_X11_DISP)
        except Exception:
            pass

    def reposition(self) -> None:
        if getattr(self, "_is_dead", False):
            return
        screen = self.get_screen()
        display = screen.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            geom = monitor.get_geometry()
            x = geom.x + (geom.width - int(self.window_size)) // 2
            y = geom.y + int(self.target_y)
            try:
                self.set_size_request(int(self.window_size), int(self.window_size))
                self.move(int(x), int(y))
                self.resize(int(self.window_size), int(self.window_size))
            except Exception:
                pass

        self.raise_to_top()

    @classmethod
    def find_screensaver_window_xid(cls) -> Optional[int]:
        try:
            res = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True, timeout=0.3)
            for line in res.stdout.splitlines():
                if "cinnamon-screensaver-window" in line:
                    parts = line.strip().split()
                    if parts:
                        return int(parts[0], 16)
        except Exception:
            pass
        return None

    def attach_to_screensaver(self, ss_xid: int) -> None:
        """Reparents overlay into the screensaver window so it draws securely on top."""
        if not x11 or not _X11_DISP or getattr(self, "_is_dead", False):
            return
        if getattr(self, "_current_parent_xid", None) == ss_xid:
            return
        gdk_win = self.get_window()
        if not gdk_win:
            self.realize()
            gdk_win = self.get_window()
        if gdk_win and not gdk_win.is_destroyed():
            xid = gdk_win.get_xid()
            screen = self.get_screen()
            display = screen.get_display()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geom = monitor.get_geometry()
            x = geom.x + (geom.width - int(self.window_size)) // 2
            y = geom.y + int(self.target_y)
            try:
                print(f"[NotchOverlay] Attaching popout card {hex(xid)} into screensaver {hex(ss_xid)} at ({x}, {y})")
                x11.XReparentWindow(_X11_DISP, xid, ss_xid, int(x), int(y))
                x11.XMapWindow(_X11_DISP, xid)
                x11.XRaiseWindow(_X11_DISP, xid)
                x11.XFlush(_X11_DISP)
                self._current_parent_xid = ss_xid
                self.show_all()
                self.reposition()
            except Exception as e:
                print(f"[NotchOverlay] Error reparenting to screensaver: {e}")

    def detach_from_screensaver(self) -> None:
        """Safely unmaps and hides overlay upon screen unlock."""
        self._stop_anim_loop()
        self.hide()
        if x11 and _X11_DISP:
            gdk_win = self.get_window()
            if gdk_win and not gdk_win.is_destroyed():
                try:
                    xid = gdk_win.get_xid()
                    x11.XUnmapWindow(_X11_DISP, xid)
                    x11.XFlush(_X11_DISP)
                except Exception:
                    pass
        self._current_parent_xid = None

    def show_all(self) -> None:
        if not self._is_allowed_to_show():
            self.hide()
            return
        super().show_all()

    def arm(self) -> None:
        if not self._is_allowed_to_show():
            self.hide()
            return
        self.phase = OverlayPhase.SCANNING
        self.idle_frame_idx = 0
        # Trigger smooth pop-in spring
        self.current_scale = 0.2
        self.scale_velocity = 0.0
        self.target_scale = 1.0
        self.show_all()
        self.reposition()
        self.queue_draw()
        self._start_anim_loop()

    def begin_scanning(self) -> None:
        if not self._is_allowed_to_show():
            self.hide()
            return
        self.phase = OverlayPhase.SCANNING
        self.target_scale = 1.0
        self.show_all()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

    def set_progress(self, progress: float, subtitle: Optional[str] = None) -> None:
        # Zero text: pure animations convey all feedback
        pass

    def finish_success(self) -> None:
        self.phase = OverlayPhase.SUCCESS
        self.unlock_frame_idx = 0
        self.target_scale = 1.0
        # Give a slight gentle bounce upon success
        self.scale_velocity = max(self.scale_velocity, 0.04)
        self.show_all()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

    def finish_failure(self, reason: str = "", title: str = "", auto_collapse_ms: int = 1800) -> None:
        self.phase = OverlayPhase.FAILURE
        self.failure_frame_idx = 0
        self.shake_decay = 1.0  # Trigger spring shake
        self.show_all()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

        GLib.timeout_add(auto_collapse_ms, self._auto_collapse_if_unhovered)

    def show_notice(self, title: str, subtitle: str, auto_collapse_ms: int = 2000) -> None:
        self.finish_failure(reason=subtitle, title=title, auto_collapse_ms=auto_collapse_ms)

    def _flush_updates(self) -> None:
        if getattr(self, "_is_dead", False) or not self._is_allowed_to_show():
            return
        gdk_win = self.get_window()
        if gdk_win and not gdk_win.is_destroyed():
            try:
                gdk_win.process_updates(True)
            except Exception:
                pass
        if x11 and _X11_DISP:
            try:
                x11.XFlush(_X11_DISP)
            except Exception:
                pass

    def disarm(self) -> bool:
        self.dismiss_and_close()
        return False

    def _auto_collapse_if_unhovered(self) -> bool:
        if not self.is_hovered and self.phase in (OverlayPhase.FAILURE, OverlayPhase.SUCCESS):
            self.dismiss_and_close()
        return False

    def _on_anim_frame(self) -> bool:
        if getattr(self, "_is_dead", False) or not getattr(self, "_anim_running", False):
            self._anim_running = False
            return False
        if not self._is_allowed_to_show() or not self.is_visible() or self.phase == OverlayPhase.CLOSED:
            self._anim_running = False
            self.hide()
            return False
        if not self.get_realized():
            return True
        gdk_win = self.get_window()
        if gdk_win is not None and gdk_win.is_destroyed():
            self._is_dead = True
            self._anim_running = False
            return False

        try:
            self.anim_tick += 0.05

            # 1. Authentic Apple Spring Scale Physics
            force = (self.target_scale - self.current_scale) * self.spring_k
            self.scale_velocity = (self.scale_velocity + force) * self.spring_damping
            self.current_scale += self.scale_velocity

            # If dismissing and fully shrunk, cleanly destroy
            if self.target_scale == 0.0 and self.current_scale < 0.04:
                self.clean_close()
                return False

            # 2. Shake decay for error feedback
            if self.shake_decay > 0:
                self.shake_decay = max(0.0, self.shake_decay - 0.05)

            # 3. Advance active animation frame index
            if self.phase == OverlayPhase.SCANNING:
                if AnimationAssets.idle_surfaces:
                    self.idle_frame_idx = (self.idle_frame_idx + 1) % len(AnimationAssets.idle_surfaces)

            elif self.phase == OverlayPhase.SUCCESS:
                if AnimationAssets.unlock_surfaces and self.unlock_frame_idx < len(AnimationAssets.unlock_surfaces) - 1:
                    self.unlock_frame_idx += 1

            elif self.phase == OverlayPhase.FAILURE:
                if AnimationAssets.failure_surfaces and self.failure_frame_idx < len(AnimationAssets.failure_surfaces) - 1:
                    self.failure_frame_idx += 1

            self.queue_draw()
            self._flush_updates()
        except Exception:
            pass
        return True

    def on_enter(self, widget, event):
        self.is_hovered = True
        if self.phase in (OverlayPhase.CLOSED, OverlayPhase.FAILURE):
            if self.on_retry_clicked:
                self.on_retry_clicked()
        return False

    def on_leave(self, widget, event):
        self.is_hovered = False
        return False

    def on_click(self, widget, event):
        if self.on_retry_clicked:
            self.on_retry_clicked()
        return True

    def on_draw(self, widget, cr: cairo.Context) -> bool:
        if getattr(self, "_is_dead", False) or not self._is_allowed_to_show():
            self.hide()
            return False

        # 1. Clear entire canvas to 100% transparent alpha
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        cr.restore()
        cr.set_operator(cairo.OPERATOR_OVER)

        w = float(widget.get_allocated_width())
        h = float(widget.get_allocated_height())
        cx = w / 2.0
        cy = h / 2.0

        card_w = self.card_size
        card_h = self.card_size
        rad = self.corner_radius

        # 2. Apply spring scale and horizontal shake centered inside the canvas
        shake_x = math.sin(self.anim_tick * 28.0) * 6.0 * self.shake_decay
        cr.save()
        cr.translate(cx + shake_x, cy)
        cr.scale(self.current_scale, self.current_scale)
        cr.translate(-card_w / 2.0, -card_h / 2.0)

        # 3. Compact Rounded Squircle Container Path
        cr.new_sub_path()
        cr.arc(card_w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(card_w - rad, card_h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, card_h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

        # Obsidian black glass background
        cr.set_source_rgba(0.02, 0.02, 0.03, 0.98)
        cr.fill_preserve()

        # Clip content to rounded card
        cr.clip()

        # 4. Paint Active Animation Surface
        curr_surf: Optional[cairo.ImageSurface] = None
        if self.phase == OverlayPhase.SUCCESS and AnimationAssets.unlock_surfaces:
            curr_surf = AnimationAssets.unlock_surfaces[self.unlock_frame_idx]
        elif self.phase == OverlayPhase.FAILURE and AnimationAssets.failure_surfaces:
            curr_surf = AnimationAssets.failure_surfaces[self.failure_frame_idx]
        elif AnimationAssets.idle_surfaces:
            curr_surf = AnimationAssets.idle_surfaces[self.idle_frame_idx]

        if curr_surf is not None:
            cr.set_source_surface(curr_surf, 0, 0)
            cr.paint()

        cr.reset_clip()

        # 5. Smooth High-DPI Border Stroke
        cr.new_sub_path()
        cr.arc(card_w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(card_w - rad, card_h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, card_h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

        if self.phase == OverlayPhase.SUCCESS:
            cr.set_source_rgba(0.19, 0.82, 0.35, 0.90)  # Apple Green
            cr.set_line_width(2.0)
        elif self.phase == OverlayPhase.FAILURE:
            cr.set_source_rgba(1.0, 0.27, 0.23, 0.90)  # Apple Red
            cr.set_line_width(2.0)
        else:
            cr.set_source_rgba(0.25, 0.25, 0.30, 0.60)  # Subtle Dark Slate
            cr.set_line_width(1.5)
        cr.stroke()

        cr.restore()
        return False

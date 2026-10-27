"""
Blink Lockscreen Overlay Card.
Presents a black squarish card with rounded corners in the center top under the camera area.
Renders smooth 60 FPS animations (idle scanning, unlock success, failure) with zero text.
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
    """Pre-decodes and caches 60 FPS Cairo surfaces for seamless playback with zero lag."""
    _loaded = False
    _loading_lock = threading.Lock()

    idle_surfaces: List[cairo.ImageSurface] = []
    idle_buffers: List[np.ndarray] = []

    unlock_surfaces: List[cairo.ImageSurface] = []
    unlock_buffers: List[np.ndarray] = []

    failure_surfaces: List[cairo.ImageSurface] = []
    failure_buffers: List[np.ndarray] = []

    @classmethod
    def load_all(cls, target_size: int = 150) -> None:
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
                    # Force 100% alpha on non-black content, seamless edge blending
                    surf = cairo.ImageSurface.create_for_data(bgra, cairo.FORMAT_ARGB32, target_size, target_size, target_size * 4)
                    surfs.append(surf)
                    bufs.append(bgra)
                cap.release()
                return surfs, bufs

            cls.idle_surfaces, cls.idle_buffers = _load_video("idleanimation.mp4")
            cls.unlock_surfaces, cls.unlock_buffers = _load_video("unlockanimation.mp4")
            cls.failure_surfaces, cls.failure_buffers = _load_video("unsuccessfulunlockanimation.mp4")

            cls._loaded = True
            print(f"[AnimationAssets] Cached {len(cls.idle_surfaces)} idle, {len(cls.unlock_surfaces)} unlock, {len(cls.failure_surfaces)} failure surfaces.")


class NotchOverlayWindow(Gtk.Window):
    def __init__(self, on_retry_clicked: Optional[Callable[[], None]] = None):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.on_retry_clicked = on_retry_clicked
        self._is_dead = False
        self._current_parent_xid = None

        # Preload animations into memory
        AnimationAssets.load_all(target_size=150)

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

        # Card dimensions: black squarish card with rounded corners
        self.card_size = 150.0
        self.corner_radius = 28.0

        # Animation tracking
        self.idle_frame_idx = 0
        self.unlock_frame_idx = 0
        self.failure_frame_idx = 0

        self.anim_tick = 0.0
        self.shake_decay = 0.0

        # Pop-out physics: starts tucked under camera bezel, springs smoothly out
        self.pop_progress = 0.0
        self.target_y = 20.0
        self.current_y = -self.card_size

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

        self.set_size_request(int(self.card_size), int(self.card_size))
        self.realize()
        self.apply_shape_mask()
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

    def apply_shape_mask(self) -> None:
        if getattr(self, "_is_dead", False):
            return
        w = int(self.card_size)
        h = int(self.card_size)
        rad = self.corner_radius
        surf = cairo.ImageSurface(cairo.FORMAT_A1, w, h)
        cr = cairo.Context(surf)
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(1, 1, 1, 1)

        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()

        try:
            reg = Gdk.cairo_region_create_from_surface(surf)
            self.shape_combine_region(reg)
        except Exception:
            pass

    def clean_close(self) -> None:
        self._is_dead = True
        self._stop_anim_loop()
        try:
            self.hide()
            self.detach_from_screensaver()
            self.destroy()
        except Exception:
            pass

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
            shake_x = math.sin(self.anim_tick * 28.0) * 8.0 * self.shake_decay
            x = geom.x + (geom.width - int(self.card_size)) // 2 + int(shake_x)
            y = int(self.current_y)
            try:
                self.set_size_request(int(self.card_size), int(self.card_size))
                self.move(int(x), int(y))
                self.resize(int(self.card_size), int(self.card_size))
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
            x = geom.x + (geom.width - int(self.card_size)) // 2
            y = int(self.target_y)
            try:
                print(f"[NotchOverlay] Reparenting squarish card {hex(xid)} into screensaver {hex(ss_xid)} at ({x}, {y})")
                x11.XReparentWindow(_X11_DISP, xid, ss_xid, int(x), int(y))
                x11.XMapWindow(_X11_DISP, xid)
                x11.XRaiseWindow(_X11_DISP, xid)
                x11.XFlush(_X11_DISP)
                self._current_parent_xid = ss_xid
                self.show_all()
                self.apply_shape_mask()
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
        self.pop_progress = 0.0
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._start_anim_loop()

    def begin_scanning(self) -> None:
        if not self._is_allowed_to_show():
            self.hide()
            return
        self.phase = OverlayPhase.SCANNING
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

    def set_progress(self, progress: float, subtitle: Optional[str] = None) -> None:
        # User requested no text/telemetry, animation alone conveys the status
        pass

    def finish_success(self) -> None:
        self.phase = OverlayPhase.SUCCESS
        self.unlock_frame_idx = 0
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

    def finish_failure(self, reason: str = "", title: str = "", auto_collapse_ms: int = 1800) -> None:
        self.phase = OverlayPhase.FAILURE
        self.failure_frame_idx = 0
        self.shake_decay = 1.0  # Trigger spring shake
        self.show_all()
        self.apply_shape_mask()
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
        self._stop_anim_loop()
        self.phase = OverlayPhase.CLOSED
        self.detach_from_screensaver()
        self.hide()
        return False

    def _auto_collapse_if_unhovered(self) -> bool:
        if not self.is_hovered and self.phase in (OverlayPhase.FAILURE, OverlayPhase.SUCCESS):
            self.disarm()
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

            # 1. Pop-out physics interpolation (springs smoothly out from top bezel)
            if self.pop_progress < 1.0:
                self.pop_progress = min(1.0, self.pop_progress + 0.15)
                # Cubic ease-out
                ease = 1.0 - math.pow(1.0 - self.pop_progress, 3)
                start_y = -self.card_size
                self.current_y = start_y + (self.target_y - start_y) * ease
                self.reposition()
            else:
                self.current_y = self.target_y

            # 2. Shake decay
            if self.shake_decay > 0:
                self.shake_decay = max(0.0, self.shake_decay - 0.05)
                self.reposition()

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

        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
        cr.paint()
        cr.restore()
        cr.set_operator(cairo.OPERATOR_OVER)

        w = float(widget.get_allocated_width())
        h = float(widget.get_allocated_height())
        rad = self.corner_radius

        # 1. Black Squarish Card Container with Rounded Corners
        cr.save()
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

        # Deep obsidian black background
        cr.set_source_rgba(0.02, 0.02, 0.03, 0.98)
        cr.fill_preserve()

        # Clip content to rounded squarish card
        cr.clip()

        # 2. Render Active Animation Surface
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

        # 3. Elegant Perimeter Border
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

        if self.phase == OverlayPhase.SUCCESS:
            cr.set_source_rgba(0.19, 0.82, 0.35, 0.85)  # Apple Green
            cr.set_line_width(2.0)
        elif self.phase == OverlayPhase.FAILURE:
            cr.set_source_rgba(1.0, 0.27, 0.23, 0.85)  # Apple Red
            cr.set_line_width(2.0)
        else:
            cr.set_source_rgba(0.25, 0.25, 0.30, 0.55)  # Subtle Dark Slate
            cr.set_line_width(1.5)
        cr.stroke()

        cr.restore()
        return False

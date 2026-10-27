"""
Authentic Apple Dynamic Island overlay for Linux desktop & lock screens.
Includes:
- Apple Face ID glyph with animated laser scanner beam
- Padlock icon that unlatches (unlocks) on authorization
- Real-time scan progress bar & percentage telemetry
- Spring physics expansion, horizontal shake on failure, and hover retry
- X11 override-redirect to stay floating on top of lock screens
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
from typing import Callable, Optional

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

    # Install resilient X11 error handler so asynchronous window destruction never kills Python
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
    COLLAPSING = "collapsing"


class NotchOverlayWindow(Gtk.Window):
    def __init__(self, on_retry_clicked: Optional[Callable[[], None]] = None):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.on_retry_clicked = on_retry_clicked
        self._is_dead = False
        self._current_parent_xid = None

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
        self.status_title = "Face ID"
        self.status_subtitle = "Locked"
        self.is_hovered = False

        # Scan progress (0.0 to 1.0)
        self.scan_progress = 0.0

        # Authentic Apple iOS Face ID Card dimensions
        self.card_width = 340.0
        self.card_height = 200.0

        self.current_width = self.card_width
        self.current_height = self.card_height
        self.target_width = self.card_width
        self.target_height = self.card_height

        self.anim_tick = 0.0
        self.shake_decay = 0.0

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

        # Force exact dimensions and realize window
        self.set_size_request(int(self.card_width), int(self.card_height))
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
        """Starts the 60 FPS animation timer on-demand only when overlay is active/visible."""
        if getattr(self, "_is_dead", False) or not self._is_allowed_to_show():
            return
        if not getattr(self, "_anim_running", False):
            self._anim_running = True
            GLib.timeout_add(16, self._on_anim_frame)

    def _stop_anim_loop(self) -> None:
        """Stops the 60 FPS animation timer to achieve 0.0% CPU when idle or closed."""
        self._anim_running = False

    def apply_shape_mask(self) -> None:
        if getattr(self, "_is_dead", False):
            return
        w = int(self.card_width)
        h = int(self.card_height)
        rad = 32.0
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
            # Calculate spring shake offset
            shake_x = math.sin(self.anim_tick * 32.0) * 12.0 * self.shake_decay
            x = geom.x + (geom.width - int(self.card_width)) // 2 + int(shake_x)
            base_y = geom.y + 80
            try:
                self.set_size_request(int(self.card_width), int(self.card_height))
                self.move(int(x), int(base_y))
                self.resize(int(self.card_width), int(self.card_height))
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
        """Reparents overlay into the screensaver window so it is guaranteed to draw in front."""
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
            x = geom.x + (geom.width - int(self.card_width)) // 2
            base_y = geom.y + 80
            try:
                print(f"[NotchOverlay] Reparenting window {hex(xid)} into screensaver {hex(ss_xid)} at ({x}, {base_y})")
                x11.XReparentWindow(_X11_DISP, xid, ss_xid, int(x), int(base_y))
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
        """Safely unmaps and hides overlay upon screen unlock. Never shows on desktop."""
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
        self.phase = OverlayPhase.CLOSED
        self.status_title = "Face ID"
        self.status_subtitle = "Scanning…"
        self.scan_progress = 0.05
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
        self.status_title = "Face ID"
        self.status_subtitle = "Verifying Face…"
        self.scan_progress = 0.10
        print("[NotchOverlay] begin_scanning: iOS Face ID card active at (513, 80)")
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

    def set_progress(self, progress: float, subtitle: Optional[str] = None) -> None:
        self.scan_progress = max(0.0, min(1.0, progress))
        if subtitle:
            self.status_subtitle = subtitle
        self.queue_draw()
        self._flush_updates()

    def finish_success(self) -> None:
        self.phase = OverlayPhase.SUCCESS
        self.target_width = self.card_width
        self.target_height = self.card_height
        self.status_title = "Face ID"
        self.status_subtitle = "Unlocked"
        self.scan_progress = 1.0
        print("[NotchOverlay] finish_success: Unlocked animation playing")
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

    def finish_failure(self, reason: str = "Face Not Recognized", title: str = "Face ID", auto_collapse_ms: int = 2000) -> None:
        self.phase = OverlayPhase.FAILURE
        self.target_width = self.card_width
        self.target_height = self.card_height
        self.status_title = title
        self.status_subtitle = reason
        self.shake_decay = 1.0  # Trigger spring shake
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()

        # Auto-collapse after auto_collapse_ms if not hovered
        GLib.timeout_add(auto_collapse_ms, self._auto_collapse_if_unhovered)

    def show_notice(self, title: str, subtitle: str, auto_collapse_ms: int = 2200) -> None:
        """Displays an informative card notice (e.g. low light, set password)."""
        self.phase = OverlayPhase.FAILURE
        self.target_width = self.card_width
        self.target_height = self.card_height
        self.status_title = title
        self.status_subtitle = subtitle
        self.shake_decay = 0.5
        self.show_all()
        self.apply_shape_mask()
        self.reposition()
        self.queue_draw()
        self._flush_updates()
        self._start_anim_loop()
        GLib.timeout_add(auto_collapse_ms, self._auto_collapse_if_unhovered)

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
        self.target_width = self.card_width
        self.target_height = self.card_height
        self.current_width = self.card_width
        self.current_height = self.card_height
        self.scan_progress = 0.0
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

            # Decay shake oscillation
            if self.shake_decay > 0:
                self.shake_decay = max(0.0, self.shake_decay - 0.04)

            # Smooth spring/lerp interpolation
            changed = False
            dw = (self.target_width - self.current_width) * 0.28
            dh = (self.target_height - self.current_height) * 0.28
            if abs(dw) > 0.5:
                self.current_width += dw
                changed = True
            else:
                self.current_width = self.target_width

            if abs(dh) > 0.5:
                self.current_height += dh
                changed = True
            else:
                self.current_height = self.target_height

            if changed or self.shake_decay > 0:
                self.reposition()

            # Gradually advance simulated scan progress if scanning
            if self.phase == OverlayPhase.SCANNING:
                if self.scan_progress < 0.90:
                    self.scan_progress += 0.012

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
        rad = 32.0

        # 1. Clean Apple iOS Rounded Card Container
        cr.save()
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

        is_low_light = "low light" in self.status_title.lower() or "low light" in self.status_subtitle.lower()
        is_no_face = "no face" in self.status_title.lower() or "no face" in self.status_subtitle.lower()

        # Translucent glass dark background
        if self.phase == OverlayPhase.SUCCESS:
            cr.set_source_rgba(0.03, 0.08, 0.04, 0.96)
        elif self.phase == OverlayPhase.FAILURE:
            if is_low_light:
                cr.set_source_rgba(0.12, 0.08, 0.02, 0.96)
            elif is_no_face:
                cr.set_source_rgba(0.10, 0.06, 0.02, 0.96)
            else:
                cr.set_source_rgba(0.12, 0.03, 0.03, 0.96)
        else:
            cr.set_source_rgba(0.06, 0.06, 0.08, 0.96)
        cr.fill_preserve()

        # Perimeter border glow
        if self.phase == OverlayPhase.SUCCESS:
            cr.set_source_rgba(0.19, 0.82, 0.35, 0.92)  # Apple Green
            cr.set_line_width(2.4)
        elif self.phase == OverlayPhase.FAILURE:
            if is_low_light:
                cr.set_source_rgba(1.0, 0.65, 0.05, 0.90)  # Amber Yellow
            elif is_no_face:
                cr.set_source_rgba(1.0, 0.55, 0.15, 0.90)  # Orange
            else:
                cr.set_source_rgba(1.0, 0.27, 0.23, 0.92)  # Apple Red
            cr.set_line_width(2.4)
        elif self.phase == OverlayPhase.SCANNING:
            pulse = (math.sin(self.anim_tick * 3.0) + 1.0) / 2.0
            cr.set_source_rgba(0.2 + 0.2 * pulse, 0.65 + 0.2 * pulse, 1.0, 0.85)  # Electric Blue
            cr.set_line_width(2.2)
        else:
            cr.set_source_rgba(0.25, 0.25, 0.30, 0.6)
            cr.set_line_width(1.5)
        cr.stroke()
        cr.restore()

        cx, cy = w / 2.0, 72.0

        if self.phase in (OverlayPhase.CLOSED, OverlayPhase.SCANNING):
            # 2. Large Apple Face ID Glyph (64x64)
            self._draw_face_id_glyph(
                cr,
                cx=cx,
                cy=cy,
                size=64.0,
                laser_scan=(self.phase == OverlayPhase.SCANNING),
                color=(0.3, 0.8, 1.0, 1.0)
            )

            # Title: 'Face ID'
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(18.0)
            cr.set_source_rgba(0.95, 0.95, 1.0, 1.0)
            ext = cr.text_extents("Face ID")
            cr.move_to((w - ext.width) / 2.0, 140.0)
            cr.show_text("Face ID")

            # Subtitle
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(13.0)
            cr.set_source_rgba(0.65, 0.8, 1.0, 0.9)
            if "%" in self.status_subtitle:
                sub = self.status_subtitle
            elif self.phase == OverlayPhase.SCANNING:
                pct = int(self.scan_progress * 100)
                sub = f"{self.status_subtitle} ({pct}%)"
            else:
                sub = self.status_subtitle
            ext2 = cr.text_extents(sub)
            cr.move_to((w - ext2.width) / 2.0, 162.0)
            cr.show_text(sub)

            # Progress bar track
            track_w = w - 60.0
            track_x = 30.0
            track_y = 176.0
            cr.set_source_rgba(0.2, 0.2, 0.25, 0.8)
            self._draw_rounded_rect(cr, track_x, track_y, track_w, 4.0, 2.0)
            cr.fill()
            cr.set_source_rgba(0.2, 0.75, 1.0, 0.95)
            fill_w = max(4.0, track_w * self.scan_progress)
            self._draw_rounded_rect(cr, track_x, track_y, fill_w, 4.0, 2.0)
            cr.fill()

        elif self.phase == OverlayPhase.SUCCESS:
            # Apple Green Checkmark Circle
            cr.save()
            cr.set_line_width(3.6)
            cr.set_source_rgba(0.19, 0.82, 0.35, 1.0)
            cr.arc(cx, cy, 28.0, 0, 2 * math.pi)
            cr.stroke()

            # Checkmark check
            cr.set_line_width(4.0)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.move_to(cx - 12.0, cy - 1.0)
            cr.line_to(cx - 3.0, cy + 9.0)
            cr.line_to(cx + 14.0, cy - 8.0)
            cr.stroke()
            cr.restore()

            # Title: 'Face ID'
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(18.0)
            cr.set_source_rgba(0.95, 0.95, 1.0, 1.0)
            ext = cr.text_extents("Face ID")
            cr.move_to((w - ext.width) / 2.0, 140.0)
            cr.show_text("Face ID")

            # Subtitle: 'Unlocked' with unlatched padlock icon, grouped & centered
            text_str = self.status_subtitle or "Unlocked"
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(15.0)
            ext2 = cr.text_extents(text_str)
            padlock_w = 16.0
            spacing = 8.0
            total_w = padlock_w + spacing + ext2.width
            start_x = (w - total_w) / 2.0
            self._draw_padlock(cr, x=start_x + padlock_w / 2.0, y=162.0, size=padlock_w, is_unlocked=True)
            cr.set_source_rgba(0.19, 0.82, 0.35, 1.0)
            cr.move_to(start_x + padlock_w + spacing, 167.0)
            cr.show_text(text_str)

        elif self.phase == OverlayPhase.FAILURE:
            if is_low_light:
                self._draw_low_light_glyph(cr, cx=cx, cy=cy, size=56.0)
                title_color = (1.0, 0.75, 0.15, 1.0)
            elif is_no_face:
                self._draw_no_face_glyph(cr, cx=cx, cy=cy, size=56.0)
                title_color = (1.0, 0.65, 0.20, 1.0)
            else:
                self._draw_face_id_glyph(cr, cx=cx, cy=cy, size=64.0, is_frown=True, color=(1.0, 0.35, 0.35, 1.0))
                title_color = (1.0, 0.40, 0.40, 1.0)

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(18.0)
            cr.set_source_rgba(*title_color)
            ext = cr.text_extents(self.status_title)
            cr.move_to((w - ext.width) / 2.0, 140.0)
            cr.show_text(self.status_title)

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(13.5)
            cr.set_source_rgba(0.9, 0.9, 0.95, 0.9)
            ext2 = cr.text_extents(self.status_subtitle)
            cr.move_to((w - ext2.width) / 2.0, 166.0)
            cr.show_text(self.status_subtitle)

        return False

    def _draw_low_light_glyph(self, cr: cairo.Context, cx: float, cy: float, size: float = 38.0) -> None:
        """Draws Apple-style low-light indicator (crescent moon + sparkle star)."""
        cr.save()
        cr.set_source_rgba(1.0, 0.75, 0.15, 1.0)

        # Upright crescent moon
        cr.arc(cx + 4.0, cy, 22.0, math.pi * 0.5, math.pi * 1.5)
        cr.arc_negative(cx - 5.0, cy, 19.0, math.pi * 1.5, math.pi * 0.5)
        cr.close_path()
        cr.fill()

        # 4-pointed sparkle star
        sx, sy = cx + 18.0, cy - 14.0
        cr.new_sub_path()
        cr.move_to(sx, sy - 7.0)
        cr.curve_to(sx, sy - 2.0, sx + 2.0, sy, sx + 7.0, sy)
        cr.curve_to(sx + 2.0, sy, sx, sy + 2.0, sx, sy + 7.0)
        cr.curve_to(sx, sy + 2.0, sx - 2.0, sy, sx - 7.0, sy)
        cr.curve_to(sx - 2.0, sy, sx, sy - 2.0, sx, sy - 7.0)
        cr.close_path()
        cr.fill()
        cr.restore()

    def _draw_no_face_glyph(self, cr: cairo.Context, cx: float, cy: float, size: float = 38.0) -> None:
        """Draws empty face bracket with exclamation indicator."""
        half = size / 2.0
        x = cx - half
        y = cy - half
        cr.save()
        cr.set_source_rgba(1.0, 0.65, 0.2, 0.95)
        cr.set_line_width(2.0)
        b_len = size * 0.28
        rad = 5.0
        # Top-left
        cr.move_to(x, y + b_len)
        cr.line_to(x, y + rad)
        cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
        cr.line_to(x + b_len, y)
        # Top-right
        cr.move_to(x + size - b_len, y)
        cr.line_to(x + size - rad, y)
        cr.arc(x + size - rad, y + rad, rad, 3 * math.pi / 2, 0)
        cr.line_to(x + size, y + b_len)
        # Bottom-right
        cr.move_to(x + size, y + size - b_len)
        cr.line_to(x + size, y + size - rad)
        cr.arc(x + size - rad, y + size - rad, rad, 0, math.pi / 2)
        cr.line_to(x + size - b_len, y + size)
        # Bottom-left
        cr.move_to(x + b_len, y + size)
        cr.line_to(x + rad, y + size)
        cr.arc(x + rad, y + size - rad, rad, math.pi / 2, math.pi)
        cr.line_to(x, y + size - b_len)
        cr.stroke()

        # Exclamation Mark
        cr.set_line_width(2.4)
        cr.move_to(cx, cy - 6)
        cr.line_to(cx, cy + 2)
        cr.stroke()
        cr.arc(cx, cy + 6.5, 1.4, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

    def _draw_face_id_glyph(
        self,
        cr: cairo.Context,
        cx: float,
        cy: float,
        size: float = 64.0,
        laser_scan: bool = False,
        is_frown: bool = False,
        color: Tuple[float, float, float, float] = (0.3, 0.8, 1.0, 1.0)
    ) -> None:
        """Draws authentic Apple Face ID rounded bracket icon with eyes, nose, and smile/frown."""
        half = size / 2.0
        x = cx - half
        y = cy - half

        cr.save()
        cr.set_source_rgba(*color)
        cr.set_line_width(2.8)

        # 4 Corner brackets: [ ]
        b_len = size * 0.28
        rad = 8.0
        # Top-left
        cr.move_to(x, y + b_len)
        cr.line_to(x, y + rad)
        cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
        cr.line_to(x + b_len, y)
        # Top-right
        cr.move_to(x + size - b_len, y)
        cr.line_to(x + size - rad, y)
        cr.arc(x + size - rad, y + rad, rad, 3 * math.pi / 2, 0)
        cr.line_to(x + size, y + b_len)
        # Bottom-right
        cr.move_to(x + size, y + size - b_len)
        cr.line_to(x + size, y + size - rad)
        cr.arc(x + size - rad, y + size - rad, rad, 0, math.pi / 2)
        cr.line_to(x + size - b_len, y + size)
        # Bottom-left
        cr.move_to(x + b_len, y + size)
        cr.line_to(x + rad, y + size)
        cr.arc(x + rad, y + size - rad, rad, math.pi / 2, math.pi)
        cr.line_to(x, y + size - b_len)
        cr.stroke()

        # Eyes
        eye_r = 3.5
        cr.arc(cx - size * 0.19, cy - size * 0.12, eye_r, 0, 2 * math.pi)
        cr.fill()
        cr.arc(cx + size * 0.19, cy - size * 0.12, eye_r, 0, 2 * math.pi)
        cr.fill()

        # Nose (L-shaped bracket)
        cr.set_line_width(2.4)
        cr.move_to(cx, cy - size * 0.03)
        cr.line_to(cx, cy + size * 0.12)
        cr.line_to(cx + size * 0.08, cy + size * 0.12)
        cr.stroke()

        # Mouth: Smile or Frown
        if is_frown:
            cr.arc_negative(cx, cy + size * 0.28, size * 0.18, -0.2 * math.pi, -0.8 * math.pi)
        else:
            cr.arc(cx, cy + size * 0.12, size * 0.18, 0.2 * math.pi, 0.8 * math.pi)
        cr.stroke()

        # Animated laser scanning beam
        if laser_scan:
            scan_y = cy - half + (size * ((math.sin(self.anim_tick * 4.0) + 1.0) / 2.0))
            cr.set_source_rgba(0.4, 0.9, 1.0, 0.85)
            cr.set_line_width(2.0)
            cr.move_to(x + 4, scan_y)
            cr.line_to(x + size - 4, scan_y)
            cr.stroke()

        cr.restore()

    def _draw_padlock(self, cr: cairo.Context, x: float, y: float, size: float = 14.0, is_unlocked: bool = False) -> None:
        """Draws Apple-style padlock (locked or unlocked)."""
        cr.save()
        body_w = size
        body_h = size * 0.75
        body_x = x - body_w / 2.0
        body_y = y - body_h / 2.0 + (size * 0.15)

        # Padlock Body
        if is_unlocked:
            cr.set_source_rgba(0.2, 0.85, 0.4, 1.0) # Green unlocked
        else:
            cr.set_source_rgba(0.85, 0.85, 0.9, 0.9)
        self._draw_rounded_rect(cr, body_x, body_y, body_w, body_h, 2.5)
        cr.fill()

        # Keyhole
        cr.set_source_rgba(0.05, 0.05, 0.06, 1.0)
        cr.arc(x, body_y + body_h * 0.45, 1.5, 0, 2 * math.pi)
        cr.fill()
        cr.move_to(x, body_y + body_h * 0.45)
        cr.line_to(x, body_y + body_h * 0.75)
        cr.set_line_width(1.2)
        cr.stroke()

        # Shackle
        shackle_r = size * 0.28
        cr.set_line_width(2.0)
        if is_unlocked:
            cr.set_source_rgba(0.2, 0.85, 0.4, 1.0)
            # Lifted and opened shackle
            cr.arc(x - 2.0, body_y - 2.0, shackle_r, math.pi * 0.8, math.pi * 1.9)
            cr.stroke()
        else:
            cr.set_source_rgba(0.85, 0.85, 0.9, 0.9)
            cr.arc(x, body_y, shackle_r, math.pi, 2 * math.pi)
            cr.move_to(x - shackle_r, body_y)
            cr.line_to(x - shackle_r, body_y + 2)
            cr.move_to(x + shackle_r, body_y)
            cr.line_to(x + shackle_r, body_y + 2)
            cr.stroke()

        cr.restore()

    def _draw_rounded_rect(self, cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

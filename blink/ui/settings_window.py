"""
Blink Settings Application for Linux.
Designed following Apple iOS 27 and macOS Sequoia glassmorphism guidelines:
liquid glass materials, translucent surfaces, subtle inner light refraction,
full-sensor uncropped camera previews, and unified Apple biometric controls.
"""

from __future__ import annotations

import getpass
import math
import os
import platform
import subprocess
import threading
import time
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

import cv2
import numpy as np

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import cairo

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.ui.enrollment_dialog import EnrollmentDialog
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector, DetectedFace
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder
from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.cues import LivenessCue, LivenessDecisionKind
from blink.system.keystroke_injector import KeystrokeInjector


# ------------------------------------------------------------------------------
# Resource & Icon Helpers
# ------------------------------------------------------------------------------
RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"

def get_blink_logo_pixbuf(size: int = 24) -> Optional[GdkPixbuf.Pixbuf]:
    """Loads the official Blink logo at the requested dimension."""
    for fname in ["blink-logo.svg", "final.png", "logo.png"]:
        p = RESOURCES_DIR / fname
        if p.is_file():
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), size, size, True)
            except Exception:
                continue
    return None


def get_system_idle_delay() -> int:
    """Reads system screensaver idle delay in minutes from Cinnamon / GNOME."""
    for schema in ["org.cinnamon.desktop.session", "org.gnome.desktop.session"]:
        try:
            res = subprocess.run(["gsettings", "get", schema, "idle-delay"], capture_output=True, text=True)
            if res.returncode == 0:
                parts = res.stdout.strip().split()
                sec = int(parts[-1])
                if sec > 0:
                    return max(5, round(sec / 60))
        except Exception:
            pass
    return 15


def set_system_idle_delay(minutes: int) -> None:
    """Applies system screensaver idle delay in minutes to Cinnamon and GNOME."""
    seconds = minutes * 60
    for schema in ["org.cinnamon.desktop.session", "org.gnome.desktop.session"]:
        try:
            subprocess.run(["gsettings", "set", schema, "idle-delay", str(seconds)], capture_output=True)
        except Exception:
            pass


def verify_linux_password(username: str, password: str) -> bool:
    """Verifies password directly against the local Linux user account."""
    if not password:
        return False
    try:
        proc = subprocess.run(
            ["sudo", "-k", "-S", "-u", username, "true"],
            input=f"{password}\n",
            capture_output=True,
            text=True,
            timeout=3.0
        )
        return proc.returncode == 0
    except Exception:
        try:
            proc = subprocess.run(
                ["su", "-c", "true", username],
                input=f"{password}\n",
                capture_output=True,
                text=True,
                timeout=3.0
            )
            return proc.returncode == 0
        except Exception:
            return True


# ------------------------------------------------------------------------------
# Apple iOS 27 / macOS Glassmorphism Stylesheet
# ------------------------------------------------------------------------------
MACOS_CSS = """
/* Liquid Glass & Translucent Obsidian Base */
window.macos-window {
    background-color: rgba(18, 18, 24, 0.88);
    color: #FFFFFF;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro", "Segoe UI", Roboto, Cantarell, sans-serif;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.16);
}

headerbar {
    background-color: rgba(24, 24, 32, 0.72);
    border-bottom: 1px solid rgba(255, 255, 255, 0.09);
    min-height: 42px;
    padding: 0 12px;
}
headerbar .title {
    font-size: 13px;
    font-weight: 600;
    color: #FFFFFF;
    letter-spacing: -0.2px;
}

/* Glass Sidebar */
.sidebar {
    background-color: rgba(16, 16, 22, 0.70);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}
.sidebar-brand-box {
    padding: 14px 14px 8px 14px;
}
.sidebar-brand-title {
    font-size: 14px;
    font-weight: 700;
    color: #FFFFFF;
}
.sidebar-brand-sub {
    font-size: 11px;
    color: #8E8E93;
}
.sidebar-search-box {
    padding: 4px 12px 10px 12px;
}
entry.search-entry {
    background-color: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    color: #FFFFFF;
    font-size: 12px;
    padding: 6px 10px;
    min-height: 24px;
}
entry.search-entry placeholder {
    color: #8E8E93;
}
entry.search-entry:focus {
    border-color: #0A84FF;
    background-color: rgba(255, 255, 255, 0.10);
}

list.sidebar-list {
    background-color: transparent;
}
list.sidebar-list row {
    background-color: transparent;
    border-radius: 8px;
    padding: 6px 10px;
    margin: 2px 8px;
    color: #C7C7CC;
    border: none;
}
list.sidebar-list row:hover {
    background-color: rgba(255, 255, 255, 0.06);
    color: #FFFFFF;
}
list.sidebar-list row:selected {
    background-color: rgba(255, 255, 255, 0.14);
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: #FFFFFF;
}
list.sidebar-list row:selected label {
    font-weight: 600;
    color: #FFFFFF;
}

/* Content Pane */
.content-scroll {
    background-color: transparent;
}
.content-box {
    padding: 24px 32px 48px 32px;
}
.page-title {
    font-size: 22px;
    font-weight: 700;
    color: #FFFFFF;
    margin-bottom: 16px;
    letter-spacing: -0.4px;
}
.group-title {
    font-size: 12px;
    font-weight: 600;
    color: #8E8E93;
    margin-top: 20px;
    margin-bottom: 6px;
    margin-left: 2px;
    letter-spacing: -0.1px;
}
.group-footnote {
    font-size: 11.5px;
    color: #8E8E93;
    margin-top: 6px;
    margin-bottom: 16px;
    margin-left: 2px;
}

/* Frosted Liquid Glass Cards */
.inset-group {
    background-color: rgba(34, 34, 42, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 12px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.15), 0 8px 32px rgba(0, 0, 0, 0.35);
    padding: 0;
}
.inset-row {
    padding: 10px 14px;
    min-height: 42px;
}
.inset-divider {
    background-color: rgba(255, 255, 255, 0.07);
    min-height: 1px;
    margin-left: 14px;
}
.row-label {
    font-size: 13px;
    font-weight: 500;
    color: #FFFFFF;
}
.row-desc {
    font-size: 11px;
    color: #8E8E93;
}

/* Glass Hero Banner */
.glass-hero {
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.09) 0%, rgba(255, 255, 255, 0.03) 100%);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 16px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 10px 30px rgba(0, 0, 0, 0.35);
    padding: 16px 18px;
    margin-bottom: 18px;
}
.hero-title {
    font-size: 17px;
    font-weight: 700;
    color: #FFFFFF;
}
.hero-sub {
    font-size: 11.5px;
    color: #A1A1A6;
}

/* Glass Status Badges */
.pill-badge-active {
    background-color: rgba(52, 199, 89, 0.18);
    border: 1px solid rgba(52, 199, 89, 0.40);
    color: #34C759;
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill-badge-disabled {
    background-color: rgba(255, 159, 10, 0.18);
    border: 1px solid rgba(255, 159, 10, 0.40);
    color: #FF9F0A;
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill-badge-sensor {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.14);
    color: #C7C7CC;
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 500;
}

/* Buttons */
.btn-macos {
    background-color: rgba(255, 255, 255, 0.09);
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 7px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.15), 0 2px 6px rgba(0, 0, 0, 0.20);
    font-size: 12px;
    font-weight: 500;
    padding: 4px 14px;
    min-height: 24px;
}
.btn-macos:hover {
    background-color: rgba(255, 255, 255, 0.16);
    border-color: rgba(255, 255, 255, 0.28);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25), 0 4px 10px rgba(0, 0, 0, 0.30);
}
.btn-macos-primary {
    background-image: linear-gradient(180deg, #0A84FF 0%, #0066CC 100%);
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 7px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.40), 0 4px 14px rgba(10, 132, 255, 0.40);
    font-size: 12px;
    font-weight: 600;
    padding: 5px 16px;
    min-height: 24px;
}
.btn-macos-primary:hover {
    background-image: linear-gradient(180deg, #1A8FFF 0%, #0077EE 100%);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.50), 0 6px 18px rgba(10, 132, 255, 0.55);
}
.btn-macos-danger {
    background-color: rgba(255, 69, 58, 0.16);
    color: #FF6B62;
    border: 1px solid rgba(255, 69, 58, 0.45);
    border-radius: 7px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12);
    font-size: 11.5px;
    font-weight: 500;
    padding: 4px 12px;
}
.btn-macos-danger:hover {
    background-color: #FF453A;
    color: #FFFFFF;
    border-color: #FF453A;
    box-shadow: 0 4px 14px rgba(255, 69, 58, 0.45);
}

/* Switches (Authentic Apple iOS 27 Toggle) */
switch {
    background-color: rgba(255, 255, 255, 0.16);
    background-image: none;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 13px;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.35);
    min-width: 44px;
    min-height: 24px;
    padding: 0;
    margin: 0;
}
switch:checked {
    background-color: #34C759;
    background-image: none;
    border-color: #34C759;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.40), 0 2px 10px rgba(52, 199, 89, 0.45);
}
switch slider {
    background-color: #FFFFFF;
    background-image: none;
    border-radius: 50%;
    min-width: 20px;
    min-height: 20px;
    margin: 1px;
    border: none;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.40);
}

/* Sliders */
scale trough {
    background-color: rgba(255, 255, 255, 0.12);
    border-radius: 3px;
    min-height: 4px;
}
scale highlight {
    background-color: #0A84FF;
    border-radius: 3px;
}
scale slider {
    background-color: #FFFFFF;
    border-radius: 50%;
    min-width: 14px;
    min-height: 14px;
    margin: -5px 0;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.40);
}

/* Text Fields */
entry {
    background-color: rgba(255, 255, 255, 0.08);
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
entry:focus {
    border-color: #0A84FF;
    background-color: rgba(255, 255, 255, 0.12);
}

/* Whole Sensor Camera Viewfinder */
.viewfinder-card {
    background-color: #08080A;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 14px;
    box-shadow: inset 0 0 24px rgba(0, 0, 0, 0.90), 0 12px 36px rgba(0, 0, 0, 0.45);
    padding: 12px;
}
.hud-bar {
    padding: 0 4px 8px 4px;
}
.hud-footer {
    padding: 8px 4px 0 4px;
}

/* Progress Bars */
progressbar trough {
    background-color: rgba(255, 255, 255, 0.10);
    border-radius: 3px;
    min-height: 4px;
}
progressbar progress {
    background-color: #0A84FF;
    border-radius: 3px;
}
"""


# ------------------------------------------------------------------------------
# Cairo Vector Icon Generators (2x Retina Supersampled)
# ------------------------------------------------------------------------------
def create_macos_icon(kind: str, bg_hex: str, size: int = 24) -> cairo.ImageSurface:
    """Renders 2x retina supersampled authentic macOS squircle icons with glass highlight."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size * 2, size * 2)
    surf.set_device_scale(2.0, 2.0)
    cr = cairo.Context(surf)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    h = bg_hex.lstrip("#")
    r, g, b = [int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)]

    # 6px radius squircle on 24px
    rad = 6.0
    cr.new_sub_path()
    cr.arc(size - rad, rad, rad, -math.pi / 2, 0)
    cr.arc(size - rad, size - rad, rad, 0, math.pi / 2)
    cr.arc(rad, size - rad, rad, math.pi / 2, math.pi)
    cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgb(r, g, b)
    cr.fill_preserve()

    # Subtle inner top glass reflection
    cr.set_line_width(1.0)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.25)
    cr.stroke()

    cr.save()
    cr.translate(size / 2.0, size / 2.0)
    cr.set_source_rgb(1.0, 1.0, 1.0)
    cr.set_line_width(1.5)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    if kind == "face_id":
        b_len = 2.8
        cr.move_to(-5.5, -5.5 + b_len); cr.line_to(-5.5, -5.5); cr.line_to(-5.5 + b_len, -5.5)
        cr.move_to(5.5 - b_len, -5.5); cr.line_to(5.5, -5.5); cr.line_to(5.5, -5.5 + b_len)
        cr.move_to(-5.5, 5.5 - b_len); cr.line_to(-5.5, 5.5); cr.line_to(-5.5 + b_len, 5.5)
        cr.move_to(5.5 - b_len, 5.5); cr.line_to(5.5, 5.5); cr.line_to(5.5, 5.5 - b_len)
        cr.stroke()
        cr.arc(-2.2, -2.0, 0.9, 0, 2 * math.pi); cr.fill()
        cr.arc(2.2, -2.0, 0.9, 0, 2 * math.pi); cr.fill()
        cr.move_to(0, -0.6); cr.line_to(0, 1.3); cr.line_to(0.8, 1.3); cr.stroke()
        cr.new_sub_path()
        cr.arc(0, 1.2, 2.8, 0.22 * math.pi, 0.78 * math.pi); cr.stroke()

    elif kind == "camera":
        c_rad = 1.8
        cr.new_sub_path()
        cr.arc(5.5 - c_rad, -2.5 + c_rad, c_rad, -math.pi / 2, 0)
        cr.arc(5.5 - c_rad, 5.5 - c_rad, c_rad, 0, math.pi / 2)
        cr.arc(-5.5 + c_rad, 5.5 - c_rad, c_rad, math.pi / 2, math.pi)
        cr.arc(-5.5 + c_rad, -2.5 + c_rad, c_rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()
        cr.rectangle(-2.2, -4.5, 4.4, 2.0); cr.fill()
        cr.set_source_rgb(r, g, b)
        cr.arc(0, 1.5, 2.4, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.arc(0, 1.5, 1.2, 0, 2 * math.pi); cr.fill()
        cr.arc(0.7, 0.8, 0.45, 0, 2 * math.pi); cr.fill()

    elif kind == "liveness":
        cr.move_to(0, -5.5)
        cr.curve_to(2.0, -5.5, 4.5, -4.8, 5.0, -3.2)
        cr.curve_to(5.0, 1.5, 2.5, 4.8, 0, 6.0)
        cr.curve_to(-2.5, 4.8, -5.0, 1.5, -5.0, -3.2)
        cr.curve_to(-4.5, -4.8, -2.0, -5.5, 0, -5.5)
        cr.close_path()
        cr.stroke()
        cr.move_to(-2.2, 0.0); cr.line_to(-0.6, 1.8); cr.line_to(2.5, -1.8); cr.stroke()

    elif kind == "diagnostics":
        cr.set_line_width(2.0)
        cr.move_to(-3.6, 3.5); cr.line_to(-3.6, -1.0); cr.stroke()
        cr.move_to(0.0, 3.5); cr.line_to(0.0, -4.5); cr.stroke()
        cr.move_to(3.6, 3.5); cr.line_to(3.6, 0.5); cr.stroke()

    elif kind == "about":
        cr.set_line_width(1.3)
        cr.arc(0, 0, 5.5, 0, 2 * math.pi); cr.stroke()
        cr.arc(0, -2.4, 0.8, 0, 2 * math.pi); cr.fill()
        cr.set_line_width(1.4)
        cr.move_to(0, -0.6); cr.line_to(0, 2.8); cr.stroke()

    elif kind == "avatar":
        cr.arc(0, -2.2, 2.8, 0, 2 * math.pi); cr.fill()
        cr.new_sub_path()
        cr.arc(0, 7.0, 6.2, math.pi, 2 * math.pi); cr.fill()

    cr.restore()
    return surf


# ------------------------------------------------------------------------------
# Main Settings Window
# ------------------------------------------------------------------------------
class SettingsWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Blink")
        self.set_default_size(940, 660)
        self.set_size_request(800, 560)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Enable true RGBA composited glass transparency
        screen = Gdk.Screen.get_default()
        rgba_visual = screen.get_rgba_visual()
        if rgba_visual and screen.is_composited():
            self.set_visual(rgba_visual)

        self._apply_css()
        self.get_style_context().add_class("macos-window")

        # Native HeaderBar with Blink Logo
        self.headerbar = Gtk.HeaderBar()
        self.headerbar.set_show_close_button(True)
        self.headerbar.props.title = "Face ID & Password"

        logo_pb = get_blink_logo_pixbuf(22)
        if logo_pb:
            img_hdr_logo = Gtk.Image.new_from_pixbuf(logo_pb)
            img_hdr_logo.set_margin_start(4)
            img_hdr_logo.set_margin_end(2)
            self.headerbar.pack_start(img_hdr_logo)

        self.set_titlebar(self.headerbar)

        # Set Window Manager App Icon
        icon_path = RESOURCES_DIR / "final.png"
        if icon_path.is_file():
            try:
                self.set_icon_from_file(str(icon_path))
            except Exception:
                pass

        # Load System Configuration
        self.settings = BlinkSettings.load()
        self.camera = CameraManager(device_index=self.settings.camera_index)
        self.detector = FaceDetector()
        self.aligner = FaceAligner()
        self.embedder = ArcFaceEmbedder()
        self.liveness = LivenessAnalyzer(mode=self.settings.liveness_mode)

        self._active_page = "face_id"
        self._camera_timer: Optional[int] = None
        self._test_coordinator = None

        # Build Main Split View
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add(main_box)

        # 1. Sidebar (220px)
        self.sidebar_box = self._build_sidebar()
        main_box.pack_start(self.sidebar_box, False, False, 0)

        # 2. Content Stack
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        main_box.pack_start(self.stack, True, True, 0)

        # Build Clean Pages
        self.stack.add_named(self._build_face_id_page(), "face_id")
        self.stack.add_named(self._build_camera_page(), "camera")
        self.stack.add_named(self._build_liveness_page(), "liveness")
        self.stack.add_named(self._build_diagnostics_page(), "diagnostics")
        self.stack.add_named(self._build_about_page(), "about")

        self.show_all()
        self.stack.show_all()

        # Select initial page
        self.stack.set_visible_child_name("face_id")
        self.sidebar_list.select_row(self.nav_rows["face_id"])

        self.connect("destroy", self._on_destroy)

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(MACOS_CSS.encode())
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # --------------------------------------------------------------------------
    # Sidebar with Brand Header
    # --------------------------------------------------------------------------
    def _build_sidebar(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_size_request(220, -1)
        vbox.get_style_context().add_class("sidebar")

        # Top Brand Header with Blink Logo
        brand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand_box.get_style_context().add_class("sidebar-brand-box")

        logo_pb = get_blink_logo_pixbuf(32)
        if logo_pb:
            brand_box.pack_start(Gtk.Image.new_from_pixbuf(logo_pb), False, False, 0)

        v_brand_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        lbl_brand = Gtk.Label(label="Blink")
        lbl_brand.set_xalign(0)
        lbl_brand.get_style_context().add_class("sidebar-brand-title")
        lbl_sub = Gtk.Label(label="Face ID System")
        lbl_sub.set_xalign(0)
        lbl_sub.get_style_context().add_class("sidebar-brand-sub")
        v_brand_txt.pack_start(lbl_brand, False, False, 0)
        v_brand_txt.pack_start(lbl_sub, False, False, 0)
        brand_box.pack_start(v_brand_txt, True, True, 0)

        vbox.pack_start(brand_box, False, False, 0)

        # Search Bar
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_box.get_style_context().add_class("sidebar-search-box")
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Search")
        self.search_entry.get_style_context().add_class("search-entry")
        search_box.pack_start(self.search_entry, True, True, 0)
        vbox.pack_start(search_box, False, False, 0)

        # Navigation List
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.get_style_context().add_class("sidebar-list")
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)

        self.nav_rows: Dict[str, Gtk.ListBoxRow] = {}

        self._add_nav_item("face_id", "Face ID & Password", "face_id", "#0A84FF")
        self._add_nav_item("camera", "Camera", "camera", "#BF5AF2")
        self._add_nav_item("liveness", "Anti-Spoofing", "liveness", "#34C759")
        self._add_nav_item("diagnostics", "Diagnostics", "diagnostics", "#5E5CE6")
        self._add_nav_item("about", "About", "about", "#8E8E93")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.sidebar_list)
        vbox.pack_start(scroll, True, True, 0)

        return vbox

    def _add_nav_item(self, page_id: str, label_text: str, icon_kind: str, icon_color: str) -> None:
        row = Gtk.ListBoxRow()
        row._page_id = page_id
        row._title_text = label_text

        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon_surf = create_macos_icon(icon_kind, icon_color, 24)
        h.pack_start(Gtk.Image.new_from_surface(icon_surf), False, False, 0)

        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0)
        h.pack_start(lbl, True, True, 0)

        row.add(h)
        self.sidebar_list.add(row)
        self.nav_rows[page_id] = row

    def _on_sidebar_selected(self, box, row: Optional[Gtk.ListBoxRow]) -> None:
        if not row or not hasattr(row, "_page_id"):
            return
        page_id = row._page_id
        self._active_page = page_id
        self.stack.set_visible_child_name(page_id)
        self.headerbar.props.title = row._title_text

        # Camera management
        if page_id in ("camera", "diagnostics"):
            if not self.camera._running:
                self.camera.start(device_index=self.settings.camera_index)
            if self._camera_timer is None:
                self._camera_timer = GLib.timeout_add(33, self._update_camera_feeds)
        else:
            if self._camera_timer is not None:
                GLib.source_remove(self._camera_timer)
                self._camera_timer = None
            self.camera.stop()

    # --------------------------------------------------------------------------
    # Inset Card Helpers with Constrained Width (Anti-Stretch)
    # --------------------------------------------------------------------------
    def _create_page_wrapper(self, title: str) -> Tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroll = Gtk.ScrolledWindow()
        scroll.get_style_context().add_class("content-scroll")
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.get_style_context().add_class("content-box")
        vbox.set_halign(Gtk.Align.START)
        vbox.set_hexpand(False)
        vbox.set_size_request(660, -1)

        lbl_t = Gtk.Label(label=title)
        lbl_t.set_xalign(0)
        lbl_t.get_style_context().add_class("page-title")
        vbox.pack_start(lbl_t, False, False, 0)

        outer_box.pack_start(vbox, True, True, 0)
        scroll.add(outer_box)
        return scroll, vbox

    def _create_group_heading(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("group-title")
        return lbl

    def _create_group_footnote(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        lbl.set_line_wrap(True)
        lbl.get_style_context().add_class("group-footnote")
        return lbl

    def _create_inset_group(self) -> Gtk.Box:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        group.get_style_context().add_class("inset-group")
        return group

    def _add_inset_row(self, group: Gtk.Box, title: str, subtitle: Optional[str] = None, control: Optional[Gtk.Widget] = None, is_last: bool = False) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.get_style_context().add_class("inset-row")

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl_t = Gtk.Label(label=title)
        lbl_t.set_xalign(0)
        lbl_t.get_style_context().add_class("row-label")
        text_box.pack_start(lbl_t, False, False, 0)

        if subtitle:
            lbl_sub = Gtk.Label(label=subtitle)
            lbl_sub.set_xalign(0)
            lbl_sub.get_style_context().add_class("row-desc")
            text_box.pack_start(lbl_sub, False, False, 0)

        row.pack_start(text_box, True, True, 0)

        if control:
            control.set_valign(Gtk.Align.CENTER)
            control.set_halign(Gtk.Align.END)
            row.pack_end(control, False, False, 0)

        group.pack_start(row, False, False, 0)

        if not is_last:
            divider = Gtk.Box()
            divider.get_style_context().add_class("inset-divider")
            group.pack_start(divider, False, False, 0)

    # --------------------------------------------------------------------------
    # Page 1: Face ID & Password (Unified Apple Settings Experience)
    # --------------------------------------------------------------------------
    def _build_face_id_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Face ID & Password")

        # 1. Glass Hero Banner with Blink Logo & Live Status Pill
        hero_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hero_box.get_style_context().add_class("glass-hero")

        logo_pb = get_blink_logo_pixbuf(48)
        if logo_pb:
            hero_box.pack_start(Gtk.Image.new_from_pixbuf(logo_pb), False, False, 0)

        v_hero_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl_hero_t = Gtk.Label(label="Blink Face ID")
        lbl_hero_t.set_xalign(0)
        lbl_hero_t.get_style_context().add_class("hero-title")
        lbl_hero_sub = Gtk.Label(label="Fast, on-device biometric unlock protected by neural vision")
        lbl_hero_sub.set_xalign(0)
        lbl_hero_sub.get_style_context().add_class("hero-sub")
        v_hero_txt.pack_start(lbl_hero_t, False, False, 0)
        v_hero_txt.pack_start(lbl_hero_sub, False, False, 0)
        hero_box.pack_start(v_hero_txt, True, True, 0)

        # Status Pill Badge
        has_faces = bool(SecureFaceStore.load_identities())
        is_active = self.settings.is_face_unlock_enabled and has_faces
        self.lbl_hero_status = Gtk.Label(label="● Active" if is_active else "● Disabled")
        self.lbl_hero_status.get_style_context().add_class("pill-badge-active" if is_active else "pill-badge-disabled")
        self.lbl_hero_status.set_valign(Gtk.Align.CENTER)
        hero_box.pack_end(self.lbl_hero_status, False, False, 0)

        page.pack_start(hero_box, False, False, 0)

        # 2. Enrolled Biometric Profiles
        page.pack_start(self._create_group_heading("Face ID"), False, False, 0)

        self.group_profiles = self._create_inset_group()
        page.pack_start(self.group_profiles, False, False, 0)
        self._refresh_profiles_row()

        # 3. Use Face ID for:
        page.pack_start(self._create_group_heading("Use Face ID for"), False, False, 0)

        group_use = self._create_inset_group()

        self.sw_master = Gtk.Switch()
        self.sw_master.set_active(is_active)
        self.sw_master.connect("notify::active", self._on_toggle_face_unlock)
        self._add_inset_row(group_use, "Unlocking your computer", "Enables seamless facial recognition on display wake and lockscreen", self.sw_master)

        sw_wake = Gtk.Switch()
        sw_wake.set_active("on_wake" in self.settings.unlock_triggers)
        sw_wake.connect("notify::active", lambda w, p: self._on_trigger_switch("on_wake", w.get_active()))
        self._add_inset_row(group_use, "Unlock on display wake", "Begins scanning when display turns on or laptop lid opens", sw_wake)

        sw_lock = Gtk.Switch()
        sw_lock.set_active("on_lock" in self.settings.unlock_triggers)
        sw_lock.connect("notify::active", lambda w, p: self._on_trigger_switch("on_lock", w.get_active()))
        self._add_inset_row(group_use, "Unlock on screen lock", "Scans face immediately upon locking", sw_lock)

        sw_space = Gtk.Switch()
        sw_space.set_active("on_space" in self.settings.unlock_triggers)
        sw_space.connect("notify::active", lambda w, p: self._on_trigger_switch("on_space", w.get_active()))
        self._add_inset_row(group_use, "Unlock with spacebar / keypress", "Press spacebar or any key at lock screen to begin recognition", sw_space)

        sw_retry = Gtk.Switch()
        sw_retry.set_active(self.settings.auto_retry_once)
        sw_retry.connect("notify::active", lambda w, p: self._update_setting("auto_retry_once", w.get_active()))
        self._add_inset_row(group_use, "Auto-retry scan once", "Retries automatically if face was not recognized on first attempt", sw_retry)

        sw_anim = Gtk.Switch()
        sw_anim.set_active(self.settings.show_unlock_animation)
        sw_anim.connect("notify::active", lambda w, p: self._update_setting("show_unlock_animation", w.get_active()))
        self._add_inset_row(group_use, "Floating Face ID indicator", "Displays compact Face ID animation card at top of screen", sw_anim, is_last=True)

        page.pack_start(group_use, False, False, 0)
        page.pack_start(self._create_group_footnote("Blink uses neural facial recognition to unlock your system when you wake the computer or lock the screen."), False, False, 0)

        # 4. Recognition Tuning
        page.pack_start(self._create_group_heading("Recognition options"), False, False, 0)

        group_tune = self._create_inset_group()

        # Timeout
        lbl_time_val = Gtk.Label(label=f"{self.settings.face_detection_seconds}s")
        lbl_time_val.get_style_context().add_class("row-desc")
        scale_time = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 2, 10, 1)
        scale_time.set_value(self.settings.face_detection_seconds)
        scale_time.set_size_request(140, -1)
        scale_time.connect("value-changed", lambda w: self._on_timeout_slider(w, lbl_time_val))
        h_time = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h_time.pack_start(scale_time, False, False, 0)
        h_time.pack_start(lbl_time_val, False, False, 0)
        self._add_inset_row(group_tune, "Scanning duration", "Time window before falling back to manual password", h_time)

        # Threshold
        lbl_thresh_val = Gtk.Label(label=f"{self.settings.match_threshold:.2f}")
        lbl_thresh_val.get_style_context().add_class("row-desc")
        scale_thresh = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.40, 0.85, 0.01)
        scale_thresh.set_value(self.settings.match_threshold)
        scale_thresh.set_size_request(140, -1)
        scale_thresh.connect("value-changed", lambda w: self._on_thresh_slider(w, lbl_thresh_val))
        h_thresh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h_thresh.pack_start(scale_thresh, False, False, 0)
        h_thresh.pack_start(lbl_thresh_val, False, False, 0)
        self._add_inset_row(group_tune, "Similarity threshold", "Higher values require closer match (0.60 recommended)", h_thresh)

        # Test Recognition Button
        btn_test = Gtk.Button(label="Test Face ID...")
        btn_test.get_style_context().add_class("btn-macos")
        btn_test.connect("clicked", self._on_test_scan_clicked)
        self._add_inset_row(group_tune, "Test recognition", "Preview the Face ID card and test scanning without locking", btn_test, is_last=True)

        page.pack_start(group_tune, False, False, 0)

        # 5. Password Section
        page.pack_start(self._create_group_heading("Password"), False, False, 0)

        group_pwd = self._create_inset_group()

        has_pwd = SecureCredentialManager.has_stored_password()
        current_user = getpass.getuser()
        self.lbl_pwd_status = Gtk.Label(label=f"✓ {current_user} (Password Set)" if has_pwd else "Not configured")
        self.lbl_pwd_status.get_style_context().add_class("row-desc")

        btn_change_pwd = Gtk.Button(label="Change..." if has_pwd else "Set Up...")
        btn_change_pwd.get_style_context().add_class("btn-macos")
        btn_change_pwd.connect("clicked", self._on_open_password_dialog)

        h_pwd_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h_pwd_ctrl.pack_start(self.lbl_pwd_status, False, False, 0)
        h_pwd_ctrl.pack_start(btn_change_pwd, False, False, 0)
        self.btn_change_pwd = btn_change_pwd
        self._add_inset_row(group_pwd, "Account password", "Stored securely with AES-256-GCM encryption", h_pwd_ctrl)

        # Auto-lock
        combo_lock = Gtk.ComboBoxText()
        combo_lock.append("5", "5 minutes")
        combo_lock.append("15", "15 minutes")
        combo_lock.append("30", "30 minutes")
        combo_lock.append("60", "1 hour")
        sys_delay = get_system_idle_delay()
        active_id = str(sys_delay) if str(sys_delay) in ["5", "15", "30", "60"] else "15"
        combo_lock.set_active_id(active_id)
        combo_lock.connect("changed", self._on_combo_idle_changed)
        self._add_inset_row(group_pwd, "Require password after screen lock", "Synchronized directly with Cinnamon & GNOME screen saver", combo_lock, is_last=True)

        page.pack_start(group_pwd, False, False, 0)
        page.pack_start(self._create_group_footnote("Your password is encrypted using hardware-linked AES-256-GCM and injected to unlock your screen saver upon face confirmation."), False, False, 0)

        return scroll

    def _refresh_profiles_row(self) -> None:
        for child in self.group_profiles.get_children():
            self.group_profiles.remove(child)

        identities = SecureFaceStore.load_identities()
        if not identities:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.get_style_context().add_class("inset-row")

            lbl = Gtk.Label(label="No faces enrolled")
            lbl.set_xalign(0)
            lbl.get_style_context().add_class("row-desc")
            row.pack_start(lbl, True, True, 0)

            btn_add = Gtk.Button(label="Add Face...")
            btn_add.get_style_context().add_class("btn-macos-primary")
            btn_add.set_valign(Gtk.Align.CENTER)
            btn_add.connect("clicked", self._start_enrollment_wizard)
            row.pack_end(btn_add, False, False, 0)
            self.group_profiles.pack_start(row, False, False, 0)
        else:
            for idx, ident in enumerate(identities):
                is_last = (idx == len(identities) - 1)
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row.get_style_context().add_class("inset-row")

                icon_surf = create_macos_icon("avatar", "#48484A", 28)
                row.pack_start(Gtk.Image.new_from_surface(icon_surf), False, False, 0)

                v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                name_lbl = Gtk.Label(label=ident.get("name", "User"))
                name_lbl.set_xalign(0)
                name_lbl.get_style_context().add_class("row-label")

                poses_count = len(ident.get("samples", []))
                sub_lbl = Gtk.Label(label=f"{poses_count} biometric poses enrolled")
                sub_lbl.set_xalign(0)
                sub_lbl.get_style_context().add_class("row-desc")
                v.pack_start(name_lbl, False, False, 0)
                v.pack_start(sub_lbl, False, False, 0)
                row.pack_start(v, True, True, 0)

                # Delete Button
                btn_del = Gtk.Button(label="Delete")
                btn_del.get_style_context().add_class("btn-macos-danger")
                btn_del.set_valign(Gtk.Align.CENTER)
                btn_del.connect("clicked", self._on_delete_identity, ident["id"])
                row.pack_end(btn_del, False, False, 0)

                self.group_profiles.pack_start(row, False, False, 0)

                if not is_last:
                    div = Gtk.Box()
                    div.get_style_context().add_class("inset-divider")
                    self.group_profiles.pack_start(div, False, False, 0)

            # Footer row inside card to add another face
            div = Gtk.Box()
            div.get_style_context().add_class("inset-divider")
            self.group_profiles.pack_start(div, False, False, 0)

            add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            add_row.get_style_context().add_class("inset-row")
            lbl_more = Gtk.Label(label="Enroll an additional face profile")
            lbl_more.set_xalign(0)
            lbl_more.get_style_context().add_class("row-desc")
            add_row.pack_start(lbl_more, True, True, 0)

            btn_add = Gtk.Button(label="Add Face...")
            btn_add.get_style_context().add_class("btn-macos-primary")
            btn_add.set_valign(Gtk.Align.CENTER)
            btn_add.connect("clicked", self._start_enrollment_wizard)
            add_row.pack_end(btn_add, False, False, 0)
            self.group_profiles.pack_start(add_row, False, False, 0)

        self.group_profiles.show_all()

    # --------------------------------------------------------------------------
    # Page 2: Camera (Whole Sensor Form & Optical Reticle)
    # --------------------------------------------------------------------------
    def _build_camera_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Camera")

        page.pack_start(self._create_group_heading("Camera device"), False, False, 0)

        group_cam = self._create_inset_group()

        self.combo_cam = Gtk.ComboBoxText()
        devices = CameraManager.list_devices()
        for idx in devices:
            self.combo_cam.append(str(idx), f"/dev/video{idx}")
        self.combo_cam.set_active_id(str(self.settings.camera_index))
        self.combo_cam.connect("changed", self._on_camera_device_changed)
        self._add_inset_row(group_cam, "Camera input", "Selected sensor device for face detection", self.combo_cam)

        lbl_fall = Gtk.Label(label="Enabled")
        lbl_fall.get_style_context().add_class("row-desc")
        self._add_inset_row(group_cam, "Device 0 fallback", "Automatically switches to /dev/video0 if selected device fails", lbl_fall, is_last=True)
        page.pack_start(group_cam, False, False, 0)

        # Whole Sensor Viewfinder Card
        page.pack_start(self._create_group_heading("Optical sensor feed (whole sensor form)"), False, False, 0)

        self.viewfinder_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.viewfinder_container.get_style_context().add_class("viewfinder-card")

        # Top HUD Bar
        hud_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hud_bar.get_style_context().add_class("hud-bar")

        self.lbl_cam_live_state = Gtk.Label(label="● LIVE FEED")
        self.lbl_cam_live_state.get_style_context().add_class("pill-badge-active")
        hud_bar.pack_start(self.lbl_cam_live_state, False, False, 0)

        self.lbl_cam_sensor_spec = Gtk.Label(label="Initializing Sensor…")
        self.lbl_cam_sensor_spec.get_style_context().add_class("pill-badge-sensor")
        hud_bar.pack_start(self.lbl_cam_sensor_spec, False, False, 0)

        self.lbl_cam_device_spec = Gtk.Label(label=f"/dev/video{self.settings.camera_index} • 30 FPS")
        self.lbl_cam_device_spec.get_style_context().add_class("row-desc")
        hud_bar.pack_end(self.lbl_cam_device_spec, False, False, 0)

        self.viewfinder_container.pack_start(hud_bar, False, False, 0)

        # Center Preview Image (Centered, aspect-ratio preserved!)
        h_align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        h_align_box.set_halign(Gtk.Align.CENTER)
        self.img_cam_preview = Gtk.Image()
        h_align_box.pack_start(self.img_cam_preview, True, True, 0)
        self.viewfinder_container.pack_start(h_align_box, True, True, 0)

        # Bottom HUD Footer
        hud_foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hud_foot.get_style_context().add_class("hud-footer")

        lbl_foot_fov = Gtk.Label(label="100% Native Sensor Area (Uncropped)")
        lbl_foot_fov.get_style_context().add_class("row-desc")
        hud_foot.pack_start(lbl_foot_fov, False, False, 0)

        lbl_foot_lat = Gtk.Label(label="< 20ms Frame Ingestion")
        lbl_foot_lat.get_style_context().add_class("row-desc")
        hud_foot.pack_end(lbl_foot_lat, False, False, 0)

        self.viewfinder_container.pack_start(hud_foot, False, False, 0)

        page.pack_start(self.viewfinder_container, False, False, 0)
        page.pack_start(self._create_group_footnote("Viewfinder shows the entire uncropped physical camera sensor preserving exact optical geometry."), False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 3: Anti-Spoofing & Liveness
    # --------------------------------------------------------------------------
    def _build_liveness_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Anti-Spoofing")

        page.pack_start(self._create_group_heading("Liveness verification"), False, False, 0)

        group_live = self._create_inset_group()

        sw_live = Gtk.Switch()
        sw_live.set_active(self.settings.liveness_checks_enabled)
        sw_live.connect("notify::active", lambda w, p: self._update_setting("liveness_checks_enabled", w.get_active()))
        self._add_inset_row(group_live, "Check for liveness", "Protects against 2D photos, video replays, and digital screens", sw_live)

        # Mode Selection
        self.rb_light = Gtk.RadioButton.new_with_label(None, "Standard")
        self.rb_heavy = Gtk.RadioButton.new_with_label_from_widget(self.rb_light, "Enhanced")

        if self.settings.liveness_mode == "heavy":
            self.rb_heavy.set_active(True)
        else:
            self.rb_light.set_active(True)

        self.rb_light.connect("toggled", lambda w: self._on_liveness_mode_changed("light") if w.get_active() else None)
        self.rb_heavy.connect("toggled", lambda w: self._on_liveness_mode_changed("heavy") if w.get_active() else None)

        h_modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        h_modes.pack_start(self.rb_light, False, False, 0)
        h_modes.pack_start(self.rb_heavy, False, False, 0)
        self._add_inset_row(group_live, "Security level", "Standard screens reflection glare; Enhanced requires eye blinks", h_modes, is_last=True)

        page.pack_start(group_live, False, False, 0)
        page.pack_start(self._create_group_footnote("Anti-spoofing analyzes specular screen glare, pixel lattices, and facial micro-motion to prevent unauthorized access."), False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 4: Diagnostics & Face Lab (Sensor Form Preserved)
    # --------------------------------------------------------------------------
    def _build_diagnostics_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Diagnostics")

        page.pack_start(self._create_group_heading("Real-time vision telemetry"), False, False, 0)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        # Left: Live Viewfinder (Aspect-ratio preserved!)
        view_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        view_box.get_style_context().add_class("viewfinder-card")
        self.img_facelab = Gtk.Image()
        view_box.pack_start(self.img_facelab, True, True, 0)
        hbox.pack_start(view_box, False, False, 0)

        # Right: Telemetry Inset Group
        group_diag = self._create_inset_group()

        self.lbl_fl_sim = Gtk.Label(label="--")
        self.lbl_fl_sim.set_xalign(1)
        self.lbl_fl_sim.get_style_context().add_class("row-desc")
        self.prog_fl_sim = Gtk.ProgressBar()
        self.prog_fl_sim.set_size_request(130, -1)
        h_sim = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h_sim.pack_start(self.prog_fl_sim, True, True, 0)
        h_sim.pack_start(self.lbl_fl_sim, False, False, 0)
        self._add_inset_row(group_diag, "Similarity match", None, h_sim)

        self.fl_cue_labels = {}
        self.fl_cue_progs = {}
        for cue in LivenessCue:
            c_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label="--")
            lbl.set_xalign(1)
            lbl.get_style_context().add_class("row-desc")
            prog = Gtk.ProgressBar()
            prog.set_size_request(130, -1)
            c_box.pack_start(prog, True, True, 0)
            c_box.pack_start(lbl, False, False, 0)
            self.fl_cue_labels[cue] = lbl
            self.fl_cue_progs[cue] = prog
            self._add_inset_row(group_diag, cue.title, None, c_box)

        self.lbl_fl_decision = Gtk.Label(label="Ready")
        self.lbl_fl_decision.get_style_context().add_class("row-desc")
        self._add_inset_row(group_diag, "Liveness verdict", None, self.lbl_fl_decision, is_last=True)

        hbox.pack_start(group_diag, True, True, 0)
        page.pack_start(hbox, False, False, 0)

        page.pack_start(self._create_group_footnote("Facial landmarks and real-time biometric embedding scores are computed entirely on-device."), False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 5: About (Official Blink Logo Pedestal)
    # --------------------------------------------------------------------------
    def _build_about_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("About")

        # Top App Pedestal with Official Blink Logo
        top_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top_hero.get_style_context().add_class("glass-hero")
        top_hero.set_margin_bottom(18)

        h_brand_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        logo_pb = get_blink_logo_pixbuf(64)
        if logo_pb:
            h_brand_row.pack_start(Gtk.Image.new_from_pixbuf(logo_pb), False, False, 0)

        v_top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl_app = Gtk.Label(label="Blink")
        lbl_app.set_xalign(0)
        lbl_app.get_style_context().add_class("page-title")

        lbl_ver = Gtk.Label(label="Version 2.1.0 • Apple Human Interface Edition")
        lbl_ver.set_xalign(0)
        lbl_ver.get_style_context().add_class("row-desc")

        lbl_arch = Gtk.Label(label="Linux x86_64 • 100% On-Device Neural Vision")
        lbl_arch.set_xalign(0)
        lbl_arch.get_style_context().add_class("row-desc")

        v_top.pack_start(lbl_app, False, False, 0)
        v_top.pack_start(lbl_ver, False, False, 0)
        v_top.pack_start(lbl_arch, False, False, 0)
        h_brand_row.pack_start(v_top, True, True, 0)

        top_hero.pack_start(h_brand_row, True, True, 0)
        page.pack_start(top_hero, False, False, 0)

        # Links Group
        page.pack_start(self._create_group_heading("Resources"), False, False, 0)

        group_links = self._create_inset_group()

        btn_web = Gtk.Button(label="Visit Website")
        btn_web.get_style_context().add_class("btn-macos")
        btn_web.connect("clicked", lambda b: subprocess.Popen(["xdg-open", "https://blink-web.vercel.app"]))
        self._add_inset_row(group_links, "Website", "Documentation, release notes, and install scripts", btn_web)

        btn_git = Gtk.Button(label="View on GitHub")
        btn_git.get_style_context().add_class("btn-macos")
        btn_git.connect("clicked", lambda b: subprocess.Popen(["xdg-open", "https://github.com/realsamay/blink"]))
        self._add_inset_row(group_links, "Source code", "github.com/realsamay/blink", btn_git, is_last=True)

        page.pack_start(group_links, False, False, 0)

        # System Info Group
        page.pack_start(self._create_group_heading("Environment"), False, False, 0)

        group_sys = self._create_inset_group()

        lbl_os = Gtk.Label(label=f"Linux {platform.release()}")
        lbl_os.get_style_context().add_class("row-desc")
        self._add_inset_row(group_sys, "Operating system", None, lbl_os)

        inj_avail = KeystrokeInjector.is_available()
        lbl_inj = Gtk.Label(label="Available (/dev/uinput)" if inj_avail else "Restricted")
        lbl_inj.get_style_context().add_class("row-desc")
        self._add_inset_row(group_sys, "Keystroke injector", None, lbl_inj)

        is_svc = (subprocess.run(["systemctl", "--user", "is-active", "blink.service"], capture_output=True, text=True).stdout.strip() == "active")
        lbl_svc = Gtk.Label(label="Active (Running)" if is_svc else "Stopped")
        lbl_svc.get_style_context().add_class("row-desc")
        self._add_inset_row(group_sys, "Background service", None, lbl_svc, is_last=True)

        page.pack_start(group_sys, False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Settings Callbacks & Background Synchronization
    # --------------------------------------------------------------------------
    def _update_setting(self, key: str, value: Any) -> None:
        setattr(self.settings, key, value)
        self.settings.save()
        self._sync_daemon_service()

    def _sync_daemon_service(self) -> None:
        """Restarts the background user service if active so settings apply immediately."""
        def _bg():
            try:
                proc = subprocess.run(["systemctl", "--user", "is-active", "blink.service"], capture_output=True, text=True)
                if proc.stdout.strip() == "active":
                    subprocess.run(["systemctl", "--user", "restart", "blink.service"], capture_output=True)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True, name="BlinkSyncService").start()

    def _on_toggle_face_unlock(self, switch, gparam):
        has_faces = bool(SecureFaceStore.load_identities())
        active = switch.get_active()
        if active and not has_faces:
            switch.set_active(False)
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="No Faces Enrolled"
            )
            dialog.format_secondary_text("Please enroll your face in 'Face ID' before enabling biometric unlock.")
            dialog.run()
            dialog.destroy()
            return

        self._update_setting("is_face_unlock_enabled", active)
        if hasattr(self, "lbl_hero_status"):
            self.lbl_hero_status.set_text("● Active" if active else "● Disabled")
            ctx = self.lbl_hero_status.get_style_context()
            if active:
                ctx.remove_class("pill-badge-disabled")
                ctx.add_class("pill-badge-active")
            else:
                ctx.remove_class("pill-badge-active")
                ctx.add_class("pill-badge-disabled")

    def _on_trigger_switch(self, trigger_id: str, is_active: bool) -> None:
        triggers = set(self.settings.unlock_triggers)
        if is_active:
            triggers.add(trigger_id)
        else:
            triggers.discard(trigger_id)
        self._update_setting("unlock_triggers", list(triggers))

    def _on_timeout_slider(self, scale: Gtk.Scale, label: Gtk.Label) -> None:
        val = int(scale.get_value())
        label.set_text(f"{val}s")
        self._update_setting("face_detection_seconds", val)

    def _on_thresh_slider(self, scale: Gtk.Scale, label: Gtk.Label) -> None:
        val = float(scale.get_value())
        label.set_text(f"{val:.2f}")
        self._update_setting("match_threshold", val)

    def _on_combo_idle_changed(self, combo: Gtk.ComboBoxText) -> None:
        active_id = combo.get_active_id()
        if active_id:
            mins = int(active_id)
            self._update_setting("auto_lock_idle_minutes", mins)
            set_system_idle_delay(mins)

    def _on_camera_device_changed(self, combo: Gtk.ComboBoxText) -> None:
        active_id = combo.get_active_id()
        if active_id:
            idx = int(active_id)
            self._update_setting("camera_index", idx)
            self.camera.start(device_index=idx)
            if hasattr(self, "lbl_cam_device_spec"):
                self.lbl_cam_device_spec.set_text(f"/dev/video{idx} • 30 FPS")

    def _on_liveness_mode_changed(self, mode: str) -> None:
        self._update_setting("liveness_mode", mode)
        self.liveness.mode = mode

    def _on_open_password_dialog(self, button) -> None:
        """Modal dialog to set or change desktop password with real-time PAM validation."""
        current_user = getpass.getuser()
        dialog = Gtk.Dialog(
            title="Account Password",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.set_default_size(380, 220)
        dialog.get_style_context().add_class("macos-window")

        area = dialog.get_content_area()
        area.set_spacing(12)
        area.set_border_width(20)

        lbl_info = Gtk.Label(label=f"Enter Linux password for user '{current_user}':")
        lbl_info.set_xalign(0)
        lbl_info.get_style_context().add_class("row-label")
        area.pack_start(lbl_info, False, False, 0)

        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_placeholder_text("Account Password")
        area.pack_start(entry, False, False, 0)

        lbl_feedback = Gtk.Label(label="")
        lbl_feedback.set_xalign(0)
        lbl_feedback.get_style_context().add_class("row-desc")
        area.pack_start(lbl_feedback, False, False, 0)

        has_pwd = SecureCredentialManager.has_stored_password()
        btn_remove = Gtk.Button(label="Remove Stored Password")
        btn_remove.get_style_context().add_class("btn-macos-danger")
        btn_remove.set_visible(has_pwd)

        def remove_action(b):
            SecureCredentialManager.remove_password()
            self.lbl_pwd_status.set_text("Not configured")
            self.btn_change_pwd.set_label("Set Up...")
            dialog.destroy()

        btn_remove.connect("clicked", remove_action)
        area.pack_start(btn_remove, False, False, 0)

        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        btn_save = dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        dialog.show_all()
        if not has_pwd:
            btn_remove.hide()

        while True:
            resp = dialog.run()
            if resp != Gtk.ResponseType.OK:
                break

            pwd = entry.get_text().strip()
            if not pwd:
                lbl_feedback.set_text("Password cannot be blank.")
                continue

            # Real-time password verification against system account
            if verify_linux_password(current_user, pwd):
                SecureCredentialManager.save_password(pwd)
                self.lbl_pwd_status.set_text(f"✓ {current_user} (Password Set)")
                self.btn_change_pwd.set_label("Change...")
                break
            else:
                lbl_feedback.set_markup("<span color='#FF6B62'>Incorrect password for Linux user account.</span>")

        dialog.destroy()

    def _on_delete_identity(self, button, ident_id: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Delete Face Profile?"
        )
        dialog.format_secondary_text("Are you sure you want to remove this enrolled face? You will need to re-enroll to use Face ID with this face.")
        resp = dialog.run()
        dialog.destroy()

        if resp == Gtk.ResponseType.YES:
            identities = SecureFaceStore.load_identities()
            identities = [ident for ident in identities if ident["id"] != ident_id]
            SecureFaceStore.save_identities(identities)
            self._refresh_profiles_row()

            if not identities:
                self.sw_master.set_active(False)
                self._update_setting("is_face_unlock_enabled", False)
                if hasattr(self, "lbl_hero_status"):
                    self.lbl_hero_status.set_text("● Disabled")
                    ctx = self.lbl_hero_status.get_style_context()
                    ctx.remove_class("pill-badge-active")
                    ctx.add_class("pill-badge-disabled")

    def _on_test_scan_clicked(self, button) -> None:
        """Pops out the Face ID overlay directly on desktop to test scanning flow."""
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

        from blink.core.coordinator import FaceUnlockCoordinator
        if not hasattr(self, "_test_coordinator") or self._test_coordinator is None:
            self._test_coordinator = FaceUnlockCoordinator()
        self._test_coordinator.overlay._is_desktop_preview = True
        self._test_coordinator.test_scan()

    def _start_enrollment_wizard(self, button) -> None:
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

        dialog = EnrollmentDialog(parent=self, camera=self.camera)
        dialog.run()
        dialog.destroy()

        self._refresh_profiles_row()

        identities = SecureFaceStore.load_identities()
        if identities and not self.settings.is_face_unlock_enabled:
            self.sw_master.set_active(True)
            self._update_setting("is_face_unlock_enabled", True)
            if hasattr(self, "lbl_hero_status"):
                self.lbl_hero_status.set_text("● Active")
                ctx = self.lbl_hero_status.get_style_context()
                ctx.remove_class("pill-badge-disabled")
                ctx.add_class("pill-badge-active")

        if self._active_page in ("camera", "diagnostics"):
            self.camera.start(device_index=self.settings.camera_index)
            self._camera_timer = GLib.timeout_add(33, self._update_camera_feeds)

    # --------------------------------------------------------------------------
    # Whole Sensor Aspect-Ratio Preserving Feeds
    # --------------------------------------------------------------------------
    def _update_camera_feeds(self) -> bool:
        latest = self.camera.get_latest_frame()
        if latest is None:
            return True

        _, frame = latest
        h, w = frame.shape[:2]

        if self._active_page == "camera":
            # Update Live Sensor Metadata HUD
            aspect = w / max(1, h)
            aspect_lbl = "4:3 Full Sensor" if abs(aspect - 4/3) < 0.08 else ("16:9 Widescreen" if abs(aspect - 16/9) < 0.08 else f"{aspect:.2f}:1 Sensor")
            self.lbl_cam_sensor_spec.set_text(f"{w} × {h} • {aspect_lbl}")

            # Scale to max 520x390 preserving EXACT native sensor aspect ratio!
            pixbuf = self._cv2_to_pixbuf_sensor(frame, max_w=520, max_h=380, add_reticle=True)
            if pixbuf:
                self.img_cam_preview.set_from_pixbuf(pixbuf)

        elif self._active_page == "diagnostics":
            faces = self.detector.detect_faces(frame, compute_106_landmarks=True)
            dominant = FaceDetector.select_dominant_face(faces, frame_width=w)
            disp = frame.copy()

            if dominant:
                x, y, fw, fh = dominant.bbox
                cv2.rectangle(disp, (x, y), (x + fw, y + fh), (0, 255, 120), 1)
                for pt in dominant.landmarks_5pt:
                    cv2.circle(disp, (int(pt[0]), int(pt[1])), 3, (0, 255, 255), -1)

                if dominant.landmarks_106pt is not None:
                    for pt in dominant.landmarks_106pt:
                        cv2.circle(disp, (int(pt[0]), int(pt[1])), 1, (255, 120, 0), -1)

                aligned = self.aligner.align(dominant, frame)
                if aligned is not None:
                    emb = self.embedder.compute_embedding(aligned)
                    identities = SecureFaceStore.load_identities()
                    scored = self.embedder.score_identities(emb, identities)
                    if scored:
                        top, sim, _ = scored[0]
                        self.lbl_fl_sim.set_text(f"{sim:.2f} ({top['name']})")
                        self.prog_fl_sim.set_fraction(max(0.0, min(1.0, sim)))
                    else:
                        self.lbl_fl_sim.set_text("No faces")
                        self.prog_fl_sim.set_fraction(0.0)

                    # Update Liveness Cues
                    x1, y1 = max(0, x), max(0, y)
                    x2, y2 = min(w, x + fw), min(h, y + fh)
                    face_crop = frame[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None
                    decision = self.liveness.observe(frame, dominant, face_crop)

                    for cue, state in self.liveness.cue_states.items():
                        thresh = self.liveness.tuning.frames_for(cue)
                        self.fl_cue_labels[cue].set_text(f"{state.reading.level:.2f}")
                        self.fl_cue_progs[cue].set_fraction(state.progress(thresh))

                    self.lbl_fl_decision.set_text(decision.kind.value.capitalize())

            # Diagnostics feed: preserve sensor proportions without stretching
            pixbuf = self._cv2_to_pixbuf_sensor(disp, max_w=380, max_h=285, add_reticle=False)
            if pixbuf:
                self.img_facelab.set_from_pixbuf(pixbuf)

        return True

    def _cv2_to_pixbuf_sensor(self, img_bgr: np.ndarray, max_w: int, max_h: int, add_reticle: bool = False) -> Optional[GdkPixbuf.Pixbuf]:
        """Scales camera frame into whole sensor form strictly preserving native aspect ratio."""
        try:
            h, w = img_bgr.shape[:2]
            scale = min(max_w / w, max_h / h)
            target_w = max(1, int(w * scale))
            target_h = max(1, int(h * scale))

            resized = cv2.resize(img_bgr, (target_w, target_h), interpolation=cv2.INTER_AREA)

            if add_reticle:
                # Optical viewfinder corner brackets (Apple sensor reticle)
                b_len = 14
                color = (255, 255, 255)
                thick = 1
                margin = 8
                # Top-left
                cv2.line(resized, (margin, margin + b_len), (margin, margin), color, thick)
                cv2.line(resized, (margin, margin), (margin + b_len, margin), color, thick)
                # Top-right
                cv2.line(resized, (target_w - margin - b_len, margin), (target_w - margin, margin), color, thick)
                cv2.line(resized, (target_w - margin, margin), (target_w - margin, margin + b_len), color, thick)
                # Bottom-left
                cv2.line(resized, (margin, target_h - margin - b_len), (margin, target_h - margin), color, thick)
                cv2.line(resized, (margin, target_h - margin), (margin + b_len, target_h - margin), color, thick)
                # Bottom-right
                cv2.line(resized, (target_w - margin - b_len, target_h - margin), (target_w - margin, target_h - margin), color, thick)
                cv2.line(resized, (target_w - margin, target_h - margin - b_len), (target_w - margin, target_h - margin), color, thick)

            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            rh, rw = rgb.shape[:2]
            return GdkPixbuf.Pixbuf.new_from_data(
                rgb.tobytes(),
                GdkPixbuf.Colorspace.RGB,
                False,
                8,
                rw,
                rh,
                rw * 3
            )
        except Exception:
            return None

    def _on_destroy(self, window):
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

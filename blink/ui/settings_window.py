"""
Modern Apple-Inspired Settings & Biometric Management Application for Blink on Linux.
Features macOS / iOS Cupertino design language with squircle sidebar navigation,
grouped inset cards, smooth page transitions, real-time telemetry, and Live Face ID preview.
"""

from __future__ import annotations

import math
import os
import platform
import subprocess
import threading
import time
import uuid
from typing import Optional, List, Dict, Any

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
# Apple Cupertino CSS Theme
# ------------------------------------------------------------------------------
APPLE_CSS = """
/* Window Background */
window.apple-window {
    background-color: #0D0D0F;
    color: #F5F5F7;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Segoe UI", Roboto, "Cantarell", "Ubuntu", sans-serif;
    border-radius: 16px;
}

/* HeaderBar */
headerbar {
    background-color: #141417;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    min-height: 44px;
    padding: 0 12px;
}
headerbar .title {
    font-weight: 600;
    font-size: 13.5px;
    color: #FFFFFF;
}
headerbar .subtitle {
    font-size: 11px;
    color: #8E8E93;
}

/* Sidebar */
.sidebar-box {
    background-color: #121215;
    border-right: 1px solid rgba(255, 255, 255, 0.07);
}
.sidebar-header {
    padding: 16px 14px 12px 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
.sidebar-title {
    font-size: 16px;
    font-weight: 700;
    color: #FFFFFF;
}
.sidebar-subtitle {
    font-size: 11px;
    color: #8E8E93;
}
.sidebar-section-header {
    font-size: 10px;
    font-weight: 700;
    color: #636366;
    padding: 14px 14px 4px 14px;
}
list.sidebar-nav {
    background-color: transparent;
}
list.sidebar-nav row {
    background-color: transparent;
    border-radius: 9px;
    padding: 6px 10px;
    margin: 2px 8px;
    color: #A1A1A6;
    border: none;
}
list.sidebar-nav row:hover {
    background-color: rgba(255, 255, 255, 0.06);
    color: #FFFFFF;
}
list.sidebar-nav row:selected {
    background-color: rgba(0, 113, 227, 0.28);
    color: #FFFFFF;
    font-weight: 600;
}

/* Content Area */
.content-scroll {
    background-color: #0D0D0F;
}
.page-container {
    padding: 24px 32px 40px 32px;
}
.page-title {
    font-size: 24px;
    font-weight: 700;
    color: #FFFFFF;
}
.page-subtitle {
    font-size: 13px;
    color: #8E8E93;
    margin-bottom: 20px;
}
.section-title {
    font-size: 11px;
    font-weight: 700;
    color: #8E8E93;
    margin-top: 24px;
    margin-bottom: 8px;
    margin-left: 4px;
}

/* Hero Status Card */
.hero-card {
    background: linear-gradient(135deg, rgba(0, 113, 227, 0.20) 0%, rgba(26, 26, 30, 0.95) 100%);
    border: 1px solid rgba(0, 113, 227, 0.40);
    border-radius: 16px;
    padding: 20px;
}
.hero-title {
    font-size: 18px;
    font-weight: 700;
    color: #FFFFFF;
}
.hero-desc {
    font-size: 12.5px;
    color: #B0B0B5;
}

/* Inset Grouped Cards */
.apple-card {
    background-color: #1A1A1E;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 13px;
    padding: 0;
}
.card-row {
    padding: 12px 16px;
    min-height: 48px;
}
.card-row-divider {
    background-color: rgba(255, 255, 255, 0.06);
    min-height: 1px;
    margin-left: 16px;
}
.row-label {
    font-size: 13.5px;
    font-weight: 500;
    color: #FFFFFF;
}
.row-desc {
    font-size: 11.5px;
    color: #8E8E93;
}

/* Buttons */
.btn-primary {
    background-color: #0071E3;
    color: #FFFFFF;
    border-radius: 9px;
    font-size: 13px;
    font-weight: 600;
    padding: 8px 18px;
    border: none;
}
.btn-primary:hover {
    background-color: #0077ED;
}
.btn-primary:active {
    background-color: #0062C4;
}

.btn-secondary {
    background-color: rgba(255, 255, 255, 0.08);
    color: #FFFFFF;
    border-radius: 9px;
    font-size: 13px;
    font-weight: 500;
    padding: 8px 16px;
    border: 1px solid rgba(255, 255, 255, 0.12);
}
.btn-secondary:hover {
    background-color: rgba(255, 255, 255, 0.13);
}

.btn-danger {
    background-color: rgba(255, 69, 58, 0.12);
    color: #FF453A;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 12px;
    border: 1px solid rgba(255, 69, 58, 0.25);
}
.btn-danger:hover {
    background-color: rgba(255, 69, 58, 0.22);
}

/* Status Pills */
.pill-active {
    background-color: rgba(48, 209, 88, 0.15);
    color: #30D158;
    border: 1px solid rgba(48, 209, 88, 0.30);
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill-inactive {
    background-color: rgba(142, 142, 147, 0.15);
    color: #8E8E93;
    border: 1px solid rgba(142, 142, 147, 0.25);
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill-badge {
    background-color: #0071E3;
    color: #FFFFFF;
    border-radius: 10px;
    padding: 1px 7px;
    font-size: 11px;
    font-weight: 700;
}

/* Switches */
switch {
    border-radius: 16px;
    min-width: 48px;
    min-height: 28px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    background-color: #39393D;
}
switch:checked {
    background-color: #30D158;
    border-color: #30D158;
}
switch slider {
    background-color: #FFFFFF;
    border-radius: 50%;
    min-width: 24px;
    min-height: 24px;
}

/* Sliders */
scale trough {
    background-color: #2C2C2E;
    border-radius: 4px;
    min-height: 6px;
}
scale highlight {
    background-color: #0071E3;
    border-radius: 4px;
}
scale slider {
    background-color: #FFFFFF;
    border-radius: 50%;
    min-width: 18px;
    min-height: 18px;
    margin: -6px 0;
}

/* Entry Fields */
entry {
    background-color: #242428;
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
}
entry:focus {
    border-color: #0071E3;
}

/* Viewfinder Box */
.viewfinder-card {
    background-color: #000000;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 16px;
}

/* Progress Bars */
progressbar trough {
    background-color: #2C2C2E;
    border-radius: 4px;
    min-height: 6px;
}
progressbar progress {
    background-color: #0071E3;
    border-radius: 4px;
}
"""


# ------------------------------------------------------------------------------
# Cairo Vector Icon Generators
# ------------------------------------------------------------------------------
def create_apple_icon_surface(kind: str, bg_hex: str, size: int = 28) -> cairo.ImageSurface:
    """Renders crisp vector Apple Settings squircle icons with pure white glyphs."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    h = bg_hex.lstrip("#")
    r, g, b = [int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)]

    # Squircle background
    rad = size * 0.25
    cr.new_sub_path()
    cr.arc(size - rad, rad, rad, -math.pi / 2, 0)
    cr.arc(size - rad, size - rad, rad, 0, math.pi / 2)
    cr.arc(rad, size - rad, rad, math.pi / 2, math.pi)
    cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgb(r, g, b)
    cr.fill()

    # Glyph in center
    cr.save()
    cr.translate(size / 2.0, size / 2.0)
    scale = size / 28.0
    cr.scale(scale, scale)
    cr.set_source_rgb(1.0, 1.0, 1.0)
    cr.set_line_width(1.5)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    if kind == "face_id":
        # Face ID framing corners
        cr.move_to(-6, -3); cr.line_to(-6, -6); cr.line_to(-3, -6)
        cr.move_to(3, -6); cr.line_to(6, -6); cr.line_to(6, -3)
        cr.move_to(-6, 3); cr.line_to(-6, 6); cr.line_to(-3, 6)
        cr.move_to(3, 6); cr.line_to(6, 6); cr.line_to(6, 3)
        cr.stroke()
        # Eyes
        cr.arc(-2.5, -2, 0.9, 0, 2 * math.pi); cr.fill()
        cr.arc(2.5, -2, 0.9, 0, 2 * math.pi); cr.fill()
        # Nose
        cr.move_to(0, -0.5); cr.line_to(0, 1.5); cr.line_to(1, 1.5); cr.stroke()
        # Smile
        cr.new_sub_path()
        cr.arc(0, 1.5, 3.2, 0.2 * math.pi, 0.8 * math.pi); cr.stroke()

    elif kind == "faces":
        # Head
        cr.arc(0, -3.5, 3.0, 0, 2 * math.pi); cr.fill()
        # Shoulders
        cr.new_sub_path()
        cr.arc(0, 6.0, 6.0, math.pi, 2 * math.pi); cr.fill()

    elif kind == "passcode":
        # Padlock shackle
        cr.new_sub_path()
        cr.arc(0, -2, 3.2, math.pi, 2 * math.pi)
        cr.line_to(3.2, 0); cr.move_to(-3.2, 0); cr.line_to(-3.2, -2); cr.stroke()
        # Body
        cr.rectangle(-4.5, 0, 9, 6.5); cr.fill()
        # Keyhole
        cr.set_source_rgb(r, g, b)
        cr.arc(0, 2.5, 1.0, 0, 2 * math.pi); cr.fill()

    elif kind == "camera":
        # Camera body
        cr.rectangle(-6, -3, 12, 9); cr.fill()
        cr.rectangle(-2.5, -5, 5, 2); cr.fill()
        # Lens
        cr.set_source_rgb(r, g, b)
        cr.arc(0, 1.5, 2.6, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.arc(0, 1.5, 1.2, 0, 2 * math.pi); cr.fill()

    elif kind == "liveness":
        # Eye / Iris
        cr.new_sub_path()
        cr.arc(0, -5, 7.5, 0.3 * math.pi, 0.7 * math.pi); cr.stroke()
        cr.new_sub_path()
        cr.arc(0, 5, 7.5, 1.3 * math.pi, 1.7 * math.pi); cr.stroke()
        cr.arc(0, 0, 2.8, 0, 2 * math.pi); cr.fill()
        cr.set_source_rgb(r, g, b)
        cr.arc(0, 0, 1.2, 0, 2 * math.pi); cr.fill()

    elif kind == "facelab":
        # 3 telemetry bars
        cr.rectangle(-5, 0, 2.2, 6); cr.fill()
        cr.rectangle(-1.1, -4, 2.2, 10); cr.fill()
        cr.rectangle(2.8, -2, 2.2, 8); cr.fill()

    elif kind == "about":
        # Lowercase i
        cr.arc(0, -4, 1.2, 0, 2 * math.pi); cr.fill()
        cr.rectangle(-1.0, -1.5, 2.0, 6.5); cr.fill()
        cr.move_to(-2.0, -1.5); cr.line_to(0, -1.5); cr.stroke()
        cr.move_to(-2.0, 5.0); cr.line_to(2.0, 5.0); cr.stroke()

    cr.restore()
    return surf


# ------------------------------------------------------------------------------
# Main Application Window
# ------------------------------------------------------------------------------
class SettingsWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Blink")
        self.set_default_size(960, 680)
        self.set_size_request(820, 560)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Apply Apple CSS Provider
        self._apply_css()

        self.get_style_context().add_class("apple-window")

        # Custom Cupertino HeaderBar
        self.headerbar = Gtk.HeaderBar()
        self.headerbar.set_show_close_button(True)
        self.headerbar.props.title = "Blink"
        self.headerbar.props.subtitle = "Face ID & Security"
        self.set_titlebar(self.headerbar)

        # Application Icon
        icon_path = os.path.join(os.path.dirname(__file__), "..", "resources", "appicon.png")
        if os.path.exists(icon_path):
            try:
                self.set_icon_from_file(icon_path)
            except Exception:
                pass

        # Load Core Subsystems
        self.settings = BlinkSettings.load()
        self.camera = CameraManager(device_index=self.settings.camera_index)
        self.detector = FaceDetector()
        self.aligner = FaceAligner()
        self.embedder = ArcFaceEmbedder()
        self.liveness = LivenessAnalyzer(mode=self.settings.liveness_mode)

        self._active_page = "overview"
        self._camera_timer: Optional[int] = None
        self._test_coordinator = None

        # Build Main Layout (Sidebar + Stack)
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add(main_box)

        # 1. Left Sidebar
        self.sidebar_box = self._build_sidebar()
        main_box.pack_start(self.sidebar_box, False, False, 0)

        # 2. Right Content Stack
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        main_box.pack_start(self.stack, True, True, 0)

        # Build Pages
        self.stack.add_named(self._build_overview_page(), "overview")
        self.stack.add_named(self._build_faces_page(), "faces")
        self.stack.add_named(self._build_password_page(), "password")
        self.stack.add_named(self._build_camera_page(), "camera")
        self.stack.add_named(self._build_liveness_page(), "liveness")
        self.stack.add_named(self._build_facelab_page(), "facelab")
        self.stack.add_named(self._build_about_page(), "about")

        self.show_all()
        self.stack.show_all()

        # Select initial page
        self.stack.set_visible_child_name("overview")
        self.sidebar_list.select_row(self.nav_rows["overview"])

        self.connect("destroy", self._on_destroy)

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(APPLE_CSS.encode())
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # --------------------------------------------------------------------------
    # Sidebar Construction
    # --------------------------------------------------------------------------
    def _build_sidebar(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_size_request(240, -1)
        vbox.get_style_context().add_class("sidebar-box")

        # Sidebar Header (App Branding)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.get_style_context().add_class("sidebar-header")

        icon_surf = create_apple_icon_surface("face_id", "#0071E3", 32)
        header.pack_start(Gtk.Image.new_from_surface(icon_surf), False, False, 0)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        t_lbl = Gtk.Label(label="Blink")
        t_lbl.set_xalign(0)
        t_lbl.get_style_context().add_class("sidebar-title")
        sub_lbl = Gtk.Label(label="Face ID & Security")
        sub_lbl.set_xalign(0)
        sub_lbl.get_style_context().add_class("sidebar-subtitle")
        title_box.pack_start(t_lbl, False, False, 0)
        title_box.pack_start(sub_lbl, False, False, 0)
        header.pack_start(title_box, True, True, 0)
        vbox.pack_start(header, False, False, 0)

        # Navigation List
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.get_style_context().add_class("sidebar-nav")
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)

        self.nav_rows: Dict[str, Gtk.ListBoxRow] = {}

        # Group 1: SECURITY
        vbox.pack_start(self._create_section_label("SECURITY"), False, False, 0)
        self._add_nav_item("overview", "Overview", "face_id", "#0071E3")
        self._add_nav_item("faces", "Enrolled Faces", "faces", "#30D158", badge_text=str(len(SecureFaceStore.load_identities())))
        self._add_nav_item("password", "Passcode & Vault", "passcode", "#FF9F0A")

        # Group 2: HARDWARE & ENGINE
        vbox.pack_start(self._create_section_label("HARDWARE & VISION"), False, False, 0)
        self._add_nav_item("camera", "Camera & Vision", "camera", "#BF5AF2")
        self._add_nav_item("liveness", "Anti-Spoofing", "liveness", "#64D2FF")
        self._add_nav_item("facelab", "Face Lab Telemetry", "facelab", "#5E5CE6")

        # Group 3: APPLICATION
        vbox.pack_start(self._create_section_label("SYSTEM"), False, False, 0)
        self._add_nav_item("about", "About Blink", "about", "#8E8E93")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.sidebar_list)
        vbox.pack_start(scroll, True, True, 0)

        return vbox

    def _create_section_label(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("sidebar-section-header")
        return lbl

    def _add_nav_item(self, page_id: str, label_text: str, icon_kind: str, icon_color: str, badge_text: Optional[str] = None) -> None:
        row = Gtk.ListBoxRow()
        row._page_id = page_id

        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h.pack_start(Gtk.Image.new_from_surface(create_apple_icon_surface(icon_kind, icon_color, 24)), False, False, 0)

        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0)
        h.pack_start(lbl, True, True, 0)

        if badge_text:
            badge = Gtk.Label(label=badge_text)
            badge.get_style_context().add_class("pill-badge")
            h.pack_end(badge, False, False, 0)
            row._badge_widget = badge

        row.add(h)
        self.sidebar_list.add(row)
        self.nav_rows[page_id] = row

    def _on_sidebar_selected(self, box, row: Optional[Gtk.ListBoxRow]) -> None:
        if not row or not hasattr(row, "_page_id"):
            return
        page_id = row._page_id
        self._active_page = page_id
        self.stack.set_visible_child_name(page_id)

        # Handle camera polling activation/deactivation
        if page_id in ("camera", "facelab"):
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
    # Card & Row UI Helpers
    # --------------------------------------------------------------------------
    def _create_page_wrapper(self, title: str, subtitle: str) -> Tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroll = Gtk.ScrolledWindow()
        scroll.get_style_context().add_class("content-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.get_style_context().add_class("page-container")

        lbl_t = Gtk.Label(label=title)
        lbl_t.set_xalign(0)
        lbl_t.get_style_context().add_class("page-title")
        vbox.pack_start(lbl_t, False, False, 0)

        lbl_sub = Gtk.Label(label=subtitle)
        lbl_sub.set_xalign(0)
        lbl_sub.get_style_context().add_class("page-subtitle")
        vbox.pack_start(lbl_sub, False, False, 0)

        scroll.add(vbox)
        return scroll, vbox

    def _create_apple_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.get_style_context().add_class("apple-card")
        return card

    def _add_card_row(self, card: Gtk.Box, title: str, subtitle: Optional[str] = None, control: Optional[Gtk.Widget] = None, is_last: bool = False) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.get_style_context().add_class("card-row")

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
            row.pack_end(control, False, False, 0)

        card.pack_start(row, False, False, 0)

        if not is_last:
            divider = Gtk.Box()
            divider.get_style_context().add_class("card-row-divider")
            card.pack_start(divider, False, False, 0)

    # --------------------------------------------------------------------------
    # Page 1: Overview & Face ID Dashboard
    # --------------------------------------------------------------------------
    def _build_overview_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Face ID & Security", "Manage facial biometric recognition, triggers, and unlock policies.")

        # Hero Status Card
        self.hero_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.hero_box.get_style_context().add_class("hero-card")

        # Large biometric logo
        hero_icon_surf = create_apple_icon_surface("face_id", "#0071E3", 44)
        self.hero_box.pack_start(Gtk.Image.new_from_surface(hero_icon_surf), False, False, 0)

        hero_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.lbl_hero_title = Gtk.Label(label="Face ID is Active")
        self.lbl_hero_title.set_xalign(0)
        self.lbl_hero_title.get_style_context().add_class("hero-title")

        self.lbl_hero_desc = Gtk.Label(label="Seamless biometric authentication enabled for lock screen & wake.")
        self.lbl_hero_desc.set_xalign(0)
        self.lbl_hero_desc.get_style_context().add_class("hero-desc")

        h_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_hero_pill = Gtk.Label(label="● Armed")
        self.lbl_hero_pill.get_style_context().add_class("pill-active")
        h_status.pack_start(self.lbl_hero_pill, False, False, 0)

        hero_text.pack_start(self.lbl_hero_title, False, False, 0)
        hero_text.pack_start(self.lbl_hero_desc, False, False, 0)
        hero_text.pack_start(h_status, False, False, 0)
        self.hero_box.pack_start(hero_text, True, True, 0)

        # Master Switch
        self.sw_master = Gtk.Switch()
        has_faces = bool(SecureFaceStore.load_identities())
        self.sw_master.set_active(self.settings.is_face_unlock_enabled and has_faces)
        self.sw_master.connect("notify::active", self._on_toggle_face_unlock)
        self.hero_box.pack_end(self.sw_master, False, False, 0)

        page.pack_start(self.hero_box, False, False, 0)

        # Quick Actions Bar
        h_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h_actions.set_margin_top(16)
        h_actions.set_margin_bottom(12)

        btn_test = Gtk.Button(label="▶ Preview Face ID (Test Scan on Desktop)")
        btn_test.get_style_context().add_class("btn-secondary")
        btn_test.connect("clicked", self._on_test_scan_clicked)
        h_actions.pack_start(btn_test, False, False, 0)

        btn_add = Gtk.Button(label="➕ Enroll New Face")
        btn_add.get_style_context().add_class("btn-primary")
        btn_add.connect("clicked", self._start_enrollment_wizard)
        h_actions.pack_end(btn_add, False, False, 0)

        page.pack_start(h_actions, False, False, 0)

        # Group 1: UNLOCK TRIGGERS
        lbl_sec1 = Gtk.Label(label="UNLOCK TRIGGERS")
        lbl_sec1.set_xalign(0)
        lbl_sec1.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec1, False, False, 0)

        card_triggers = self._create_apple_card()

        sw_wake = Gtk.Switch()
        sw_wake.set_active("on_wake" in self.settings.unlock_triggers)
        sw_wake.connect("notify::active", lambda w, p: self._on_trigger_switch("on_wake", w.get_active()))
        self._add_card_row(card_triggers, "Unlock on Wake", "Immediately scan face when display turns on or lid opens", sw_wake)

        sw_lock = Gtk.Switch()
        sw_lock.set_active("on_lock" in self.settings.unlock_triggers)
        sw_lock.connect("notify::active", lambda w, p: self._on_trigger_switch("on_lock", w.get_active()))
        self._add_card_row(card_triggers, "Unlock on Lock", "Triggers scan immediately when system locks", sw_lock)

        sw_space = Gtk.Switch()
        sw_space.set_active("on_space" in self.settings.unlock_triggers)
        sw_space.connect("notify::active", lambda w, p: self._on_trigger_switch("on_space", w.get_active()))
        self._add_card_row(card_triggers, "Unlock on Spacebar", "Press spacebar at lock screen to begin recognition", sw_space, is_last=True)

        page.pack_start(card_triggers, False, False, 0)

        # Group 2: RECOGNITION POLICIES & ANIMATION
        lbl_sec2 = Gtk.Label(label="POLICIES & ANIMATION")
        lbl_sec2.set_xalign(0)
        lbl_sec2.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec2, False, False, 0)

        card_policies = self._create_apple_card()

        sw_retry = Gtk.Switch()
        sw_retry.set_active(self.settings.auto_retry_once)
        sw_retry.connect("notify::active", lambda w, p: self._update_setting("auto_retry_once", w.get_active()))
        self._add_card_row(card_policies, "Auto-retry on Missed Recognition", "Seamlessly retries once if face is momentarily obscured", sw_retry)

        sw_anim = Gtk.Switch()
        sw_anim.set_active(self.settings.show_unlock_animation)
        sw_anim.connect("notify::active", lambda w, p: self._update_setting("show_unlock_animation", w.get_active()))
        self._add_card_row(card_policies, "Dynamic Island Floating Animation", "Displays sleek compact card at camera bezel during scan", sw_anim)

        # Timeout slider
        lbl_timeout_val = Gtk.Label(label=f"{self.settings.face_detection_seconds}s")
        lbl_timeout_val.get_style_context().add_class("pill-active")
        scale_timeout = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 2, 10, 1)
        scale_timeout.set_value(self.settings.face_detection_seconds)
        scale_timeout.set_size_request(160, -1)
        scale_timeout.connect("value-changed", lambda w: self._on_timeout_slider(w, lbl_timeout_val))

        h_timeout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h_timeout.pack_start(scale_timeout, False, False, 0)
        h_timeout.pack_start(lbl_timeout_val, False, False, 0)
        self._add_card_row(card_policies, "Scanning Window Duration", "Time window before falling back to manual password", h_timeout)

        # Similarity threshold slider
        lbl_thresh_val = Gtk.Label(label=f"{self.settings.match_threshold:.2f}")
        lbl_thresh_val.get_style_context().add_class("pill-active")
        scale_thresh = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.40, 0.85, 0.01)
        scale_thresh.set_value(self.settings.match_threshold)
        scale_thresh.set_size_request(160, -1)
        scale_thresh.connect("value-changed", lambda w: self._on_thresh_slider(w, lbl_thresh_val))

        h_thresh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h_thresh.pack_start(scale_thresh, False, False, 0)
        h_thresh.pack_start(lbl_thresh_val, False, False, 0)
        self._add_card_row(card_policies, "Match Similarity Threshold", "Higher values require closer facial match (0.60 recommended)", h_thresh, is_last=True)

        page.pack_start(card_policies, False, False, 0)

        self._update_hero_status()
        return scroll

    def _update_hero_status(self) -> None:
        has_faces = bool(SecureFaceStore.load_identities())
        is_active = self.settings.is_face_unlock_enabled and has_faces

        if is_active:
            self.lbl_hero_title.set_text("Face ID is Active")
            self.lbl_hero_desc.set_text("Seamless biometric authentication enabled for lock screen & wake.")
            self.lbl_hero_pill.set_text("● Armed")
            self.lbl_hero_pill.get_style_context().remove_class("pill-inactive")
            self.lbl_hero_pill.get_style_context().add_class("pill-active")
        else:
            self.lbl_hero_title.set_text("Face ID is Inactive")
            msg = "Enroll your face to enable biometric unlock." if not has_faces else "Toggle switch to re-arm Face ID."
            self.lbl_hero_desc.set_text(msg)
            self.lbl_hero_pill.set_text("○ Disarmed")
            self.lbl_hero_pill.get_style_context().remove_class("pill-active")
            self.lbl_hero_pill.get_style_context().add_class("pill-inactive")

    # --------------------------------------------------------------------------
    # Page 2: Enrolled Biometric Profiles
    # --------------------------------------------------------------------------
    def _build_faces_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Biometric Profiles", "Manage enrolled facial identities recognized by Blink on this device.")

        # Top Action Bar
        h_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h_bar.set_margin_bottom(16)
        lbl_hint = Gtk.Label(label="Biometric templates are encrypted with hardware-bound AES-256-GCM.")
        lbl_hint.set_xalign(0)
        lbl_hint.get_style_context().add_class("row-desc")
        h_bar.pack_start(lbl_hint, True, True, 0)

        btn_add = Gtk.Button(label="➕ Enroll New Face")
        btn_add.get_style_context().add_class("btn-primary")
        btn_add.connect("clicked", self._start_enrollment_wizard)
        h_bar.pack_end(btn_add, False, False, 0)
        page.pack_start(h_bar, False, False, 0)

        # Profiles Card Container
        self.faces_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.pack_start(self.faces_container, False, False, 0)

        self._refresh_faces_view()
        return scroll

    def _refresh_faces_view(self) -> None:
        for child in self.faces_container.get_children():
            self.faces_container.remove(child)

        identities = SecureFaceStore.load_identities()

        # Update sidebar badge
        if "faces" in self.nav_rows and hasattr(self.nav_rows["faces"], "_badge_widget"):
            self.nav_rows["faces"]._badge_widget.set_text(str(len(identities)))

        if not identities:
            # Empty state card
            empty_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            empty_card.get_style_context().add_class("apple-card")
            empty_card.set_margin_top(12)
            empty_card.set_margin_bottom(12)
            empty_card.set_border_width(32)

            icon_surf = create_apple_icon_surface("face_id", "#8E8E93", 48)
            img = Gtk.Image.new_from_surface(icon_surf)
            empty_card.pack_start(img, False, False, 0)

            lbl_empty = Gtk.Label(label="<b>No Biometric Profiles Enrolled</b>")
            lbl_empty.set_use_markup(True)
            empty_card.pack_start(lbl_empty, False, False, 0)

            lbl_hint = Gtk.Label(label="Enroll your face using Blink's fast 5-pose guided capture to enable Face ID.")
            lbl_hint.get_style_context().add_class("row-desc")
            empty_card.pack_start(lbl_hint, False, False, 0)

            btn_enroll_empty = Gtk.Button(label="Set Up Face ID")
            btn_enroll_empty.get_style_context().add_class("btn-primary")
            btn_enroll_empty.connect("clicked", self._start_enrollment_wizard)
            empty_card.pack_start(btn_enroll_empty, False, False, 0)

            self.faces_container.pack_start(empty_card, False, False, 0)
        else:
            card = self._create_apple_card()
            for idx, ident in enumerate(identities):
                is_last = (idx == len(identities) - 1)
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
                row.get_style_context().add_class("card-row")

                # Avatar squircle
                avatar_surf = create_apple_icon_surface("faces", "#30D158", 32)
                row.pack_start(Gtk.Image.new_from_surface(avatar_surf), False, False, 0)

                # Info
                v_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                name_lbl = Gtk.Label(label=ident.get("name", "User"))
                name_lbl.set_xalign(0)
                name_lbl.get_style_context().add_class("row-label")

                poses_count = len(ident.get("samples", []))
                sub_text = f"{poses_count} poses enrolled • Encrypted AES-256 Vault"
                sub_lbl = Gtk.Label(label=sub_text)
                sub_lbl.set_xalign(0)
                sub_lbl.get_style_context().add_class("row-desc")

                v_info.pack_start(name_lbl, False, False, 0)
                v_info.pack_start(sub_lbl, False, False, 0)
                row.pack_start(v_info, True, True, 0)

                # Actions: Switch + Delete Button
                sw = Gtk.Switch()
                sw.set_active(ident.get("is_enabled", True))
                sw.connect("notify::active", self._on_toggle_identity, ident["id"])
                row.pack_end(sw, False, False, 0)

                btn_del = Gtk.Button(label="Delete")
                btn_del.get_style_context().add_class("btn-danger")
                btn_del.connect("clicked", self._on_delete_identity, ident["id"])
                row.pack_end(btn_del, False, False, 0)

                card.pack_start(row, False, False, 0)
                if not is_last:
                    divider = Gtk.Box()
                    divider.get_style_context().add_class("card-row-divider")
                    card.pack_start(divider, False, False, 0)

            self.faces_container.pack_start(card, False, False, 0)

        self.faces_container.show_all()
        self._update_hero_status()

    # --------------------------------------------------------------------------
    # Page 3: Passcode & Vault
    # --------------------------------------------------------------------------
    def _build_password_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Passcode & Vault", "Manage desktop lockscreen credentials and auto-lock security policies.")

        # Info Banner
        lbl_sec = Gtk.Label(label="HOW BLINK UNLOCKS YOUR SYSTEM")
        lbl_sec.set_xalign(0)
        lbl_sec.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec, False, False, 0)

        card_info = self._create_apple_card()
        has_pwd = SecureCredentialManager.has_stored_password()

        self.lbl_vault_status = Gtk.Label(label="● AES-256-GCM Stored" if has_pwd else "○ Not Configured")
        self.lbl_vault_status.get_style_context().add_class("pill-active" if has_pwd else "pill-inactive")

        self._add_card_row(
            card_info,
            "Hardware-Bound Security Vault",
            "Blink encrypts your desktop unlock password using PBKDF2 and AES-256-GCM linked to machine hardware keys.",
            self.lbl_vault_status,
            is_last=True,
        )
        page.pack_start(card_info, False, False, 0)

        # Password Entry Card
        lbl_sec2 = Gtk.Label(label="USER PASSWORD")
        lbl_sec2.set_xalign(0)
        lbl_sec2.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec2, False, False, 0)

        card_entry = self._create_apple_card()

        h_input = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.entry_pwd = Gtk.Entry()
        self.entry_pwd.set_visibility(False)
        self.entry_pwd.set_placeholder_text("Enter Linux user account password")
        self.entry_pwd.set_size_request(260, -1)
        h_input.pack_start(self.entry_pwd, True, True, 0)

        btn_save = Gtk.Button(label="Save Password")
        btn_save.get_style_context().add_class("btn-primary")
        btn_save.connect("clicked", self._on_save_password)
        h_input.pack_start(btn_save, False, False, 0)

        btn_remove = Gtk.Button(label="Remove")
        btn_remove.get_style_context().add_class("btn-danger")
        btn_remove.connect("clicked", self._on_remove_password)
        h_input.pack_start(btn_remove, False, False, 0)

        self._add_card_row(card_entry, "Desktop Password", "Injected via Linux /dev/uinput or PAM after face confirmation", h_input, is_last=True)
        page.pack_start(card_entry, False, False, 0)

        # Auto-Lock Idle Timeout Card
        lbl_sec3 = Gtk.Label(label="SESSION SECURITY")
        lbl_sec3.set_xalign(0)
        lbl_sec3.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec3, False, False, 0)

        card_session = self._create_apple_card()
        combo_lock = Gtk.ComboBoxText()
        combo_lock.append("5", "5 minutes")
        combo_lock.append("15", "15 minutes")
        combo_lock.append("30", "30 minutes")
        combo_lock.append("60", "1 hour")
        combo_lock.set_active_id(str(self.settings.auto_lock_idle_minutes))
        combo_lock.connect("changed", self._on_combo_idle_changed)

        self._add_card_row(card_session, "Auto-lock session after idle", "Enforces re-authentication after specified inactivity period", combo_lock, is_last=True)
        page.pack_start(card_session, False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 4: Camera & Vision
    # --------------------------------------------------------------------------
    def _build_camera_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Camera & Vision", "Configure optical hardware devices and inspect camera viewfinder.")

        lbl_sec1 = Gtk.Label(label="HARDWARE DEVICE")
        lbl_sec1.set_xalign(0)
        lbl_sec1.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec1, False, False, 0)

        card_dev = self._create_apple_card()

        self.combo_cam = Gtk.ComboBoxText()
        devices = CameraManager.list_devices()
        for idx in devices:
            self.combo_cam.append(str(idx), f"/dev/video{idx}")
        self.combo_cam.set_active_id(str(self.settings.camera_index))
        self.combo_cam.connect("changed", self._on_camera_device_changed)

        self._add_card_row(card_dev, "Active Camera", "Select optical camera sensor device for face detection", self.combo_cam)

        lbl_fallback = Gtk.Label(label="Enabled")
        lbl_fallback.get_style_context().add_class("pill-active")
        self._add_card_row(card_dev, "Device 0 Fallback", "Automatically switches to /dev/video0 if selected device fails", lbl_fallback, is_last=True)
        page.pack_start(card_dev, False, False, 0)

        # Camera Viewfinder
        lbl_sec2 = Gtk.Label(label="LIVE VIEWFINDER")
        lbl_sec2.set_xalign(0)
        lbl_sec2.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec2, False, False, 0)

        card_cam_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card_cam_view.get_style_context().add_class("viewfinder-card")

        self.img_cam_preview = Gtk.Image()
        self.img_cam_preview.set_size_request(480, 360)
        card_cam_view.pack_start(self.img_cam_preview, True, True, 0)
        page.pack_start(card_cam_view, False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 5: Anti-Spoofing & Liveness
    # --------------------------------------------------------------------------
    def _build_liveness_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Anti-Spoofing", "Configure multi-cue liveness detection against 2D photos, screens, and masks.")

        lbl_sec1 = Gtk.Label(label="PROTECTION ENGINE")
        lbl_sec1.set_xalign(0)
        lbl_sec1.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec1, False, False, 0)

        card_live = self._create_apple_card()
        sw_live = Gtk.Switch()
        sw_live.set_active(self.settings.liveness_checks_enabled)
        sw_live.connect("notify::active", lambda w, p: self._update_setting("liveness_checks_enabled", w.get_active()))
        self._add_card_row(card_live, "Multi-Cue Liveness Analysis", "Analyzes specular glare, pixel lattice, and facial micro-motion", sw_live, is_last=True)
        page.pack_start(card_live, False, False, 0)

        # Strictness Mode Cards
        lbl_sec2 = Gtk.Label(label="SECURITY LEVEL")
        lbl_sec2.set_xalign(0)
        lbl_sec2.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec2, False, False, 0)

        card_mode = self._create_apple_card()

        self.rb_light = Gtk.RadioButton.new_with_label(None, "Standard (Passive Defense)")
        self.rb_heavy = Gtk.RadioButton.new_with_label_from_widget(self.rb_light, "Enhanced (Active Proof-of-Life)")

        if self.settings.liveness_mode == "heavy":
            self.rb_heavy.set_active(True)
        else:
            self.rb_light.set_active(True)

        self.rb_light.connect("toggled", lambda w: self._on_liveness_mode_changed("light") if w.get_active() else None)
        self.rb_heavy.connect("toggled", lambda w: self._on_liveness_mode_changed("heavy") if w.get_active() else None)

        self._add_card_row(card_mode, "Standard Mode", "Deny-only screening: detects phone screens, glare & paper printouts without user motion", self.rb_light)
        self._add_card_row(card_mode, "Enhanced Mode", "Requires active proof-of-life: verifies natural eye blinks and 3D head rotation", self.rb_heavy, is_last=True)
        page.pack_start(card_mode, False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 6: Face Lab Diagnostics & Telemetry
    # --------------------------------------------------------------------------
    def _build_facelab_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Face Lab", "Real-time biometric computer vision diagnostics, landmark meshes, and cue telemetry.")

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        # Left: Live Viewfinder with 106-point landmark mesh
        view_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        view_box.get_style_context().add_class("viewfinder-card")
        self.img_facelab = Gtk.Image()
        self.img_facelab.set_size_request(480, 360)
        view_box.pack_start(self.img_facelab, True, True, 0)
        hbox.pack_start(view_box, False, False, 0)

        # Right: Telemetry Dashboard
        v_telemetry = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        card_diag = self._create_apple_card()

        # Similarity score
        self.lbl_fl_sim = Gtk.Label(label="Similarity: --")
        self.lbl_fl_sim.set_xalign(0)
        self.lbl_fl_sim.get_style_context().add_class("row-label")
        self.prog_fl_sim = Gtk.ProgressBar()
        self.prog_fl_sim.set_size_request(240, -1)

        sim_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sim_box.pack_start(self.lbl_fl_sim, False, False, 0)
        sim_box.pack_start(self.prog_fl_sim, False, False, 0)
        self._add_card_row(card_diag, "Biometric Match", None, sim_box)

        # Liveness Cue Meters
        self.fl_cue_labels = {}
        self.fl_cue_progs = {}
        for cue in LivenessCue:
            c_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lbl = Gtk.Label(label=f"{cue.title}: --")
            lbl.set_xalign(0)
            lbl.get_style_context().add_class("row-desc")
            prog = Gtk.ProgressBar()
            prog.set_size_request(240, -1)
            c_box.pack_start(lbl, False, False, 0)
            c_box.pack_start(prog, False, False, 0)
            self.fl_cue_labels[cue] = lbl
            self.fl_cue_progs[cue] = prog
            self._add_card_row(card_diag, cue.title, None, c_box)

        # Liveness Decision Pill
        self.lbl_fl_decision = Gtk.Label(label="Decision: --")
        self.lbl_fl_decision.get_style_context().add_class("pill-active")
        self._add_card_row(card_diag, "Overall Verdict", "Final anti-spoof determination", self.lbl_fl_decision, is_last=True)

        v_telemetry.pack_start(card_diag, True, True, 0)
        hbox.pack_start(v_telemetry, True, True, 0)

        page.pack_start(hbox, True, True, 0)
        return scroll

    # --------------------------------------------------------------------------
    # Page 7: About Blink & Community
    # --------------------------------------------------------------------------
    def _build_about_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("About Blink", "Modern Apple-grade facial authentication for Linux & Windows.")

        # Hero Banner
        card_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card_hero.get_style_context().add_class("apple-card")
        card_hero.set_border_width(28)

        icon_surf = create_apple_icon_surface("face_id", "#0071E3", 56)
        card_hero.pack_start(Gtk.Image.new_from_surface(icon_surf), False, False, 0)

        lbl_app = Gtk.Label(label="<b>Blink</b>")
        lbl_app.set_use_markup(True)
        lbl_app.get_style_context().add_class("page-title")
        card_hero.pack_start(lbl_app, False, False, 0)

        lbl_ver = Gtk.Label(label="Version 2.1.0 • Stable Release")
        lbl_ver.get_style_context().add_class("pill-active")
        card_hero.pack_start(lbl_ver, False, False, 0)

        lbl_tag = Gtk.Label(label="Ultra-fast, hands-free Face ID authentication featuring on-device AI and biometric anti-spoofing.")
        lbl_tag.get_style_context().add_class("row-desc")
        card_hero.pack_start(lbl_tag, False, False, 0)

        page.pack_start(card_hero, False, False, 0)

        # Links & Community Card
        lbl_sec1 = Gtk.Label(label="RESOURCES & COMMUNITY")
        lbl_sec1.set_xalign(0)
        lbl_sec1.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec1, False, False, 0)

        card_links = self._create_apple_card()

        btn_web = Gtk.Button(label="Open Official Website")
        btn_web.get_style_context().add_class("btn-secondary")
        btn_web.connect("clicked", lambda b: subprocess.Popen(["xdg-open", "https://blink-web.vercel.app"]))
        self._add_card_row(card_links, "Blink Web", "Product guides, benchmarks, and release notes", btn_web)

        btn_git = Gtk.Button(label="GitHub Repository")
        btn_git.get_style_context().add_class("btn-secondary")
        btn_git.connect("clicked", lambda b: subprocess.Popen(["xdg-open", "https://github.com/realsamay/blink"]))
        self._add_card_row(card_links, "Source Code", "Contribute, star, or inspect source code on GitHub", btn_git, is_last=True)

        page.pack_start(card_links, False, False, 0)

        # System Diagnostics Card
        lbl_sec2 = Gtk.Label(label="SYSTEM & ENVIRONMENT")
        lbl_sec2.set_xalign(0)
        lbl_sec2.get_style_context().add_class("section-title")
        page.pack_start(lbl_sec2, False, False, 0)

        card_sys = self._create_apple_card()

        lbl_os = Gtk.Label(label=f"Linux {platform.release()} ({platform.machine()})")
        lbl_os.get_style_context().add_class("row-desc")
        self._add_card_row(card_sys, "Operating System", None, lbl_os)

        inj_avail = KeystrokeInjector.is_available()
        lbl_inj = Gtk.Label(label="Available (/dev/uinput)" if inj_avail else "Restricted")
        lbl_inj.get_style_context().add_class("pill-active" if inj_avail else "pill-inactive")
        self._add_card_row(card_sys, "Keystroke Injector", None, lbl_inj)

        is_svc_active = (subprocess.run(["systemctl", "--user", "is-active", "blink.service"], capture_output=True, text=True).stdout.strip() == "active")
        lbl_svc = Gtk.Label(label="Active (Running)" if is_svc_active else "Stopped")
        lbl_svc.get_style_context().add_class("pill-active" if is_svc_active else "pill-inactive")
        self._add_card_row(card_sys, "Background Daemon", "systemd user service status", lbl_svc, is_last=True)

        page.pack_start(card_sys, False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Callbacks & Settings Management
    # --------------------------------------------------------------------------
    def _update_setting(self, key: str, value: Any) -> None:
        setattr(self.settings, key, value)
        self.settings.save()

    def _on_toggle_face_unlock(self, switch, gparam):
        has_faces = bool(SecureFaceStore.load_identities())
        if switch.get_active() and not has_faces:
            switch.set_active(False)
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="No Enrolled Faces"
            )
            dialog.format_secondary_text("Please enroll your face in 'Enrolled Faces' before enabling Face ID.")
            dialog.run()
            dialog.destroy()
            return
        self._update_setting("is_face_unlock_enabled", switch.get_active())
        self._update_hero_status()

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
            self._update_setting("auto_lock_idle_minutes", int(active_id))

    def _on_camera_device_changed(self, combo: Gtk.ComboBoxText) -> None:
        active_id = combo.get_active_id()
        if active_id:
            idx = int(active_id)
            self._update_setting("camera_index", idx)
            self.camera.start(device_index=idx)

    def _on_liveness_mode_changed(self, mode: str) -> None:
        self._update_setting("liveness_mode", mode)
        self.liveness.mode = mode

    def _on_save_password(self, button) -> None:
        pwd = self.entry_pwd.get_text()
        if not pwd:
            return
        SecureCredentialManager.save_password(pwd)
        self.entry_pwd.set_text("")
        self.lbl_vault_status.set_text("● AES-256-GCM Stored")
        self.lbl_vault_status.get_style_context().remove_class("pill-inactive")
        self.lbl_vault_status.get_style_context().add_class("pill-active")

        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Password Encrypted & Stored"
        )
        dialog.format_secondary_text("Your desktop password has been securely saved with AES-256-GCM encryption.")
        dialog.run()
        dialog.destroy()

    def _on_remove_password(self, button) -> None:
        SecureCredentialManager.remove_password()
        self.lbl_vault_status.set_text("○ Not Configured")
        self.lbl_vault_status.get_style_context().remove_class("pill-active")
        self.lbl_vault_status.get_style_context().add_class("pill-inactive")

    def _on_toggle_identity(self, switch, gparam, ident_id: str) -> None:
        identities = SecureFaceStore.load_identities()
        for ident in identities:
            if ident["id"] == ident_id:
                ident["is_enabled"] = switch.get_active()
        SecureFaceStore.save_identities(identities)

    def _on_delete_identity(self, button, ident_id: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Delete Biometric Profile?"
        )
        dialog.format_secondary_text("Are you sure you want to remove this enrolled face profile? This cannot be undone.")
        resp = dialog.run()
        dialog.destroy()

        if resp == Gtk.ResponseType.YES:
            identities = SecureFaceStore.load_identities()
            identities = [ident for ident in identities if ident["id"] != ident_id]
            SecureFaceStore.save_identities(identities)
            self._refresh_faces_view()

    def _on_test_scan_clicked(self, button) -> None:
        """Pops out the Face ID overlay directly on desktop to test scanning animation & flow."""
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

        self._refresh_faces_view()

        if self._active_page in ("camera", "facelab"):
            self.camera.start(device_index=self.settings.camera_index)
            self._camera_timer = GLib.timeout_add(33, self._update_camera_feeds)

    # --------------------------------------------------------------------------
    # Camera Polling & Video Processing
    # --------------------------------------------------------------------------
    def _update_camera_feeds(self) -> bool:
        latest = self.camera.get_latest_frame()
        if latest is None:
            return True

        _, frame = latest
        h, w = frame.shape[:2]

        if self._active_page == "camera":
            pixbuf = self._cv2_to_pixbuf(frame, 480, 360)
            if pixbuf:
                self.img_cam_preview.set_from_pixbuf(pixbuf)

        elif self._active_page == "facelab":
            faces = self.detector.detect_faces(frame, compute_106_landmarks=True)
            dominant = FaceDetector.select_dominant_face(faces, frame_width=w)
            disp = frame.copy()

            if dominant:
                x, y, fw, fh = dominant.bbox
                cv2.rectangle(disp, (x, y), (x + fw, y + fh), (0, 255, 120), 2)
                for pt in dominant.landmarks_5pt:
                    cv2.circle(disp, (int(pt[0]), int(pt[1])), 4, (0, 255, 255), -1)

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
                        self.lbl_fl_sim.set_text(f"Similarity: {sim:.3f} ({top['name']})")
                        self.prog_fl_sim.set_fraction(max(0.0, min(1.0, sim)))
                    else:
                        self.lbl_fl_sim.set_text("Similarity: No faces enrolled")
                        self.prog_fl_sim.set_fraction(0.0)

                    # Update Liveness Cues
                    x1, y1 = max(0, x), max(0, y)
                    x2, y2 = min(w, x + fw), min(h, y + fh)
                    face_crop = frame[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None
                    decision = self.liveness.observe(frame, dominant, face_crop)

                    for cue, state in self.liveness.cue_states.items():
                        thresh = self.liveness.tuning.frames_for(cue)
                        self.fl_cue_labels[cue].set_text(f"{cue.title}: lvl {state.reading.level:.2f}")
                        self.fl_cue_progs[cue].set_fraction(state.progress(thresh))

                    dec_str = decision.kind.value.upper()
                    self.lbl_fl_decision.set_text(f"Decision: {dec_str}")
                    if decision.kind == LivenessDecisionKind.GENUINE:
                        self.lbl_fl_decision.get_style_context().remove_class("pill-inactive")
                        self.lbl_fl_decision.get_style_context().add_class("pill-active")
                    else:
                        self.lbl_fl_decision.get_style_context().remove_class("pill-active")
                        self.lbl_fl_decision.get_style_context().add_class("pill-inactive")

            pixbuf = self._cv2_to_pixbuf(disp, 480, 360)
            if pixbuf:
                self.img_facelab.set_from_pixbuf(pixbuf)

        return True

    def _cv2_to_pixbuf(self, img_bgr: np.ndarray, target_w: int, target_h: int) -> Optional[GdkPixbuf.Pixbuf]:
        try:
            resized = cv2.resize(img_bgr, (target_w, target_h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            return GdkPixbuf.Pixbuf.new_from_data(
                rgb.tobytes(),
                GdkPixbuf.Colorspace.RGB,
                False,
                8,
                w,
                h,
                w * 3
            )
        except Exception:
            return None

    def _on_destroy(self, window):
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

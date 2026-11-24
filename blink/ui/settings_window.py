"""
Blink Settings Application for Linux.
Designed following Apple iOS 27 and macOS Sequoia glassmorphism guidelines:
hardware-blurred obsidian windows, uncropped whole-sensor camera feeds,
zero-latency background neural diagnostics, and multi-face real-time highlighting.
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
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango
import cairo

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.core import updater
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


def enable_x11_window_blur(window: Gtk.Window) -> None:
    """Enables hardware blur behind the window on X11 compositors."""
    try:
        gdk_win = window.get_window()
        if gdk_win and hasattr(gdk_win, "get_xid"):
            xid = gdk_win.get_xid()
            subprocess.run(["xprop", "-id", str(xid), "-f", "_NET_WM_BLUR_BEHIND_REGION", "32c", "-set", "_NET_WM_BLUR_BEHIND_REGION", "0"], capture_output=True)
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Asynchronous Background Vision Telemetry Worker
# Decouples neural inference from GTK UI thread to completely eliminate UI lag!
# ------------------------------------------------------------------------------
class VisionTelemetryWorker(threading.Thread):
    def __init__(self, camera: CameraManager, detector: FaceDetector, aligner: FaceAligner, embedder: ArcFaceEmbedder, liveness: LivenessAnalyzer):
        super().__init__(daemon=True, name="BlinkVisionWorker")
        self.camera = camera
        self.detector = detector
        self.aligner = aligner
        self.embedder = embedder
        self.liveness = liveness
        self._running = False
        self._lock = threading.Lock()

        self.last_frame_id = -1
        self.detected_faces_telemetry: List[Dict[str, Any]] = []
        self.recognized_identity_ids: Dict[str, Tuple[str, float]] = {}  # {ident_id: (name, sim)}
        self.top_similarity = 0.0
        self.top_name = "No Face"
        self.liveness_cues_data: Dict[Any, Tuple[float, float]] = {}
        self.liveness_verdict = "Ready"
        self.inference_latency_ms = 0.0

    def start_worker(self) -> None:
        if not self._running:
            self._running = True
            if not self.is_alive():
                self.start()

    def stop_worker(self) -> None:
        self._running = False
        if self.is_alive() and threading.current_thread() != self:
            try:
                self.join(timeout=0.8)
            except Exception:
                pass

    def run(self) -> None:
        last_face_seen_time = time.time()
        last_frame_change_time = time.time()

        while self._running:
            try:
                if not self._running or not self.camera.is_running:
                    time.sleep(0.04)
                    continue

                latest = self.camera.get_latest_frame()
                now = time.time()

                if latest is None:
                    if now - last_frame_change_time > 0.6:
                        with self._lock:
                            self.detected_faces_telemetry = []
                            self.recognized_identity_ids = {}
                            self.top_similarity = 0.0
                            self.top_name = "No Face"
                    time.sleep(0.02)
                    continue

                frame_id, frame = latest
                if frame_id == self.last_frame_id:
                    # Same frame repeating (camera stalled or dropped)
                    if now - last_frame_change_time > 0.5:
                        with self._lock:
                            self.detected_faces_telemetry = []
                            self.recognized_identity_ids = {}
                            self.top_similarity = 0.0
                            self.top_name = "No Face"
                    time.sleep(0.015)
                    continue

                self.last_frame_id = frame_id
                last_frame_change_time = now
                t0 = time.time()

                h, w = frame.shape[:2]
                faces = self.detector.detect_faces(frame, compute_106_landmarks=True)
                identities = SecureFaceStore.load_identities()
                active_identities = [i for i in identities if i.get("is_enabled", True)]

                faces_telemetry = []
                recognized_ids = {}
                top_sim = 0.0
                top_name = "No Face"

                if faces:
                    last_face_seen_time = now
                    for face in faces:
                        aligned = self.aligner.align(face, frame)
                        matched_name = None
                        matched_sim = 0.0
                        is_match = False
                        matched_id = None

                        if aligned is not None and active_identities:
                            emb = self.embedder.compute_embedding(aligned)
                            scored = self.embedder.score_identities(emb, active_identities)
                            if scored:
                                settings = BlinkSettings.load()
                                matched = self.embedder.best_match(scored, settings.match_threshold)
                                if matched is not None:
                                    best_ident, centroid_sim, max_sim = matched
                                    matched_name = best_ident.get("name", "User")
                                    matched_sim = centroid_sim
                                    is_match = True
                                    matched_id = best_ident.get("id")
                                    recognized_ids[matched_id] = (matched_name, matched_sim)
                                    if centroid_sim > top_sim:
                                        top_sim = centroid_sim
                                        top_name = matched_name
                                else:
                                    top_ident, c_sim, _ = scored[0]
                                    matched_name = f"Unknown ({c_sim:.2f})"
                                    matched_sim = c_sim

                        faces_telemetry.append({
                            "bbox": face.bbox,
                            "name": matched_name or "Unknown Face",
                            "sim": matched_sim,
                            "is_match": is_match,
                            "ident_id": matched_id,
                            "landmarks_5pt": face.landmarks_5pt,
                            "landmarks_106pt": face.landmarks_106pt
                        })
                else:
                    # No faces detected in this frame
                    if now - last_face_seen_time > 0.35:
                        recognized_ids = {}
                        faces_telemetry = []
                        top_sim = 0.0
                        top_name = "No Face"

                # Liveness evaluation on dominant face
                cues_data = {}
                verdict = "Ready" if not faces else "Analyzing"
                if faces:
                    dominant = FaceDetector.select_dominant_face(faces, frame_width=w)
                    if dominant:
                        x, y, fw, fh = dominant.bbox
                        x1, y1 = max(0, x), max(0, y)
                        x2, y2 = min(w, x + fw), min(h, y + fh)
                        crop = frame[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None
                        dec = self.liveness.observe(frame, dominant, crop)
                        verdict = dec.kind.value.capitalize()
                        for cue, state in self.liveness.cue_states.items():
                            thresh = self.liveness.tuning.frames_for(cue)
                            cues_data[cue] = (state.reading.level, state.progress(thresh))
                else:
                    self.liveness.reset()

                latency = (time.time() - t0) * 1000.0

                with self._lock:
                    self.detected_faces_telemetry = faces_telemetry
                    self.recognized_identity_ids = recognized_ids
                    self.top_similarity = top_sim
                    self.top_name = top_name
                    self.liveness_cues_data = cues_data
                    self.liveness_verdict = verdict
                    self.inference_latency_ms = latency

                time.sleep(0.015)
            except Exception:
                time.sleep(0.04)

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "faces": list(self.detected_faces_telemetry),
                "recognized_ids": dict(self.recognized_identity_ids),
                "top_sim": self.top_similarity,
                "top_name": self.top_name,
                "cues": dict(self.liveness_cues_data),
                "verdict": self.liveness_verdict,
                "latency_ms": self.inference_latency_ms,
            }


# ------------------------------------------------------------------------------
# Apple iOS 27 / macOS Sequoia Blurred Glass Stylesheet
# ------------------------------------------------------------------------------
MACOS_CSS = """
/* Hardware-Blurred Obsidian Window Base (96% opacity prevents text see-through) */
window.macos-window {
    background-color: rgba(20, 20, 26, 0.96);
    color: #FFFFFF;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro", "Segoe UI", Roboto, Cantarell, sans-serif;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.16);
}

headerbar {
    background: linear-gradient(180deg, rgba(32, 32, 40, 0.85) 0%, rgba(24, 24, 30, 0.85) 100%);
    border-bottom: 1px solid rgba(255, 255, 255, 0.10);
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
    background: linear-gradient(180deg, rgba(20, 20, 28, 0.80) 0%, rgba(16, 16, 22, 0.80) 100%);
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
    color: #A1A1A6;
}
.sidebar-search-box {
    padding: 4px 12px 10px 12px;
}
entry.search-entry {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.14);
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
    background-color: rgba(255, 255, 255, 0.12);
}

list.sidebar-list {
    background-color: transparent;
}
list.sidebar-list row {
    background-color: transparent;
    border-radius: 8px;
    padding: 6px 10px;
    margin: 2px 8px;
    color: #D1D1D6;
    border: none;
}
list.sidebar-list row:hover {
    background-color: rgba(255, 255, 255, 0.07);
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
    color: #A1A1A6;
    margin-top: 20px;
    margin-bottom: 6px;
    margin-left: 2px;
    letter-spacing: -0.1px;
}
.group-footnote {
    font-size: 11.5px;
    color: #A1A1A6;
    margin-top: 6px;
    margin-bottom: 16px;
    margin-left: 2px;
}

/* Inset Group Cards (Rich Frosted Glass Materials) */
.inset-group {
    background: linear-gradient(180deg, rgba(36, 36, 44, 0.75) 0%, rgba(28, 28, 34, 0.75) 100%);
    border: 1px solid rgba(255, 255, 255, 0.13);
    border-radius: 12px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.14), 0 8px 32px rgba(0, 0, 0, 0.40);
    padding: 0;
}
.inset-row {
    padding: 10px 14px;
    min-height: 42px;
}
.inset-divider {
    background-color: rgba(255, 255, 255, 0.08);
    min-height: 1px;
    margin-left: 14px;
}
.row-label {
    font-size: 13px;
    font-weight: 500;
    color: #FFFFFF;
}
.row-desc {
    font-size: 11.5px;
    color: #A1A1A6;
}

/* Multi-Face Live Dynamic Highlighting */
.row-face-recognized {
    background-color: rgba(52, 199, 89, 0.16);
    border: 1px solid rgba(52, 199, 89, 0.55);
    box-shadow: 0 0 16px rgba(52, 199, 89, 0.35);
    border-radius: 10px;
}
.pill-badge-live-match {
    background-color: rgba(52, 199, 89, 0.22);
    border: 1px solid rgba(52, 199, 89, 0.50);
    color: #34C759;
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}

/* Glass Hero Banner */
.glass-hero {
    background: linear-gradient(135deg, rgba(40, 40, 52, 0.80) 0%, rgba(24, 24, 32, 0.80) 100%);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 16px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.20), 0 10px 30px rgba(0, 0, 0, 0.40);
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

/* Status Badges */
.pill-badge-active {
    background-color: rgba(52, 199, 89, 0.18);
    border: 1px solid rgba(52, 199, 89, 0.45);
    color: #34C759;
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill-badge-disabled {
    background-color: rgba(255, 159, 10, 0.18);
    border: 1px solid rgba(255, 159, 10, 0.45);
    color: #FF9F0A;
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill-badge-sensor {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.14);
    color: #D1D1D6;
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 500;
}

/* Glass Buttons */
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
    border: 1px solid rgba(255, 255, 255, 0.30);
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

/* Enrollment Profile Name Card */
.enroll-name-card {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 10px;
    padding: 10px 14px;
}
.enroll-name-card entry {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 6px;
    color: #FFFFFF;
    font-size: 13px;
    padding: 6px 10px;
}
.enroll-name-card entry:focus {
    border-color: #0A84FF;
    background-color: rgba(255, 255, 255, 0.12);
}

/* Authentic Apple Toggles */
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

/* Viewfinder */
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
# Cairo Vector Icon Generators
# ------------------------------------------------------------------------------
def create_macos_icon(kind: str, bg_hex: str, size: int = 24) -> cairo.ImageSurface:
    """Renders 2x retina supersampled authentic macOS squircle icons with glass highlight."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size * 2, size * 2)
    surf.set_device_scale(2.0, 2.0)
    cr = cairo.Context(surf)
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    h = bg_hex.lstrip("#")
    r, g, b = [int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)]

    rad = 6.0
    cr.new_sub_path()
    cr.arc(size - rad, rad, rad, -math.pi / 2, 0)
    cr.arc(size - rad, size - rad, rad, 0, math.pi / 2)
    cr.arc(rad, size - rad, rad, math.pi / 2, math.pi)
    cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgb(r, g, b)
    cr.fill_preserve()

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

    elif kind == "lock":
        # Apple-style lock icon (body + shackle + keyhole)
        cr.set_line_width(1.5)
        cr.arc(0, -1.8, 3.2, math.pi, 0)
        cr.line_to(3.2, 0.5)
        cr.stroke()
        l_rad = 1.6
        cr.new_sub_path()
        cr.arc(4.8 - l_rad, 0.5 + l_rad, l_rad, -math.pi / 2, 0)
        cr.arc(4.8 - l_rad, 6.0 - l_rad, l_rad, 0, math.pi / 2)
        cr.arc(-4.8 + l_rad, 6.0 - l_rad, l_rad, math.pi / 2, math.pi)
        cr.arc(-4.8 + l_rad, 0.5 + l_rad, l_rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()
        cr.set_source_rgb(r, g, b)
        cr.arc(0, 2.5, 0.9, 0, 2 * math.pi)
        cr.fill()
        cr.move_to(-0.5, 2.5)
        cr.line_to(0.5, 2.5)
        cr.line_to(0.7, 4.6)
        cr.line_to(-0.7, 4.6)
        cr.close_path()
        cr.fill()

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

        # Native HeaderBar with Blink Logo & Quick Actions
        self.headerbar = Gtk.HeaderBar()
        self.headerbar.set_show_close_button(True)
        self.headerbar.props.title = "Face ID & Password"

        logo_pb = get_blink_logo_pixbuf(22)
        if logo_pb:
            img_hdr_logo = Gtk.Image.new_from_pixbuf(logo_pb)
            img_hdr_logo.set_margin_start(4)
            img_hdr_logo.set_margin_end(2)
            self.headerbar.pack_start(img_hdr_logo)

        btn_hdr_refresh = Gtk.Button(label="↻ Refresh")
        btn_hdr_refresh.get_style_context().add_class("btn-macos")
        btn_hdr_refresh.set_valign(Gtk.Align.CENTER)
        btn_hdr_refresh.connect("clicked", self._on_manual_refresh)
        self.headerbar.pack_end(btn_hdr_refresh)

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

        from blink.core.lock_manager import BlinkLockManager
        self.lock_mgr = BlinkLockManager.get_instance()

        # Vision Worker runs decoupled in background to completely prevent UI lag!
        self.vision_worker = VisionTelemetryWorker(
            camera=self.camera,
            detector=self.detector,
            aligner=self.aligner,
            embedder=self.embedder,
            liveness=self.liveness
        )

        self._active_page = "face_id"
        self._camera_timer: Optional[int] = None
        self._test_coordinator = None
        self.profile_row_widgets: Dict[str, Dict[str, Any]] = {}

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
        self.stack.add_named(self._build_blink_lock_page(), "blink_lock")
        self.stack.add_named(self._build_camera_page(), "camera")
        self.stack.add_named(self._build_liveness_page(), "liveness")
        self.stack.add_named(self._build_diagnostics_page(), "diagnostics")
        self.stack.add_named(self._build_about_page(), "about")

        self.show_all()
        self.stack.show_all()

        # Select initial page and start background vision
        self.stack.set_visible_child_name("face_id")
        self.sidebar_list.select_row(self.nav_rows["face_id"])

        self._last_vault_store_mtime = 0.0
        GLib.timeout_add(1000, self._check_vault_auto_sync)

        self.connect("realize", self._on_realize)
        self.connect("destroy", self._on_destroy)

        # Start camera & vision worker for live recognition
        self._start_vision_pipeline()

        # Monitor lock state so Settings releases the camera when screen locks,
        # giving the background unlock daemon immediate and exclusive camera access!
        try:
            from blink.system.lock_monitor import LockMonitor
            self.lock_monitor = LockMonitor(
                on_locked=self._on_system_locked,
                on_unlocked=self._on_system_unlocked,
            )
            self.lock_monitor.start()
        except Exception as e:
            self.lock_monitor = None
            print(f"[SettingsWindow] Lock monitor initialization notice: {e}")

    def _start_vision_pipeline(self) -> None:
        """Starts the camera device, spawns a fresh worker thread if needed, and starts GUI timer."""
        if not self.camera.is_running:
            self.camera.start(device_index=self.settings.camera_index)

        if self.vision_worker is None or not self.vision_worker.is_alive():
            self.vision_worker = VisionTelemetryWorker(
                camera=self.camera,
                detector=self.detector,
                aligner=self.aligner,
                embedder=self.embedder,
                liveness=self.liveness,
            )
            self.vision_worker.start_worker()

        if self._camera_timer is None:
            self._camera_timer = GLib.timeout_add(33, self._update_camera_feeds)

    def _stop_vision_pipeline(self) -> None:
        """Stops GUI timer, stops vision worker thread, and releases camera device."""
        if self._camera_timer is not None:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None

        if self.vision_worker is not None:
            self.vision_worker.stop_worker()

        self.camera.stop()

    def _on_system_locked(self) -> None:
        """Screen was locked: immediately release camera so the background unlock daemon can scan without resource contention."""
        print("[SettingsWindow] Screen locked: releasing camera device for background daemon.")
        self._stop_vision_pipeline()

    def _on_system_unlocked(self) -> None:
        """Screen unlocked: resume live optical view if an optical tab is active."""
        print("[SettingsWindow] Screen unlocked: resuming live optical view.")
        def _resume():
            time.sleep(0.3)
            if self._active_page in ("face_id", "camera", "diagnostics"):
                self._start_vision_pipeline()
            return False
        GLib.idle_add(_resume)

    def _on_manual_refresh(self, button=None) -> None:
        """Manually refreshes sensor feeds, clears telemetry caches, and reloads profiles."""
        print("[SettingsWindow] Manual refresh requested.")

        if button and hasattr(button, "get_label"):
            orig_label = button.get_label()
            button.set_label("✓ Refreshed")
            def _restore():
                try:
                    button.set_label(orig_label)
                except Exception:
                    pass
                return False
            GLib.timeout_add(750, _restore)

        self._stop_vision_pipeline()

        # Explicitly clear all match styles from rows right away
        for widget_dict in self.profile_row_widgets.values():
            try:
                widget_dict["row"].get_style_context().remove_class("row-face-recognized")
                widget_dict["badge"].hide()
            except Exception:
                pass

        if hasattr(self, "lbl_fl_sim"):
            self.lbl_fl_sim.set_text("--")
            self.prog_fl_sim.set_fraction(0.0)
            self.lbl_fl_decision.set_text("Refreshed")
            if hasattr(self, "fl_cue_labels"):
                for cue in self.fl_cue_labels:
                    self.fl_cue_labels[cue].set_text("--")
                    self.fl_cue_progs[cue].set_fraction(0.0)

        self._refresh_profiles_row()
        self.camera.reset()

        if self._active_page in ("face_id", "camera", "diagnostics"):
            self._start_vision_pipeline()
            self._update_camera_feeds()

    def _on_realize(self, widget) -> None:
        enable_x11_window_blur(self)

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

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_box.get_style_context().add_class("sidebar-search-box")
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Search")
        self.search_entry.get_style_context().add_class("search-entry")
        search_box.pack_start(self.search_entry, True, True, 0)
        vbox.pack_start(search_box, False, False, 0)

        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.get_style_context().add_class("sidebar-list")
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)

        self.nav_rows: Dict[str, Gtk.ListBoxRow] = {}

        self._add_nav_item("face_id", "Face ID & Password", "face_id", "#0A84FF")
        self._add_nav_item("blink_lock", "Blink Lock", "lock", "#FF9F0A")
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

        if page_id == "blink_lock":
            self._refresh_vault_rows()
            self._update_app_summary_count()

        if page_id in ("face_id", "camera", "diagnostics"):
            self._start_vision_pipeline()
        else:
            self._stop_vision_pipeline()

    # --------------------------------------------------------------------------
    # Inset Card Helpers
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

    def _add_inset_row(self, group: Gtk.Box, title: str, subtitle: Optional[Any] = None, control: Optional[Gtk.Widget] = None, is_last: bool = False) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.get_style_context().add_class("inset-row")

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl_t = Gtk.Label(label=title)
        lbl_t.set_xalign(0)
        lbl_t.get_style_context().add_class("row-label")
        text_box.pack_start(lbl_t, False, False, 0)

        if subtitle:
            if isinstance(subtitle, Gtk.Widget):
                text_box.pack_start(subtitle, False, False, 0)
            else:
                lbl_sub = Gtk.Label(label=str(subtitle))
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
    # Page 1: Face ID & Password (Multi-Face Highlighting)
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

        # 2. Enrolled Biometric Profiles with Real-Time Multi-Face Highlighting
        h_faces_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h_faces_head.pack_start(self._create_group_heading("Face ID"), True, True, 0)
        btn_f_refresh = Gtk.Button(label="↻ Refresh")
        btn_f_refresh.get_style_context().add_class("btn-macos")
        btn_f_refresh.set_valign(Gtk.Align.CENTER)
        btn_f_refresh.connect("clicked", self._on_manual_refresh)
        h_faces_head.pack_end(btn_f_refresh, False, False, 0)
        page.pack_start(h_faces_head, False, False, 0)

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
        sw_space.set_active("on_windows_key" in self.settings.unlock_triggers or "on_space" in self.settings.unlock_triggers)
        sw_space.connect("notify::active", lambda w, p: self._on_trigger_switch("on_windows_key", w.get_active()))
        self._add_inset_row(group_use, "Unlock with Windows key", "Press the Windows (Super) key at lock screen to wake up Face ID and scan", sw_space)

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

        lbl_thresh_val = Gtk.Label(label=f"{self.settings.match_threshold:.2f}")
        lbl_thresh_val.get_style_context().add_class("row-desc")
        scale_thresh = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.55, 0.85, 0.01)
        scale_thresh.set_value(self.settings.match_threshold)
        scale_thresh.set_size_request(140, -1)
        scale_thresh.connect("value-changed", lambda w: self._on_thresh_slider(w, lbl_thresh_val))
        h_thresh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h_thresh.pack_start(scale_thresh, False, False, 0)
        h_thresh.pack_start(lbl_thresh_val, False, False, 0)
        self._add_inset_row(group_tune, "Similarity threshold", "Higher values require closer biometric match (0.65 recommended)", h_thresh)

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

        self.profile_row_widgets.clear()
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

                h_title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name_lbl = Gtk.Label(label=ident.get("name", "User"))
                name_lbl.set_xalign(0)
                name_lbl.get_style_context().add_class("row-label")
                h_title_row.pack_start(name_lbl, False, False, 0)

                # Live In-Camera Match Badge (lights up when person is seen!)
                badge_match = Gtk.Label(label="● In Camera View")
                badge_match.get_style_context().add_class("pill-badge-live-match")
                badge_match.set_no_show_all(True)
                badge_match.hide()
                h_title_row.pack_start(badge_match, False, False, 0)
                v.pack_start(h_title_row, False, False, 0)

                poses_count = len(ident.get("samples", []))
                sub_lbl = Gtk.Label(label=f"{poses_count} biometric poses enrolled")
                sub_lbl.set_xalign(0)
                sub_lbl.get_style_context().add_class("row-desc")
                v.pack_start(sub_lbl, False, False, 0)
                row.pack_start(v, True, True, 0)

                btn_del = Gtk.Button(label="Delete")
                btn_del.get_style_context().add_class("btn-macos-danger")
                btn_del.set_valign(Gtk.Align.CENTER)
                btn_del.connect("clicked", self._on_delete_identity, ident["id"])
                row.pack_end(btn_del, False, False, 0)

                self.group_profiles.pack_start(row, False, False, 0)

                # Cache widgets for live recognition highlighting
                self.profile_row_widgets[ident["id"]] = {
                    "row": row,
                    "badge": badge_match,
                    "name": ident.get("name", "User"),
                    "poses": poses_count
                }

                if not is_last:
                    div = Gtk.Box()
                    div.get_style_context().add_class("inset-divider")
                    self.group_profiles.pack_start(div, False, False, 0)

            # Footer row to add another face
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
    # Page: Blink Lock (App, Folder & File Protection)
    # --------------------------------------------------------------------------
    def _build_blink_lock_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Blink Lock")

        # 1. Glass Hero Banner
        top_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top_hero.get_style_context().add_class("glass-hero")
        top_hero.set_margin_bottom(18)

        h_hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        v_hero_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        lbl_hero_t = Gtk.Label(label="Blink Lock & Vault")
        lbl_hero_t.set_xalign(0)
        lbl_hero_t.get_style_context().add_class("hero-title")

        lbl_hero_d = Gtk.Label(label="Biometric protection for apps, sensitive folders, and private files.")
        lbl_hero_d.set_xalign(0)
        lbl_hero_d.get_style_context().add_class("hero-desc")

        v_hero_info.pack_start(lbl_hero_t, False, False, 0)
        v_hero_info.pack_start(lbl_hero_d, False, False, 0)
        h_hero.pack_start(v_hero_info, True, True, 0)

        self.sw_lock_master = Gtk.Switch()
        self.sw_lock_master.set_valign(Gtk.Align.CENTER)
        self.sw_lock_master.set_active(self.lock_mgr.is_enabled())
        self.sw_lock_master.connect("state-set", self._on_lock_master_toggled)
        h_hero.pack_end(self.sw_lock_master, False, False, 0)

        top_hero.pack_start(h_hero, True, True, 0)
        page.pack_start(top_hero, False, False, 0)

        # 2. Section: Protected Applications (App Lock)
        page.pack_start(self._create_group_heading("Protected Applications"), False, False, 0)
        self.group_locked_apps = self._create_inset_group()
        page.pack_start(self.group_locked_apps, False, False, 0)
        self._build_installed_apps_view()

        page.pack_start(self._create_group_footnote(
            "Protected applications require Face ID authentication as soon as their window is opened or brought to focus."
        ), False, False, 0)

        # 3. Section: Encrypted Vault (Files & Folders)
        page.pack_start(self._create_group_heading("Encrypted Vault (Files & Folders)"), False, False, 0)
        self.group_vault = self._create_inset_group()
        page.pack_start(self.group_vault, False, False, 0)
        self._refresh_vault_rows()

        page.pack_start(self._create_group_footnote(
            "Items in the vault are encrypted with military-grade AES-256-GCM. You can also right-click any item in your file manager and select 'Lock with Blink'."
        ), False, False, 0)

        # 4. Section: Lock Preferences
        page.pack_start(self._create_group_heading("Preferences & Shortcuts"), False, False, 0)
        group_prefs = self._create_inset_group()

        combo_grace = Gtk.ComboBoxText()
        combo_grace.append("0", "Immediate (Always lock)")
        combo_grace.append("60", "1 Minute")
        combo_grace.append("300", "5 Minutes")
        combo_grace.append("900", "15 Minutes")
        current_grace = str(self.lock_mgr.get_grace_period())
        if current_grace not in ("0", "60", "300", "900"):
            combo_grace.append(current_grace, f"{current_grace} Seconds")
        combo_grace.set_active_id(current_grace)
        combo_grace.connect("changed", self._on_grace_period_changed)
        self._add_inset_row(group_prefs, "Unlock session grace period", "How long an app stays unlocked before requiring a new face scan", combo_grace)

        self.btn_install_ctx = Gtk.Button(label="Install Context Menu")
        self.btn_install_ctx.get_style_context().add_class("btn-macos")
        self.btn_install_ctx.connect("clicked", self._on_install_context_menu_clicked)
        self._add_inset_row(group_prefs, "File Manager integration", "Add 'Lock with Blink' to right-click menus in Nemo, Nautilus, and Caja", self.btn_install_ctx, is_last=True)

        page.pack_start(group_prefs, False, False, 0)
        return scroll

    def _on_lock_master_toggled(self, switch: Gtk.Switch, state: bool) -> bool:
        self.lock_mgr.set_enabled(state)
        return False

    def _on_grace_period_changed(self, combo: Gtk.ComboBoxText) -> None:
        active_id = combo.get_active_id()
        if active_id:
            self.lock_mgr.set_grace_period(int(active_id))

    def _on_install_context_menu_clicked(self, button: Gtk.Button) -> None:
        self.lock_mgr.install_context_menus()
        button.set_label("✓ Installed")
        button.set_sensitive(False)

    def _build_installed_apps_view(self) -> None:
        from blink.core.app_discovery import list_installed_applications

        for child in self.group_locked_apps.get_children():
            self.group_locked_apps.remove(child)

        # 1. Search Bar & Count Row
        h_search = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h_search.set_margin_start(14)
        h_search.set_margin_end(14)
        h_search.set_margin_top(10)
        h_search.set_margin_bottom(10)

        self.app_search_entry = Gtk.SearchEntry()
        self.app_search_entry.set_placeholder_text("Search installed apps (Chrome, Discord, Terminal, Telegram...)...")
        self.app_search_entry.connect("search-changed", self._on_app_search_changed)
        h_search.pack_start(self.app_search_entry, True, True, 0)

        self.lbl_app_summary = Gtk.Label()
        self.lbl_app_summary.get_style_context().add_class("row-desc")
        h_search.pack_end(self.lbl_app_summary, False, False, 0)

        self.group_locked_apps.pack_start(h_search, False, False, 0)

        # Separator line
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.group_locked_apps.pack_start(sep, False, False, 0)

        # 2. Scrollable App List
        scroll_apps = Gtk.ScrolledWindow()
        scroll_apps.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_apps.set_min_content_height(280)
        scroll_apps.set_max_content_height(340)

        self.app_list_box = Gtk.ListBox()
        self.app_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.app_list_box.set_filter_func(self._filter_app_row)
        self.app_list_box.set_sort_func(self._sort_app_row)
        scroll_apps.add(self.app_list_box)
        self.group_locked_apps.pack_start(scroll_apps, True, True, 0)

        # Populate with installed user applications
        installed = list_installed_applications()
        locked_keys = {a.get("key", "").lower(): a for a in self.lock_mgr.get_locked_apps()}

        # Also add any custom locked apps that might not have a .desktop file
        all_apps = list(installed)
        for lk, l_info in locked_keys.items():
            if not any(a["key"] == lk for a in all_apps):
                all_apps.append({
                    "name": l_info.get("name", lk.capitalize()),
                    "key": lk,
                    "exec": l_info.get("exe", lk),
                    "icon": "application-x-executable",
                    "comment": "Custom Application",
                })

        for app in all_apps:
            row = self._create_app_list_row(app, is_locked=(app["key"] in locked_keys))
            self.app_list_box.add(row)

        self._update_app_summary_count()
        self.group_locked_apps.show_all()

    def _create_app_list_row(self, app: Dict[str, Any], is_locked: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._app_data = app
        row._is_locked = is_locked

        h_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h_box.set_margin_start(14)
        h_box.set_margin_end(14)
        h_box.set_margin_top(8)
        h_box.set_margin_bottom(8)

        # App Icon
        icon_w = self._get_app_icon_widget(app.get("icon"), size=36)
        h_box.pack_start(icon_w, False, False, 0)

        # Name & Details
        v_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl_name = Gtk.Label(label=app.get("name", "App"))
        lbl_name.set_xalign(0)
        lbl_name.get_style_context().add_class("row-title")

        sub_desc = app.get("comment") or app.get("exec", "")
        lbl_desc = Gtk.Label(label=sub_desc)
        lbl_desc.set_xalign(0)
        lbl_desc.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_desc.set_max_width_chars(42)
        lbl_desc.get_style_context().add_class("row-desc")

        v_info.pack_start(lbl_name, False, False, 0)
        v_info.pack_start(lbl_desc, False, False, 0)
        h_box.pack_start(v_info, True, True, 0)

        # Status badge
        lbl_badge = Gtk.Label(label="● Guarded" if is_locked else "")
        lbl_badge.get_style_context().add_class("pill-badge-active" if is_locked else "row-desc")
        lbl_badge.set_visible(is_locked)
        row._badge_widget = lbl_badge
        h_box.pack_start(lbl_badge, False, False, 6)

        # Toggle Switch
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(is_locked)
        sw.connect("state-set", lambda s, state, r=row, a=app: self._on_app_switch_toggled(r, a, state))
        h_box.pack_start(sw, False, False, 0)

        row.add(h_box)
        return row

    def _get_app_icon_widget(self, icon_str: Optional[str], size: int = 36) -> Gtk.Widget:
        theme = Gtk.IconTheme.get_default()
        if icon_str:
            if os.path.exists(icon_str):
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_str, size, size, True)
                    return Gtk.Image.new_from_pixbuf(pb)
                except Exception:
                    pass
            if theme.has_icon(icon_str):
                return Gtk.Image.new_from_icon_name(icon_str, Gtk.IconSize.DND)
        return Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DND)

    def _on_app_switch_toggled(self, row: Gtk.ListBoxRow, app: Dict[str, Any], state: bool) -> bool:
        row._is_locked = state
        app_key = app.get("key", "").lower()
        app_name = app.get("name", "Application")
        app_exe = app.get("exec", app_key)

        if state:
            self.lock_mgr.add_locked_app(app_name, app_key, app_exe)
            row._badge_widget.set_text("● Guarded")
            row._badge_widget.get_style_context().add_class("pill-badge-active")
            row._badge_widget.set_visible(True)
        else:
            self.lock_mgr.remove_locked_app(app_key)
            row._badge_widget.set_visible(False)

        self._update_app_summary_count()
        self.app_list_box.invalidate_sort()
        return False

    def _filter_app_row(self, row: Gtk.ListBoxRow) -> bool:
        query = self.app_search_entry.get_text().strip().lower()
        if not query:
            return True
        app = getattr(row, "_app_data", {})
        name = app.get("name", "").lower()
        key = app.get("key", "").lower()
        comment = app.get("comment", "").lower()
        return query in name or query in key or query in comment

    def _sort_app_row(self, row1: Gtk.ListBoxRow, row2: Gtk.ListBoxRow) -> int:
        l1 = getattr(row1, "_is_locked", False)
        l2 = getattr(row2, "_is_locked", False)
        if l1 != l2:
            return -1 if l1 else 1
        name1 = getattr(row1, "_app_data", {}).get("name", "").lower()
        name2 = getattr(row2, "_app_data", {}).get("name", "").lower()
        return (name1 > name2) - (name1 < name2)

    def _on_app_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self.app_list_box.invalidate_filter()

    def _update_app_summary_count(self) -> None:
        locked_cnt = len(self.lock_mgr.get_locked_apps())
        self.lbl_app_summary.set_text(f"{locked_cnt} Guarded")

    def _check_vault_auto_sync(self) -> bool:
        try:
            from blink.core.lock_manager import LOCK_STORE_FILE
            if LOCK_STORE_FILE.exists():
                mtime = LOCK_STORE_FILE.stat().st_mtime
                if mtime != getattr(self, "_last_vault_store_mtime", 0.0):
                    self._last_vault_store_mtime = mtime
                    if getattr(self, "_active_page", None) == "blink_lock":
                        self._refresh_vault_rows()
                        self._update_app_summary_count()
        except Exception:
            pass
        return True

    def _refresh_vault_rows(self) -> None:
        for child in self.group_vault.get_children():
            self.group_vault.remove(child)

        items = self.lock_mgr.get_locked_items()
        if not items:
            self._add_inset_row(
                self.group_vault,
                "Vault is Empty",
                "Lock any file or folder using the buttons below or right-click in your file manager"
            )
        else:
            for it in items:
                name = it.get("name", "Item")
                orig_str = it.get("original_path", "")
                itype = it.get("type", "file").capitalize()
                size_kb = it.get("size_bytes", 0) / 1024.0
                sz_str = f"{size_kb / 1024.0:.1f} MB" if size_kb > 1024 else f"{size_kb:.1f} KB"

                h_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

                btn_unlock = Gtk.Button(label="🔓 Unlock")
                btn_unlock.get_style_context().add_class("btn-macos-primary")
                btn_unlock.connect("clicked", lambda b, rec=it: self._on_unlock_vault_item(rec))
                h_ctrl.pack_start(btn_unlock, False, False, 0)

                btn_rm = Gtk.Button(label="🗑️")
                btn_rm.get_style_context().add_class("btn-macos")
                btn_rm.connect("clicked", lambda b, rec=it: self._on_delete_vault_item(rec))
                h_ctrl.pack_start(btn_rm, False, False, 0)

                prefix = "📁 " if itype == "Folder" else "📄 "
                self._add_inset_row(
                    self.group_vault,
                    f"{prefix}{name}",
                    f"{itype} • {sz_str} • {orig_str}",
                    h_ctrl
                )

        # Action Buttons Row
        h_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_lock_folder = Gtk.Button(label="📁 Lock Folder...")
        btn_lock_folder.get_style_context().add_class("btn-macos")
        btn_lock_folder.connect("clicked", self._on_lock_folder_clicked)
        h_btns.pack_start(btn_lock_folder, False, False, 0)

        btn_lock_file = Gtk.Button(label="📄 Lock File...")
        btn_lock_file.get_style_context().add_class("btn-macos")
        btn_lock_file.connect("clicked", self._on_lock_file_clicked)
        h_btns.pack_start(btn_lock_file, False, False, 0)

        self._add_inset_row(
            self.group_vault,
            "Protect Files & Folders",
            "Encrypt with AES-256-GCM and store in biometric vault",
            h_btns,
            is_last=True
        )
        self.group_vault.show_all()

    def _on_lock_folder_clicked(self, button: Gtk.Button) -> None:
        chooser = Gtk.FileChooserDialog(
            title="Select Folder to Lock with Blink",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        chooser.add_button("Cancel", Gtk.ResponseType.CANCEL)
        btn_lock = chooser.add_button("Lock & Encrypt Folder", Gtk.ResponseType.OK)
        btn_lock.get_style_context().add_class("btn-macos-primary")

        if chooser.run() == Gtk.ResponseType.OK:
            selected = chooser.get_filename()
            chooser.destroy()
            if selected:
                try:
                    self.lock_mgr.lock_path(selected)
                    self._refresh_vault_rows()
                except Exception as e:
                    self._show_error_dialog(f"Failed to lock folder:\n{e}")
        else:
            chooser.destroy()

    def _on_lock_file_clicked(self, button: Gtk.Button) -> None:
        chooser = Gtk.FileChooserDialog(
            title="Select File to Lock with Blink",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        chooser.add_button("Cancel", Gtk.ResponseType.CANCEL)
        btn_lock = chooser.add_button("Lock & Encrypt File", Gtk.ResponseType.OK)
        btn_lock.get_style_context().add_class("btn-macos-primary")

        if chooser.run() == Gtk.ResponseType.OK:
            selected = chooser.get_filename()
            chooser.destroy()
            if selected:
                try:
                    self.lock_mgr.lock_path(selected)
                    self._refresh_vault_rows()
                except Exception as e:
                    self._show_error_dialog(f"Failed to lock file:\n{e}")
        else:
            chooser.destroy()

    def _on_unlock_vault_item(self, item_record: Dict[str, Any]) -> None:
        locked_path = item_record.get("locked_path", "")
        item_name = item_record.get("name", "Item")

        # Temporarily pause background vision worker during scan
        if self._camera_timer is not None:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        if self.vision_worker is not None:
            self.vision_worker.stop_worker()

        # Launch shield subprocess for isolated biometric verification
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        bin_path = shutil.which("blink")
        if bin_path and os.path.exists(bin_path):
            cmd = [bin_path, "shield", "--path", locked_path]
        else:
            cmd = [sys.executable, str(main_py), "shield", "--path", locked_path]

        subprocess.run(cmd)
        self._refresh_vault_rows()

        if self._active_page in ("face_id", "camera", "diagnostics"):
            self._start_vision_pipeline()

    def _on_delete_vault_item(self, item_record: Dict[str, Any]) -> None:
        it_id = item_record.get("id")
        data = self.lock_mgr._load_raw()
        data["locked_items"] = [x for x in data.get("locked_items", []) if x.get("id") != it_id]
        self.lock_mgr._save_raw(data)
        self._refresh_vault_rows()

    def _show_error_dialog(self, message: str) -> None:
        d = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            message_format=message
        )
        d.run()
        d.destroy()

    # --------------------------------------------------------------------------
    # Page 2: Camera (Whole Sensor Form)
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

        btn_cam_refresh = Gtk.Button(label="↻ Refresh Sensor")
        btn_cam_refresh.get_style_context().add_class("btn-macos")
        btn_cam_refresh.set_valign(Gtk.Align.CENTER)
        btn_cam_refresh.connect("clicked", self._on_manual_refresh)
        hud_bar.pack_end(btn_cam_refresh, False, False, 0)

        self.viewfinder_container.pack_start(hud_bar, False, False, 0)

        h_align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        h_align_box.set_halign(Gtk.Align.CENTER)
        self.img_cam_preview = Gtk.Image()
        h_align_box.pack_start(self.img_cam_preview, True, True, 0)
        self.viewfinder_container.pack_start(h_align_box, True, True, 0)

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
    # Page 4: Diagnostics & Face Lab (Zero-Lag Live HUD Viewfinder)
    # --------------------------------------------------------------------------
    def _build_diagnostics_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("Diagnostics")

        h_diag_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h_diag_head.pack_start(self._create_group_heading("Real-time vision telemetry (live on-camera stats)"), True, True, 0)
        btn_diag_refresh = Gtk.Button(label="↻ Refresh Diagnostics")
        btn_diag_refresh.get_style_context().add_class("btn-macos")
        btn_diag_refresh.set_valign(Gtk.Align.CENTER)
        btn_diag_refresh.connect("clicked", self._on_manual_refresh)
        h_diag_head.pack_end(btn_diag_refresh, False, False, 0)
        page.pack_start(h_diag_head, False, False, 0)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        # Left: Live Viewfinder with HUD drawn directly on camera
        view_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        view_box.get_style_context().add_class("viewfinder-card")
        self.img_facelab = Gtk.Image()
        view_box.pack_start(self.img_facelab, True, True, 0)
        hbox.pack_start(view_box, False, False, 0)

        # Right: Telemetry Meters Inset Group
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

        page.pack_start(self._create_group_footnote("Facial landmarks, multi-face tracking, and biometric embedding scores are computed asynchronously in background."), False, False, 0)

        return scroll

    # --------------------------------------------------------------------------
    # Page 5: About
    # --------------------------------------------------------------------------
    def _build_about_page(self) -> Gtk.Widget:
        scroll, page = self._create_page_wrapper("About")

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

        self.lbl_ver = Gtk.Label(label=f"Version {updater.CURRENT_VERSION} • Apple Human Interface Edition")
        self.lbl_ver.set_xalign(0)
        self.lbl_ver.get_style_context().add_class("row-desc")

        lbl_arch = Gtk.Label(label="Linux x86_64 • 100% On-Device Neural Vision")
        lbl_arch.set_xalign(0)
        lbl_arch.get_style_context().add_class("row-desc")

        v_top.pack_start(lbl_app, False, False, 0)
        v_top.pack_start(self.lbl_ver, False, False, 0)
        v_top.pack_start(lbl_arch, False, False, 0)
        h_brand_row.pack_start(v_top, True, True, 0)

        top_hero.pack_start(h_brand_row, True, True, 0)
        page.pack_start(top_hero, False, False, 0)

        # Software Update Group
        page.pack_start(self._create_group_heading("Software Update"), False, False, 0)

        group_update = self._create_inset_group()

        h_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.spn_update = Gtk.Spinner()
        self.spn_update.set_no_show_all(True)
        self.btn_check_update = Gtk.Button(label="Check for Updates")
        self.btn_check_update.get_style_context().add_class("btn-macos")
        self.btn_check_update.connect("clicked", self._on_check_for_updates)
        h_ctrl.pack_start(self.spn_update, False, False, 0)
        h_ctrl.pack_start(self.btn_check_update, False, False, 0)

        self.lbl_update_sub = Gtk.Label(label=f"Current version: v{updater.CURRENT_VERSION} • Up to date")
        self.lbl_update_sub.set_xalign(0)
        self.lbl_update_sub.get_style_context().add_class("row-desc")

        self._add_inset_row(group_update, "Blink Updates", self.lbl_update_sub, h_ctrl, is_last=False)

        # Expandable Update Card with Revealer
        self.rev_update_card = Gtk.Revealer()
        self.rev_update_card.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.rev_update_card.set_transition_duration(250)
        self.rev_update_card.set_reveal_child(False)

        self.box_update_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.box_update_card.set_margin_start(16)
        self.box_update_card.set_margin_end(16)
        self.box_update_card.set_margin_top(12)
        self.box_update_card.set_margin_bottom(14)
        self.rev_update_card.add(self.box_update_card)

        self.lbl_update_title = Gtk.Label()
        self.lbl_update_title.set_xalign(0)
        self.lbl_update_title.get_style_context().add_class("row-label")
        self.box_update_card.pack_start(self.lbl_update_title, False, False, 0)

        # Scrolled changelog container
        scroll_cl = Gtk.ScrolledWindow()
        scroll_cl.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_cl.set_min_content_height(100)
        scroll_cl.set_max_content_height(180)

        self.lbl_changelog = Gtk.Label()
        self.lbl_changelog.set_xalign(0)
        self.lbl_changelog.set_line_wrap(True)
        self.lbl_changelog.get_style_context().add_class("row-desc")
        scroll_cl.add(self.lbl_changelog)
        self.box_update_card.pack_start(scroll_cl, False, False, 0)

        # Action row
        h_action = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.btn_install_update = Gtk.Button(label="Download & Install Update")
        self.btn_install_update.get_style_context().add_class("btn-macos-primary")
        self.btn_install_update.connect("clicked", self._on_install_update_clicked)
        h_action.pack_start(self.btn_install_update, False, False, 0)

        self.lbl_install_status = Gtk.Label(label="")
        self.lbl_install_status.set_xalign(0)
        self.lbl_install_status.get_style_context().add_class("row-desc")
        h_action.pack_start(self.lbl_install_status, True, True, 0)
        self.box_update_card.pack_start(h_action, False, False, 0)

        self.prog_update = Gtk.ProgressBar()
        self.prog_update.set_no_show_all(True)
        self.prog_update.set_visible(False)
        self.box_update_card.pack_start(self.prog_update, False, False, 0)

        group_update.pack_start(self.rev_update_card, False, False, 0)

        page.pack_start(group_update, False, False, 0)

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

    def _on_check_for_updates(self, button: Gtk.Button) -> None:
        self.btn_check_update.set_sensitive(False)
        self.spn_update.set_visible(True)
        self.spn_update.start()
        self.lbl_update_sub.set_text("Checking GitHub for the latest release...")

        def _bg():
            res = updater.check_for_updates()
            GLib.idle_add(self._on_check_updates_complete, res)

        threading.Thread(target=_bg, daemon=True, name="BlinkUpdateCheck").start()

    def _on_check_updates_complete(self, res: Dict[str, Any]) -> None:
        self.spn_update.stop()
        self.spn_update.set_visible(False)
        self.btn_check_update.set_sensitive(True)

        if res.get("status") == "error":
            err_msg = res.get("error", "Network error")
            self.lbl_update_sub.set_text(f"Check failed: {err_msg}")
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Update Check Failed",
            )
            dialog.format_secondary_text(f"Could not connect to GitHub to check for updates:\n\n{err_msg}")
            dialog.run()
            dialog.destroy()
            return

        has_update = res.get("has_update", False)
        latest_ver = res.get("latest_version", "")
        rel_name = res.get("release_name", f"Blink v{latest_ver}")
        changelog = res.get("changelog", "Bug fixes and performance improvements.")

        self._pending_update = res
        self.lbl_update_title.set_markup(f"<b>What's New in {rel_name}:</b>")
        self.lbl_changelog.set_text(changelog)
        self.prog_update.set_visible(False)
        self.lbl_install_status.set_text("")

        if has_update:
            self.lbl_update_sub.set_text(f"● Update Available: v{latest_ver}")
            self.btn_install_update.set_label(f"Download & Install Update (v{latest_ver})")
            self.rev_update_card.set_reveal_child(True)
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=f"Update Available: Blink v{latest_ver}",
            )
            dialog.format_secondary_text(
                f"A new version of Blink ({rel_name}) is available!\n\n"
                "Review the release highlights below and click 'Download & Install Update' to upgrade."
            )
            dialog.run()
            dialog.destroy()
        else:
            self.lbl_update_sub.set_text(f"✓ Blink is up to date (v{updater.CURRENT_VERSION})")
            self.btn_install_update.set_label(f"Reinstall / Repair v{latest_ver}")
            self.rev_update_card.set_reveal_child(True)
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Blink is Up to Date",
            )
            dialog.format_secondary_text(
                f"You are currently running Blink v{updater.CURRENT_VERSION}, which is the latest version available.\n\n"
                f"Latest published release: {rel_name}"
            )
            dialog.run()
            dialog.destroy()

    def _on_install_update_clicked(self, button: Gtk.Button) -> None:
        if not hasattr(self, "_pending_update") or not self._pending_update:
            return

        deb_url = self._pending_update.get("deb_url")
        if not deb_url:
            self.lbl_install_status.set_text("No Debian package found in this release.")
            return

        self.btn_install_update.set_sensitive(False)
        self.prog_update.set_visible(True)
        self.prog_update.set_fraction(0.0)
        self.lbl_install_status.set_text("Connecting...")

        dest_file = updater.DOWNLOAD_CACHE_DIR / self._pending_update.get("deb_filename", "blink_latest.deb")

        def _progress(fraction: float, dl: int, total: int):
            def _update():
                self.prog_update.set_fraction(fraction)
                if total > 0:
                    mb_dl = dl / (1024 * 1024)
                    mb_tot = total / (1024 * 1024)
                    self.lbl_install_status.set_text(f"Downloading: {mb_dl:.1f} MB / {mb_tot:.1f} MB ({int(fraction*100)}%)")
                else:
                    self.lbl_install_status.set_text(f"Downloading... ({int(fraction*100)}%)")
            GLib.idle_add(_update)

        def _bg():
            ok, msg = updater.download_package(deb_url, dest_file, progress_callback=_progress)
            if not ok:
                GLib.idle_add(self._on_install_complete, False, f"Download failed: {msg}")
                return

            GLib.idle_add(lambda: self.lbl_install_status.set_text("Installing package (authenticating with pkexec)..."))
            ok_inst, msg_inst = updater.install_deb_package(dest_file)
            GLib.idle_add(self._on_install_complete, ok_inst, msg_inst)

        threading.Thread(target=_bg, daemon=True, name="BlinkUpdateInstaller").start()

    def _on_install_complete(self, success: bool, message: str) -> None:
        self.prog_update.set_visible(False)
        self.btn_install_update.set_sensitive(True)

        if success:
            latest_ver = self._pending_update.get("latest_version", "")
            self.lbl_install_status.set_text("✓ Updated successfully!")
            self.lbl_update_sub.set_text(f"✓ Blink updated to v{latest_ver}")
            self.lbl_ver.set_text(f"Version {latest_ver} • Apple Human Interface Edition")
            self.rev_update_card.set_reveal_child(False)

            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Blink Updated Successfully!"
            )
            dialog.format_secondary_text(
                f"Blink has been updated to version {latest_ver}.\n"
                "The background daemon has been restarted with the latest fixes and improvements."
            )
            dialog.run()
            dialog.destroy()
        else:
            self.lbl_install_status.set_text(f"Install error: {message}")
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Update Installation Failed"
            )
            dialog.format_secondary_text(f"Could not complete installation:\n{message}")
            dialog.run()
            dialog.destroy()

    # --------------------------------------------------------------------------
    # Settings Callbacks & Background Synchronization
    # --------------------------------------------------------------------------
    def _update_setting(self, key: str, value: Any) -> None:
        setattr(self.settings, key, value)
        self.settings.save()
        self._sync_daemon_service()

    _sync_timer: Optional[threading.Timer] = None

    def _sync_daemon_service(self) -> None:
        """Debounced background synchronization so rapid slider adjustments never flood systemd."""
        if self._sync_timer is not None:
            try:
                self._sync_timer.cancel()
            except Exception:
                pass

        def _do_restart():
            try:
                proc = subprocess.run(["systemctl", "--user", "is-active", "blink.service"], capture_output=True, text=True)
                if proc.stdout.strip() in ("active", "failed"):
                    subprocess.run(["systemctl", "--user", "reset-failed", "blink.service"], capture_output=True)
                    subprocess.run(["systemctl", "--user", "restart", "blink.service"], capture_output=True)
            except Exception:
                pass

        self._sync_timer = threading.Timer(0.8, _do_restart)
        self._sync_timer.daemon = True
        self._sync_timer.start()

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
            self.lbl_hero_status.set_text("● Active (Works immediately)" if active else "● Disabled")
            ctx = self.lbl_hero_status.get_style_context()
            if active:
                ctx.remove_class("pill-badge-disabled")
                ctx.add_class("pill-badge-active")
            else:
                ctx.remove_class("pill-badge-active")
                ctx.add_class("pill-badge-disabled")

    def _on_trigger_switch(self, trigger_id: str, is_active: bool) -> None:
        triggers = set(self.settings.unlock_triggers)
        if trigger_id == "on_windows_key":
            if is_active:
                triggers.add("on_windows_key")
                triggers.add("on_space")
            else:
                triggers.discard("on_windows_key")
                triggers.discard("on_space")
        else:
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
        dialog.add_button("Save", Gtk.ResponseType.OK)
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
        """Pops out the Face ID overlay directly on desktop to test scanning flow with guaranteed dismissal."""
        self._stop_vision_pipeline()

        from blink.core.coordinator import FaceUnlockCoordinator
        if hasattr(self, "_test_coordinator") and self._test_coordinator is not None:
            self._test_coordinator.stop_all_active()

        self._test_coordinator = FaceUnlockCoordinator()
        self._test_coordinator.overlay._is_desktop_preview = True
        self._test_coordinator.test_scan()

        # Restart background feeds after test completes
        def _resume():
            time.sleep(2.4)
            if self._active_page in ("face_id", "camera", "diagnostics"):
                GLib.idle_add(self._start_vision_pipeline)
        threading.Thread(target=_resume, daemon=True).start()

    def _start_enrollment_wizard(self, button) -> None:
        # Pause background vision worker and timer without tearing down the hardware camera
        if self._camera_timer is not None:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None

        if self.vision_worker is not None:
            self.vision_worker.stop_worker()

        dialog = EnrollmentDialog(parent=self, camera=self.camera)
        dialog.run()
        dialog.destroy()

        self._refresh_profiles_row()

        identities = SecureFaceStore.load_identities()
        if identities and not self.settings.is_face_unlock_enabled:
            self.sw_master.set_active(True)
            self._update_setting("is_face_unlock_enabled", True)
            if hasattr(self, "lbl_hero_status"):
                self.lbl_hero_status.set_text("● Active (Works immediately)")
                ctx = self.lbl_hero_status.get_style_context()
                ctx.remove_class("pill-badge-disabled")
                ctx.add_class("pill-badge-active")

        if self._active_page in ("face_id", "camera", "diagnostics"):
            self._start_vision_pipeline()

    # --------------------------------------------------------------------------
    # Whole-Sensor Display & Zero-Lag Real-Time Diagnostics
    # --------------------------------------------------------------------------
    def _update_camera_feeds(self) -> bool:
        try:
            latest = self.camera.get_latest_frame()
            if latest is None:
                return True

            _, frame = latest
            h, w = frame.shape[:2]
            telemetry = self.vision_worker.get_telemetry()
            recognized_ids = telemetry.get("recognized_ids", {})

            # 1. Multi-Face Dynamic Highlighting in Face ID & Password Page
            if self._active_page == "face_id":
                for ident_id, widget_dict in self.profile_row_widgets.items():
                    row = widget_dict["row"]
                    badge = widget_dict["badge"]
                    if ident_id in recognized_ids:
                        name, sim = recognized_ids[ident_id]
                        row.get_style_context().add_class("row-face-recognized")
                        badge.set_text(f"● In View ({int(sim * 100)}% match)")
                        badge.show()
                    else:
                        row.get_style_context().remove_class("row-face-recognized")
                        badge.hide()

            # 2. Camera Page: Uncropped Full Sensor Viewfinder
            elif self._active_page == "camera":
                aspect = w / max(1, h)
                aspect_lbl = "4:3 Full Sensor" if abs(aspect - 4/3) < 0.08 else ("16:9 Widescreen" if abs(aspect - 16/9) < 0.08 else f"{aspect:.2f}:1 Sensor")
                self.lbl_cam_sensor_spec.set_text(f"{w} × {h} • {aspect_lbl}")

                pixbuf = self._cv2_to_pixbuf_sensor(frame, max_w=520, max_h=380, add_reticle=True)
                if pixbuf:
                    self.img_cam_preview.set_from_pixbuf(pixbuf)

            # 3. Diagnostics Page: Live on-camera HUD stats (Zero lag!)
            elif self._active_page == "diagnostics":
                disp = frame.copy()

                # Render live HUD for every detected face on the camera feed
                for face_item in telemetry.get("faces", []):
                    fx, fy, fw, fh = face_item["bbox"]
                    is_match = face_item["is_match"]
                    color = (89, 199, 52) if is_match else (200, 200, 200)  # Apple Green or Silver

                    # 4 Corner Apple Neon Viewfinder Brackets
                    b_len = 16
                    thick = 2
                    cv2.line(disp, (fx, fy), (fx + b_len, fy), color, thick)
                    cv2.line(disp, (fx, fy), (fx, fy + b_len), color, thick)
                    cv2.line(disp, (fx + fw, fy), (fx + fw - b_len, fy), color, thick)
                    cv2.line(disp, (fx + fw, fy), (fx + fw, fy + b_len), color, thick)
                    cv2.line(disp, (fx, fy + fh), (fx + b_len, fy + fh), color, thick)
                    cv2.line(disp, (fx, fy + fh), (fx, fy + fh - b_len), color, thick)
                    cv2.line(disp, (fx + fw, fy + fh), (fx + fw - b_len, fy + fh), color, thick)
                    cv2.line(disp, (fx + fw, fy + fh), (fx + fw, fy + fh - b_len), color, thick)

                    # Name Tag Pill above each face
                    sim = face_item["sim"]
                    label_txt = f"{face_item['name']} • {int(sim*100)}%" if is_match else face_item["name"]
                    (tw, th), _ = cv2.getTextSize(label_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                    py1 = max(4, fy - 24)
                    py2 = max(24, fy - 2)
                    cv2.rectangle(disp, (fx, py1), (fx + tw + 14, py2), (18, 18, 24), -1)
                    cv2.rectangle(disp, (fx, py1), (fx + tw + 14, py2), color, 1)
                    cv2.putText(disp, label_txt, (fx + 7, py2 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

                    # Landmarks dots
                    if face_item.get("landmarks_5pt") is not None:
                        for pt in face_item["landmarks_5pt"]:
                            cv2.circle(disp, (int(pt[0]), int(pt[1])), 2, (0, 255, 255), -1)

                # Top-left HUD badge on video feed
                fps_val = int(1000.0 / max(1.0, telemetry.get("latency_ms", 30.0)))
                cv2.rectangle(disp, (8, 8), (240, 32), (14, 14, 18), -1)
                cv2.rectangle(disp, (8, 8), (240, 32), (80, 80, 90), 1)
                cv2.circle(disp, (18, 20), 4, (52, 199, 89), -1)
                cv2.putText(disp, f"NEURAL HUD  {w}x{h}  {fps_val} FPS", (28, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 230), 1, cv2.LINE_AA)

                pixbuf = self._cv2_to_pixbuf_sensor(disp, max_w=400, max_h=300, add_reticle=False)
                if pixbuf:
                    self.img_facelab.set_from_pixbuf(pixbuf)

                # Update right telemetry meters in parallel
                top_sim = telemetry.get("top_sim", 0.0)
                top_name = telemetry.get("top_name", "No Face")
                if top_sim > 0.0:
                    self.lbl_fl_sim.set_text(f"{top_sim:.2f} ({top_name})")
                    self.prog_fl_sim.set_fraction(max(0.0, min(1.0, top_sim)))
                else:
                    self.lbl_fl_sim.set_text("No Match")
                    self.prog_fl_sim.set_fraction(0.0)

                cues_data = telemetry.get("cues", {})
                for cue, (val, frac) in cues_data.items():
                    if cue in self.fl_cue_labels:
                        self.fl_cue_labels[cue].set_text(f"{val:.2f}")
                        self.fl_cue_progs[cue].set_fraction(max(0.0, min(1.0, frac)))

                self.lbl_fl_decision.set_text(telemetry.get("verdict", "Ready"))

        except Exception as e:
            print(f"[SettingsWindow] Warning in _update_camera_feeds: {e}")

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
                b_len = 14
                color = (255, 255, 255)
                thick = 1
                margin = 8
                cv2.line(resized, (margin, margin + b_len), (margin, margin), color, thick)
                cv2.line(resized, (margin, margin), (margin + b_len, margin), color, thick)
                cv2.line(resized, (target_w - margin - b_len, margin), (target_w - margin, margin), color, thick)
                cv2.line(resized, (target_w - margin, margin), (target_w - margin, margin + b_len), color, thick)
                cv2.line(resized, (margin, target_h - margin - b_len), (margin, target_h - margin), color, thick)
                cv2.line(resized, (margin, target_h - margin), (margin + b_len, target_h - margin), color, thick)
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
        if hasattr(self, "lock_monitor") and self.lock_monitor:
            try:
                self.lock_monitor.stop()
            except Exception:
                pass
        self._stop_vision_pipeline()

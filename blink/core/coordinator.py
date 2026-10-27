"""
FaceUnlockCoordinator: Connects face recognition, liveness, screen lock monitoring,
notch overlay, and keystroke injection into a unified unlock pipeline.
"""

from __future__ import annotations

import gc
import subprocess
import threading
import time
from typing import Optional, Dict, Any, List

import cv2
import numpy as np
from gi.repository import GLib

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.cues import LivenessDecisionKind
from blink.system.keystroke_injector import KeystrokeInjector
from blink.system.lock_monitor import LockMonitor
from blink.ui.notch_overlay import NotchOverlayWindow
from blink.vision.aligner import FaceAligner
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector
from blink.vision.embedder import ArcFaceEmbedder


class FaceUnlockCoordinator:
    def __init__(self, overlay: Optional[NotchOverlayWindow] = None):
        self.settings = BlinkSettings.load()
        self.overlay = overlay or NotchOverlayWindow(on_retry_clicked=self.start_scan_cycle)
        self.overlay.on_retry_clicked = self.start_scan_cycle

        self.camera = CameraManager(device_index=self.settings.camera_index)
        self.detector = FaceDetector()
        self.aligner = FaceAligner()
        self.embedder = ArcFaceEmbedder()

        self.lock_monitor = LockMonitor(
            on_locked=self._handle_locked,
            on_unlocked=self._handle_unlocked,
            on_wake=self._handle_wake,
            on_sleep=self._handle_sleep,
            on_activity=self._handle_lockscreen_activity,
        )

        self.scan_generation = 0
        self._scan_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self.has_armed_for_current_lock = False
        self.has_auto_retried_for_current_lock = False
        self.status_message = "Idle"
        self.last_outcome: Optional[str] = None
        KeystrokeInjector.warmup()

    def ensure_overlay(self) -> NotchOverlayWindow:
        if self.overlay is None or getattr(self.overlay, "_is_dead", False):
            if self.overlay is not None:
                try:
                    self.overlay.clean_close()
                except Exception:
                    pass
            self.overlay = NotchOverlayWindow(on_retry_clicked=self.start_scan_cycle)
        return self.overlay

    def start(self) -> None:
        self.lock_monitor.start()
        print("[Coordinator] FaceUnlockCoordinator started and monitoring lock state.")

    def stop(self) -> None:
        self.lock_monitor.stop()
        self.cancel_scan()
        if self.overlay:
            overlay_to_close = self.overlay
            self.overlay = None
            GLib.idle_add(overlay_to_close.clean_close)

    def _handle_locked(self) -> None:
        print("[Coordinator] Detected screen locked.")
        KeystrokeInjector.warmup()
        self.has_armed_for_current_lock = False
        self.has_auto_retried_for_current_lock = False
        self.cancel_scan()
        self.ensure_overlay()
        self._wake_screensaver_dialog()

        # Brief settle loop allowing screensaver window to map
        ss_xid = None
        for _ in range(12):
            time.sleep(0.1)
            ss_xid = NotchOverlayWindow.find_screensaver_window_xid()
            if ss_xid:
                break

        # Attach overlay to screensaver window so it is guaranteed to draw in front
        if ss_xid and self.overlay:
            GLib.idle_add(self.overlay.attach_to_screensaver, ss_xid)

        if "on_lock" in self.settings.unlock_triggers:
            self.arm_and_scan(auto_scan=True)
        else:
            self.arm_and_scan(auto_scan=False)

    def _handle_unlocked(self) -> None:
        print("[Coordinator] Detected screen unlocked.")
        self.has_armed_for_current_lock = False
        self.has_auto_retried_for_current_lock = False
        self.cancel_scan()
        if self.overlay and not getattr(self.overlay, "_is_dead", False):
            overlay_to_dismiss = self.overlay
            self.overlay = None
            GLib.idle_add(overlay_to_dismiss.dismiss_and_close)

    def _handle_wake(self) -> None:
        print("[Coordinator] Detected display wake.")
        self.has_armed_for_current_lock = False
        self.cancel_scan()
        self.ensure_overlay()
        self._wake_screensaver_dialog()

        ss_xid = None
        for _ in range(12):
            time.sleep(0.1)
            ss_xid = NotchOverlayWindow.find_screensaver_window_xid()
            if ss_xid:
                break

        if ss_xid and self.overlay:
            GLib.idle_add(self.overlay.attach_to_screensaver, ss_xid)

        if "on_wake" in self.settings.unlock_triggers:
            self.arm_and_scan(auto_scan=True)
        else:
            self.arm_and_scan(auto_scan=False)

    def _wake_screensaver_dialog(self) -> None:
        """Simulates activity on screensaver D-Bus to wake and focus the password prompt."""
        try:
            import pydbus
            bus = pydbus.SessionBus()
            for service, path in [
                ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver"),
                ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
            ]:
                try:
                    obj = bus.get(service, path)
                    if hasattr(obj, "SimulateUserActivity"):
                        obj.SimulateUserActivity()
                        break
                except Exception:
                    continue
        except Exception:
            pass

    def _handle_sleep(self) -> None:
        self.cancel_scan()

    def _handle_lockscreen_activity(self) -> None:
        """Called when user activity (mouse/keyboard) is detected on an already locked screen."""
        if not LockMonitor.is_screen_actually_locked():
            return
        if self.camera.is_running:
            return
        print("[Coordinator] Detected user activity on locked screen. Triggering scan...")
        self.ensure_overlay()
        self._wake_screensaver_dialog()

        ss_xid = None
        for _ in range(6):
            ss_xid = NotchOverlayWindow.find_screensaver_window_xid()
            if ss_xid:
                break
            time.sleep(0.05)

        if ss_xid and self.overlay:
            GLib.idle_add(self.overlay.attach_to_screensaver, ss_xid)

        self.arm_and_scan(auto_scan=True)

    def arm_and_scan(self, auto_scan: bool = True) -> None:
        if not self.settings.is_face_unlock_enabled:
            return

        if not SecureCredentialManager.has_stored_password():
            self.status_message = "No stored password."
            print("[Coordinator] Screen locked, but no password is stored.")
            if self.overlay:
                GLib.idle_add(self.overlay.show_notice, "Face ID", "⚠️ Set Password in Blink Settings", 2500)
            return

        identities = SecureFaceStore.load_identities()
        active_identities = [ident for ident in identities if ident.get("is_enabled", True)]
        if not active_identities:
            self.status_message = "No enrolled face identities."
            print("[Coordinator] Screen locked, but no faces are enrolled.")
            if self.overlay:
                GLib.idle_add(self.overlay.show_notice, "Face ID", "⚠️ No Face Enrolled — Open Blink", 2500)
            return

        self.has_armed_for_current_lock = True
        if self.settings.show_unlock_animation and self.overlay:
            GLib.idle_add(self.overlay.arm)

        if auto_scan:
            time.sleep(0.15)
            self.start_scan_cycle()

    def cancel_scan(self) -> None:
        with self._lock:
            self.scan_generation += 1
        self.camera.stop()

    def start_scan_cycle(self, force: bool = False) -> None:
        with self._lock:
            self.scan_generation += 1
            gen = self.scan_generation

        self._scan_thread = threading.Thread(
            target=self._run_scan_cycle,
            args=(gen, force),
            daemon=True,
            name=f"BlinkScanCycle-{gen}"
        )
        self._scan_thread.start()

    def test_scan(self) -> None:
        """Demonstration test scan executed on desktop without locking screen."""
        self.start_scan_cycle(force=True)

    def _run_scan_cycle(self, generation: int, force: bool = False) -> None:
        if not force and not LockMonitor.is_screen_actually_locked():
            return

        print(f"[Coordinator] Starting scan cycle (generation {generation}, force={force})...")
        if self.settings.show_unlock_animation and self.overlay:
            GLib.idle_add(self.overlay.begin_scanning)

        started = self.camera.start(device_index=self.settings.camera_index)
        if not started:
            for attempt in range(3):
                time.sleep(0.25)
                if self.camera.start(device_index=self.settings.camera_index):
                    started = True
                    break

        if not started:
            self.status_message = "Camera unavailable."
            if self.overlay:
                GLib.idle_add(self.overlay.finish_failure, "Camera unavailable", "Face ID")
            return

        liveness = LivenessAnalyzer(
            mode=self.settings.liveness_mode,
            window_seconds=2.0
        )
        identities = SecureFaceStore.load_identities()
        active_identities = [ident for ident in identities if ident.get("is_enabled", True)]

        if not active_identities:
            self.status_message = "No enrolled face identities."
            print("[Coordinator] No active enrolled identities found.")
            self.camera.stop()
            if self.overlay:
                GLib.idle_add(self.overlay.finish_failure, "No face enrolled", "Face ID")
            return

        cycle_start_time = time.time()
        deadline = cycle_start_time + float(self.settings.face_detection_seconds)
        last_processed_frame_id = -1
        prev_bbox = None
        has_seen_any_face = False
        wrong_face_started_at = None
        wrong_face_grace_seconds = 1.8
        liveness_confirmed = not self.settings.liveness_checks_enabled
        low_light_frame_count = 0

        scan_outcome = "no_resolution"

        while time.time() < deadline:
            with self._lock:
                if generation != self.scan_generation:
                    self.camera.stop()
                    return

            if not force and not LockMonitor.is_screen_actually_locked():
                scan_outcome = "cancelled"
                break

            latest = self.camera.get_latest_frame()
            if latest is None or latest[0] == last_processed_frame_id:
                time.sleep(0.015)
                continue

            frame_id, frame_bgr = latest
            last_processed_frame_id = frame_id

            # 1. Low-Light Detection (ambient brightness threshold)
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            brightness = float(np.mean(gray))
            if brightness < 26.0:
                low_light_frame_count += 1
                if low_light_frame_count >= 5:
                    print(f"[Coordinator] Ambient light too low (level: {brightness:.1f} < 26.0).")
                    scan_outcome = "low_light"
                    break
            else:
                low_light_frame_count = 0

            # 2. Face Detection
            frame_h, frame_w = frame_bgr.shape[:2]
            faces = self.detector.detect_faces(frame_bgr, compute_106_landmarks=True)
            dominant = FaceDetector.select_dominant_face(
                faces,
                frame_width=frame_w,
                previous_bbox=prev_bbox,
                min_width_fraction=0.03
            )

            if dominant is None:
                wrong_face_started_at = None
                prev_bbox = None
                # Smart 2-second timeout: If no face has been seen within 2.0 seconds, go to sleep
                if not has_seen_any_face and (time.time() - cycle_start_time) >= 2.0:
                    print("[Coordinator] No face detected within 2.0s window. Going to sleep.")
                    scan_outcome = "no_face_timeout"
                    break
                time.sleep(0.02)
                continue
            else:
                has_seen_any_face = True

            prev_bbox = dominant.bbox
            aligned_face = self.aligner.align(dominant, frame_bgr)
            if aligned_face is None:
                continue

            # Extract face crop for glare check
            x, y, w, h = dominant.bbox
            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(frame_w, x + w), min(frame_h, y + h)
            face_crop = frame_bgr[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None

            # Report live scan progress to Dynamic Island
            scan_duration = float(self.settings.face_detection_seconds)
            progress = min(0.95, (scan_duration - max(0.0, deadline - time.time())) / max(scan_duration, 1.0))
            if self.overlay:
                status_msg = "Verifying identity…" if liveness_confirmed else "Scanning your face…"
                GLib.idle_add(self.overlay.set_progress, progress, status_msg)

            # Liveness evaluation
            if self.settings.liveness_checks_enabled:
                decision = liveness.observe(frame_bgr, dominant, face_crop)
                if decision.kind == LivenessDecisionKind.DENIED:
                    print(f"[Coordinator] Liveness DENIED: {decision.reason}")
                    scan_outcome = "spoof_suspected"
                    break
                elif decision.kind == LivenessDecisionKind.CONFIRMED:
                    liveness_confirmed = True

            # Recognition match scoring
            emb = self.embedder.compute_embedding(aligned_face)
            scored = self.embedder.score_identities(emb, active_identities)
            matched = self.embedder.best_match(scored, self.settings.match_threshold)

            if matched:
                wrong_face_started_at = None
                top_identity, centroid_sim, max_sim = matched
                if liveness_confirmed:
                    # Match + Liveness Verified!
                    print(f"[Coordinator] Match confirmed: {top_identity['name']} (sim {centroid_sim:.3f}). Unlocking...")
                    self.status_message = f"Matched {top_identity['name']}"
                    self.last_outcome = f"Matched {top_identity['name']} at {centroid_sim:.3f}"

                    # Trigger unlock animation on overlay card
                    if self.settings.show_unlock_animation and self.overlay and not getattr(self.overlay, "_is_dead", False):
                        GLib.idle_add(self.overlay.finish_success)

                    # Allow the unlock animation to pop up and visibly unlatch (~0.50s)
                    time.sleep(0.50)

                    # Instantly type password the microsecond after the unlock animation blossoms
                    if LockMonitor.is_screen_actually_locked():
                        password = SecureCredentialManager.read_password()
                        if password:
                            KeystrokeInjector.type_password(password)
                    elif force:
                        # For desktop preview test-scan, allow user to admire the completed animation
                        time.sleep(0.70)

                    scan_outcome = "matched"
                    break
            else:
                if wrong_face_started_at is None:
                    wrong_face_started_at = time.time()
                elif (time.time() - wrong_face_started_at) >= wrong_face_grace_seconds:
                    scan_outcome = "consistently_wrong_face"
                    break

            time.sleep(0.015)

        self.camera.stop()
        gc.collect()

        with self._lock:
            if generation != self.scan_generation:
                return

        # Resolve outcome in UI
        if self.settings.show_unlock_animation and self.overlay and not getattr(self.overlay, "_is_dead", False):
            if scan_outcome == "matched":
                GLib.idle_add(self.overlay.finish_success)
            elif scan_outcome == "low_light":
                GLib.idle_add(self.overlay.finish_failure, "Enter Password", "Low Light", 2200)
            elif scan_outcome == "no_face_timeout":
                GLib.idle_add(self.overlay.finish_failure, "Enter Password", "No Face Detected", 2000)
            elif scan_outcome == "spoof_suspected":
                GLib.idle_add(self.overlay.finish_failure, "Enter Password", "Spoof Rejected", 2000)
            elif scan_outcome == "consistently_wrong_face":
                GLib.idle_add(self.overlay.finish_failure, "Enter Password", "Face Not Recognized", 2000)
            elif scan_outcome == "no_resolution":
                GLib.idle_add(self.overlay.finish_failure, "Enter Password", "No Face Detected", 2000)

        # If scan ended without finding a face or due to light, allow future user activity on lockscreen to arm again
        if scan_outcome in ("no_face_timeout", "low_light", "no_resolution"):
            self.has_armed_for_current_lock = False

        if scan_outcome in ("spoof_suspected", "consistently_wrong_face"):
            self._schedule_auto_retry()

    def _schedule_auto_retry(self) -> None:
        if self.settings.auto_retry_once and not self.has_auto_retried_for_current_lock:
            self.has_auto_retried_for_current_lock = True
            time.sleep(1.5)
            if LockMonitor.is_screen_actually_locked():
                self.start_scan_cycle()

"""
Settings, Multi-Identity Face Enrollment, and Face Lab Debug GUI for Blink on Linux.
"""

from __future__ import annotations

import math
import os
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

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.ui.enrollment_dialog import EnrollmentDialog
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector, DetectedFace
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder
from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.cues import LivenessCue, LivenessDecisionKind


class SettingsWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Blink - Face ID Settings")
        self.set_default_size(880, 680)
        self.set_position(Gtk.WindowPosition.CENTER)

        icon_path = os.path.join(os.path.dirname(__file__), "..", "resources", "appicon.png")
        if os.path.exists(icon_path):
            try:
                self.set_icon_from_file(icon_path)
            except Exception:
                pass

        self.settings = BlinkSettings.load()
        self.camera = CameraManager(device_index=self.settings.camera_index)
        self.detector = FaceDetector()
        self.aligner = FaceAligner()
        self.embedder = ArcFaceEmbedder()
        self.liveness = LivenessAnalyzer(mode=self.settings.liveness_mode)

        self._active_tab = "General"
        self._camera_timer: Optional[int] = None
        self._enrollment_step: Optional[str] = None
        self._enrollment_samples: List[Dict[str, Any]] = []

        # Build Notebook (Tabs)
        self.notebook = Gtk.Notebook()
        self.add(self.notebook)

        self.notebook.append_page(self._build_general_tab(), Gtk.Label(label="General"))
        self.notebook.append_page(self._build_faces_tab(), Gtk.Label(label="Your Face"))
        self.notebook.append_page(self._build_password_tab(), Gtk.Label(label="Password"))
        self.notebook.append_page(self._build_camera_tab(), Gtk.Label(label="Camera"))
        self.notebook.append_page(self._build_liveness_tab(), Gtk.Label(label="Liveness"))
        self.notebook.append_page(self._build_facelab_tab(), Gtk.Label(label="Face Lab"))

        self.notebook.connect("switch-page", self._on_tab_switched)
        self.connect("destroy", self._on_destroy)

    # --------------------------------------------------------------------------
    # Tab 1: General Settings
    # --------------------------------------------------------------------------
    def _build_general_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_border_width(24)

        # Main Switch
        h_main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_main = Gtk.Label(label="Enable Face Unlock")
        lbl_main.set_xalign(0)
        self.sw_main = Gtk.Switch()
        has_faces = bool(SecureFaceStore.load_identities())
        self.sw_main.set_active(self.settings.is_face_unlock_enabled and has_faces)
        self.sw_main.connect("notify::active", self._on_toggle_face_unlock)
        h_main.pack_start(lbl_main, True, True, 0)
        h_main.pack_end(self.sw_main, False, False, 0)
        vbox.pack_start(h_main, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Triggers
        lbl_triggers = Gtk.Label(label="Unlock Triggers:")
        lbl_triggers.set_xalign(0)
        vbox.pack_start(lbl_triggers, False, False, 0)

        for trigger_id, label in [("on_wake", "On Wake (screen turned on / lid opened)"),
                                   ("on_lock", "On Lock (immediate scan upon screen lock)"),
                                   ("on_space", "On Space (when space key is pressed at lockscreen)")]:
            chk = Gtk.CheckButton(label=label)
            chk.set_active(trigger_id in self.settings.unlock_triggers)
            chk.connect("toggled", self._on_trigger_toggled, trigger_id)
            vbox.pack_start(chk, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Auto-retry
        chk_retry = Gtk.CheckButton(label="Auto-retry scan once on failed recognition")
        chk_retry.set_active(self.settings.auto_retry_once)
        chk_retry.connect("toggled", lambda w: self._update_setting("auto_retry_once", w.get_active()))
        vbox.pack_start(chk_retry, False, False, 0)

        # Overlay animation
        chk_anim = Gtk.CheckButton(label="Show Dynamic Island / Notch floating animation")
        chk_anim.set_active(self.settings.show_unlock_animation)
        chk_anim.connect("toggled", lambda w: self._update_setting("show_unlock_animation", w.get_active()))
        vbox.pack_start(chk_anim, False, False, 0)

        # Timeout slider
        lbl_timeout = Gtk.Label(label=f"Scanning window duration: {self.settings.face_detection_seconds}s")
        lbl_timeout.set_xalign(0)
        scale_timeout = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 3, 10, 1)
        scale_timeout.set_value(self.settings.face_detection_seconds)
        scale_timeout.connect("value-changed", lambda w: self._update_timeout(w, lbl_timeout))
        vbox.pack_start(lbl_timeout, False, False, 0)
        vbox.pack_start(scale_timeout, False, False, 0)

        # Match threshold slider
        lbl_thresh = Gtk.Label(label=f"Similarity threshold: {self.settings.match_threshold:.2f}")
        lbl_thresh.set_xalign(0)
        scale_thresh = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.40, 0.85, 0.01)
        scale_thresh.set_value(self.settings.match_threshold)
        scale_thresh.connect("value-changed", lambda w: self._update_thresh(w, lbl_thresh))
        vbox.pack_start(lbl_thresh, False, False, 0)
        vbox.pack_start(scale_thresh, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Live Test Scan Preview
        btn_test_island = Gtk.Button(label="Preview Face ID Card & Animations (Test Scan on Desktop)")
        btn_test_island.connect("clicked", self._on_test_dynamic_island)
        vbox.pack_start(btn_test_island, False, False, 0)

        return vbox

    def _on_test_dynamic_island(self, button) -> None:
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

        from blink.core.coordinator import FaceUnlockCoordinator
        if not hasattr(self, "_test_coordinator") or self._test_coordinator is None:
            self._test_coordinator = FaceUnlockCoordinator()
        self._test_coordinator.overlay._is_desktop_preview = True
        self._test_coordinator.test_scan()

    # --------------------------------------------------------------------------
    # Tab 2: Your Face (Enrollment & Multi-Identity)
    # --------------------------------------------------------------------------
    def _build_faces_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_border_width(24)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_info = Gtk.Label(label="Manage enrolled faces or enroll a new profile.")
        lbl_info.set_xalign(0)
        btn_enroll = Gtk.Button(label="Enroll New Face")
        btn_enroll.connect("clicked", self._start_enrollment_wizard)
        header.pack_start(lbl_info, True, True, 0)
        header.pack_end(btn_enroll, False, False, 0)
        vbox.pack_start(header, False, False, 0)

        # List of faces
        self.faces_listbox = Gtk.ListBox()
        self.faces_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(250)
        scroll.add(self.faces_listbox)
        vbox.pack_start(scroll, True, True, 0)

        self._refresh_faces_list()
        return vbox

    def _refresh_faces_list(self) -> None:
        for child in self.faces_listbox.get_children():
            self.faces_listbox.remove(child)

        identities = SecureFaceStore.load_identities()
        if not identities:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label="No faces enrolled yet. Click 'Enroll New Face' above to get started.")
            lbl.set_margin_top(16)
            lbl.set_margin_bottom(16)
            row.add(lbl)
            self.faces_listbox.add(row)
        else:
            for ident in identities:
                row = Gtk.ListBoxRow()
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                hbox.set_margin_top(8)
                hbox.set_margin_bottom(8)
                hbox.set_margin_left(12)
                hbox.set_margin_right(12)

                lbl_name = Gtk.Label(label=f"<b>{ident['name']}</b> ({len(ident.get('samples', []))} poses)")
                lbl_name.set_use_markup(True)
                lbl_name.set_xalign(0)

                sw = Gtk.Switch()
                sw.set_active(ident.get("is_enabled", True))
                sw.connect("notify::active", self._on_toggle_identity, ident["id"])

                btn_del = Gtk.Button(label="Delete")
                btn_del.connect("clicked", self._on_delete_identity, ident["id"])

                hbox.pack_start(lbl_name, True, True, 0)
                hbox.pack_end(btn_del, False, False, 0)
                hbox.pack_end(sw, False, False, 0)
                row.add(hbox)
                self.faces_listbox.add(row)

        self.faces_listbox.show_all()

        has_faces = bool(identities)
        if hasattr(self, "sw_main"):
            if not has_faces:
                self.sw_main.set_active(False)
            else:
                self.sw_main.set_active(self.settings.is_face_unlock_enabled)

    # --------------------------------------------------------------------------
    # Tab 3: Password & Session
    # --------------------------------------------------------------------------
    def _build_password_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_border_width(24)

        has_pwd = SecureCredentialManager.has_stored_password()
        self.lbl_pwd_status = Gtk.Label(
            label=f"<b>Password Status:</b> {'Stored &amp; Encrypted' if has_pwd else 'Not Stored'}"
        )
        self.lbl_pwd_status.set_use_markup(True)
        self.lbl_pwd_status.set_xalign(0)
        vbox.pack_start(self.lbl_pwd_status, False, False, 0)

        # Entry for password
        h_entry = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.entry_pwd = Gtk.Entry()
        self.entry_pwd.set_visibility(False)
        self.entry_pwd.set_placeholder_text("Enter your Linux user password")
        btn_save = Gtk.Button(label="Save Password")
        btn_save.connect("clicked", self._on_save_password)
        h_entry.pack_start(self.entry_pwd, True, True, 0)
        h_entry.pack_end(btn_save, False, False, 0)
        vbox.pack_start(h_entry, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # Auto-lock timeout
        h_lock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_lock = Gtk.Label(label="Auto-lock authorized session after idle period:")
        lbl_lock.set_xalign(0)
        combo_lock = Gtk.ComboBoxText()
        combo_lock.append("5", "5 minutes")
        combo_lock.append("15", "15 minutes")
        combo_lock.append("30", "30 minutes")
        combo_lock.append("60", "1 hour")
        combo_lock.set_active_id(str(self.settings.auto_lock_idle_minutes))
        combo_lock.connect("changed", self._on_combo_idle_changed)
        h_lock.pack_start(lbl_lock, True, True, 0)
        h_lock.pack_end(combo_lock, False, False, 0)
        vbox.pack_start(h_lock, False, False, 0)

        # Delete password
        btn_remove = Gtk.Button(label="Remove Stored Password")
        btn_remove.connect("clicked", self._on_remove_password)
        vbox.pack_start(btn_remove, False, False, 0)

        return vbox

    # --------------------------------------------------------------------------
    # Tab 4: Camera Settings
    # --------------------------------------------------------------------------
    def _build_camera_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_border_width(24)

        h_dev = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_dev = Gtk.Label(label="Select Camera Device:")
        lbl_dev.set_xalign(0)
        self.combo_cam = Gtk.ComboBoxText()
        devices = CameraManager.list_devices()
        for idx in devices:
            self.combo_cam.append(str(idx), f"/dev/video{idx}")
        self.combo_cam.set_active_id(str(self.settings.camera_index))
        self.combo_cam.connect("changed", self._on_camera_device_changed)
        h_dev.pack_start(lbl_dev, True, True, 0)
        h_dev.pack_end(self.combo_cam, False, False, 0)
        vbox.pack_start(h_dev, False, False, 0)

        # Camera preview image
        self.img_cam_preview = Gtk.Image()
        self.img_cam_preview.set_size_request(480, 360)
        vbox.pack_start(self.img_cam_preview, True, True, 0)

        return vbox

    # --------------------------------------------------------------------------
    # Tab 5: Liveness Settings
    # --------------------------------------------------------------------------
    def _build_liveness_tab(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.set_border_width(24)

        h_live = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_live = Gtk.Label(label="Enable Anti-Spoofing Liveness Checks")
        lbl_live.set_xalign(0)
        sw_live = Gtk.Switch()
        sw_live.set_active(self.settings.liveness_checks_enabled)
        sw_live.connect("notify::active", lambda w, p: self._update_setting("liveness_checks_enabled", w.get_active()))
        h_live.pack_start(lbl_live, True, True, 0)
        h_live.pack_end(sw_live, False, False, 0)
        vbox.pack_start(h_live, False, False, 0)

        # Liveness Mode Radio Buttons
        lbl_mode = Gtk.Label(label="Liveness Strictness Mode:")
        lbl_mode.set_xalign(0)
        vbox.pack_start(lbl_mode, False, False, 0)

        rb_light = Gtk.RadioButton.new_with_label(None, "Light (Deny-only: only rejects obvious screens/photos)")
        rb_heavy = Gtk.RadioButton.new_with_label_from_widget(rb_light, "Heavy (Deny cues + requires proof of life: blink/3D motion)")
        if self.settings.liveness_mode == "heavy":
            rb_heavy.set_active(True)
        else:
            rb_light.set_active(True)
        rb_light.connect("toggled", lambda w: self._on_liveness_mode_changed("light") if w.get_active() else None)
        rb_heavy.connect("toggled", lambda w: self._on_liveness_mode_changed("heavy") if w.get_active() else None)
        vbox.pack_start(rb_light, False, False, 0)
        vbox.pack_start(rb_heavy, False, False, 0)

        return vbox

    # --------------------------------------------------------------------------
    # Tab 6: Face Lab (Real-Time Debug Console)
    # --------------------------------------------------------------------------
    def _build_facelab_tab(self) -> Gtk.Widget:
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hbox.set_border_width(16)

        # Left: Live Camera View with Landmark Overlay
        self.img_facelab = Gtk.Image()
        self.img_facelab.set_size_request(480, 360)
        hbox.pack_start(self.img_facelab, True, True, 0)

        # Right: Real-time telemetry panels
        v_telemetry = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v_telemetry.set_size_request(320, -1)

        lbl_head = Gtk.Label(label="<b>Face Lab Telemetry</b>")
        lbl_head.set_use_markup(True)
        lbl_head.set_xalign(0)
        v_telemetry.pack_start(lbl_head, False, False, 0)

        self.lbl_fl_sim = Gtk.Label(label="Similarity: --")
        self.lbl_fl_sim.set_xalign(0)
        self.prog_fl_sim = Gtk.ProgressBar()
        v_telemetry.pack_start(self.lbl_fl_sim, False, False, 0)
        v_telemetry.pack_start(self.prog_fl_sim, False, False, 0)

        # Liveness cues bars
        self.fl_cue_labels = {}
        self.fl_cue_progs = {}
        for cue in LivenessCue:
            lbl = Gtk.Label(label=f"{cue.title}: --")
            lbl.set_xalign(0)
            prog = Gtk.ProgressBar()
            v_telemetry.pack_start(lbl, False, False, 0)
            v_telemetry.pack_start(prog, False, False, 0)
            self.fl_cue_labels[cue] = lbl
            self.fl_cue_progs[cue] = prog

        self.lbl_fl_decision = Gtk.Label(label="Liveness Decision: --")
        self.lbl_fl_decision.set_xalign(0)
        v_telemetry.pack_start(self.lbl_fl_decision, False, False, 0)

        hbox.pack_start(v_telemetry, False, False, 0)
        return hbox

    # --------------------------------------------------------------------------
    # Enrollment Wizard Dialog (iOS Face ID style auto-capture)
    # --------------------------------------------------------------------------
    def _start_enrollment_wizard(self, button) -> None:
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

        dialog = EnrollmentDialog(parent=self, camera=self.camera)
        dialog.run()
        dialog.destroy()

        self._refresh_faces_list()

        if self._active_tab in ("Camera", "Face Lab"):
            self.camera.start(device_index=self.settings.camera_index)
            self._camera_timer = GLib.timeout_add(33, self._update_camera_tab_feed)

    # --------------------------------------------------------------------------
    # Tab Switching & Feed Loop
    # --------------------------------------------------------------------------
    def _on_tab_switched(self, notebook, page, page_num):
        tab_names = ["General", "Your Face", "Password", "Camera", "Liveness", "Face Lab"]
        self._active_tab = tab_names[page_num]

        if self._active_tab in ("Camera", "Face Lab"):
            if not self.camera._running:
                self.camera.start(device_index=self.settings.camera_index)
            if self._camera_timer is None:
                self._camera_timer = GLib.timeout_add(33, self._update_camera_tab_feed)
        else:
            if self._camera_timer is not None:
                GLib.source_remove(self._camera_timer)
                self._camera_timer = None
            self.camera.stop()

    def _update_camera_tab_feed(self) -> bool:
        latest = self.camera.get_latest_frame()
        if latest is None:
            return True

        _, frame = latest
        h, w = frame.shape[:2]

        if self._active_tab == "Camera":
            pixbuf = self._cv2_to_pixbuf(frame, 480, 360)
            if pixbuf:
                self.img_cam_preview.set_from_pixbuf(pixbuf)

        elif self._active_tab == "Face Lab":
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

                    self.lbl_fl_decision.set_text(f"Decision: {decision.kind.value.upper()}")

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

    # --------------------------------------------------------------------------
    # Settings callbacks
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
            dialog.format_secondary_text("Please enroll your face in the 'Your Face' tab before enabling Face Unlock.")
            dialog.run()
            dialog.destroy()
            return
        self._update_setting("is_face_unlock_enabled", switch.get_active())

    def _on_trigger_toggled(self, button, trigger_id: str):
        triggers = set(self.settings.unlock_triggers)
        if button.get_active():
            triggers.add(trigger_id)
        else:
            triggers.discard(trigger_id)
        self._update_setting("unlock_triggers", list(triggers))

    def _update_timeout(self, scale, label):
        val = int(scale.get_value())
        label.set_text(f"Scanning window duration: {val}s")
        self._update_setting("face_detection_seconds", val)

    def _update_thresh(self, scale, label):
        val = float(scale.get_value())
        label.set_text(f"Similarity threshold: {val:.2f}")
        self._update_setting("match_threshold", val)

    def _on_combo_idle_changed(self, combo):
        active_id = combo.get_active_id()
        if active_id:
            self._update_setting("auto_lock_idle_minutes", int(active_id))

    def _on_camera_device_changed(self, combo):
        active_id = combo.get_active_id()
        if active_id:
            idx = int(active_id)
            self._update_setting("camera_index", idx)
            self.camera.start(device_index=idx)

    def _on_liveness_mode_changed(self, mode: str):
        self._update_setting("liveness_mode", mode)
        self.liveness.mode = mode

    def _on_save_password(self, button):
        pwd = self.entry_pwd.get_text()
        if not pwd:
            return
        SecureCredentialManager.save_password(pwd)
        self.entry_pwd.set_text("")
        self.lbl_pwd_status.set_markup("<b>Password Status:</b> Stored &amp; Encrypted")

    def _on_remove_password(self, button):
        SecureCredentialManager.remove_password()
        self.lbl_pwd_status.set_markup("<b>Password Status:</b> Not Stored")

    def _on_toggle_identity(self, switch, gparam, ident_id: str):
        identities = SecureFaceStore.load_identities()
        for ident in identities:
            if ident["id"] == ident_id:
                ident["is_enabled"] = switch.get_active()
        SecureFaceStore.save_identities(identities)

    def _on_delete_identity(self, button, ident_id: str):
        identities = SecureFaceStore.load_identities()
        identities = [ident for ident in identities if ident["id"] != ident_id]
        SecureFaceStore.save_identities(identities)
        self._refresh_faces_list()

    def _on_destroy(self, window):
        if self._camera_timer:
            GLib.source_remove(self._camera_timer)
            self._camera_timer = None
        self.camera.stop()

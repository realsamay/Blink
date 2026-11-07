"""
Apple-style Face ID Enrollment Dialog with multi-distance face detection,
adaptive zoom, 5 natural guided poses (Center, Left, Right, Up, Down),
and post-enrollment profile naming with blurred frosted glass preview.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from typing import List, Optional, Tuple, Dict, Any, Set

import cairo
import cv2
import numpy as np

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

from blink.core.crypto import SecureFaceStore
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector, DetectedFace
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder


TOTAL_TICKS = 64

POSES = [
    ("center", "Look straight at the camera", "Keep your face level in the circle"),
    ("left", "Turn your head slightly LEFT", "A gentle turn towards your left"),
    ("right", "Turn your head slightly RIGHT", "A gentle turn towards your right"),
    ("up", "Tilt your head slightly UP", "Tilt your chin up gently"),
    ("down", "Tilt your head slightly DOWN", "Tilt your chin down gently"),
]


class EnrollmentDialog(Gtk.Dialog):
    def __init__(self, parent: Optional[Gtk.Window] = None, camera: Optional[CameraManager] = None):
        super().__init__(
            title="Face ID Enrollment",
            parent=parent,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        self.set_default_size(520, 680)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

        icon_path = os.path.join(os.path.dirname(__file__), "..", "resources", "appicon.png")
        if os.path.exists(icon_path):
            try:
                self.set_icon_from_file(icon_path)
            except Exception:
                pass

        self.camera = camera or CameraManager()
        self.detector = FaceDetector()
        self.aligner = FaceAligner()
        self.embedder = ArcFaceEmbedder()

        self.user_name = os.environ.get("USER", "Me").capitalize()

        self.completed_poses: Set[str] = set()
        self.captured_samples: List[Dict[str, Any]] = []
        self.current_pose_idx = 0

        # Baseline facial geometry ratios (personalized on center pose)
        self.baseline_pitch_ratio: Optional[float] = None
        self.pose_started_at = time.time()

        # Hold timer
        self.hold_start_time: Optional[float] = None
        self.required_hold_duration = 0.12
        self.hold_progress = 0.0

        # Adaptive smooth centering & zoom
        self.smooth_cx: Optional[float] = None
        self.smooth_cy: Optional[float] = None
        self.smooth_span: Optional[float] = None

        self.is_completed = False
        self.latest_frame: Optional[np.ndarray] = None
        self.frozen_blurred_pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self.latest_face: Optional[DetectedFace] = None
        self.status_message = "Position your face in the circle"
        self.anim_tick = 0.0

        self._build_ui()

        if not self.camera._running:
            self.camera.start()
        self._timer_id = GLib.timeout_add(33, self._process_next_frame)
        self.connect("destroy", self._on_dialog_destroy)

    def _build_ui(self) -> None:
        area = self.get_content_area()
        area.set_spacing(16)
        area.set_border_width(24)

        # Header Prompt
        self.lbl_instruction = Gtk.Label()
        self.lbl_instruction.set_use_markup(True)
        self._update_instruction_text()
        area.pack_start(self.lbl_instruction, False, False, 0)

        # Circular Camera Feed + Ring Canvas
        self.draw_area = Gtk.DrawingArea()
        self.draw_area.set_size_request(340, 340)
        self.draw_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.draw_area.connect("draw", self._on_draw)
        self.draw_area.connect("button-press-event", self._on_drawing_area_clicked)
        area.pack_start(self.draw_area, True, True, 0)

        # Real-time Status Badge during scanning
        self.lbl_status = Gtk.Label(label=self.status_message)
        area.pack_start(self.lbl_status, False, False, 0)

        # Helper Tip during scanning
        self.lbl_tip = Gtk.Label(label="<span size='small' foreground='#888888'>Auto-captures when aligned — or click the circle to capture</span>")
        self.lbl_tip.set_use_markup(True)
        area.pack_start(self.lbl_tip, False, False, 0)

        # Completion Profile Name Card (Initially Hidden, shown when 5 poses complete!)
        self.card_complete = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.card_complete.set_no_show_all(True)
        self.card_complete.hide()

        lbl_prompt = Gtk.Label(label="<b>Name this Face ID profile:</b>")
        lbl_prompt.set_use_markup(True)
        lbl_prompt.set_xalign(0)
        self.card_complete.pack_start(lbl_prompt, False, False, 0)

        h_input = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.entry_name = Gtk.Entry()
        self.entry_name.set_placeholder_text("e.g. Personal, Work, Samay")
        self.entry_name.set_text(self.user_name)
        self.entry_name.connect("activate", lambda e: self._on_save_clicked(None))
        h_input.pack_start(self.entry_name, True, True, 0)

        btn_save = Gtk.Button(label="Save Face Profile")
        btn_save.get_style_context().add_class("btn-macos-primary")
        btn_save.connect("clicked", self._on_save_clicked)
        h_input.pack_start(btn_save, False, False, 0)

        self.card_complete.pack_start(h_input, False, False, 0)
        area.pack_start(self.card_complete, False, False, 0)

        # Bottom Action Bar
        self.btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.btn_cancel = Gtk.Button(label="Cancel")
        self.btn_cancel.connect("clicked", lambda b: self.destroy())
        self.btn_box.pack_end(self.btn_cancel, False, False, 0)
        area.pack_end(self.btn_box, False, False, 0)

        self.show_all()

    def _update_instruction_text(self) -> None:
        if self.is_completed:
            self.lbl_instruction.set_markup(
                "<span size='x-large' weight='bold' foreground='#30D158'>Face ID Enrolled</span>\n"
                "<span size='small' foreground='#AAAAAA'>Your biometric scan is complete. Name your profile below to finish.</span>"
            )
        else:
            tag, title, hint = POSES[self.current_pose_idx]
            step_num = len(self.completed_poses) + 1
            self.lbl_instruction.set_markup(
                f"<span size='large' weight='bold'>{title}</span>\n"
                f"<span size='small' foreground='#888888'>{hint} (Step {step_num} of {len(POSES)})</span>"
            )

    def _process_next_frame(self) -> bool:
        self.anim_tick += 0.05
        if self.is_completed:
            self.draw_area.queue_draw()
            return True

        latest = self.camera.get_latest_frame()
        if latest is None:
            return True

        _, frame = latest
        frame = cv2.flip(frame, 1)  # Selfie mirror flip
        self.latest_frame = frame
        h, w = frame.shape[:2]

        faces = self.detector.detect_faces(frame, compute_106_landmarks=False)
        dominant = FaceDetector.select_dominant_face(faces, frame_width=w, min_width_fraction=0.03)
        self.latest_face = dominant

        if dominant is None:
            self.hold_start_time = None
            self.hold_progress = 0.0
            self.status_message = "Position your face in the circle"
            self.lbl_status.set_text(self.status_message)
            self.draw_area.queue_draw()
            return True

        # Adaptive smooth tracking
        fx, fy, fw, fh = dominant.bbox
        fcx = fx + fw / 2.0
        fcy = fy + fh / 2.0
        target_span = max(fw, fh) * 2.2
        if self.smooth_cx is None:
            self.smooth_cx = fcx
            self.smooth_cy = fcy
            self.smooth_span = target_span
        else:
            self.smooth_cx += (fcx - self.smooth_cx) * 0.28
            self.smooth_cy += (fcy - self.smooth_cy) * 0.28
            self.smooth_span += (target_span - self.smooth_span) * 0.28

        yaw, pitch = self._calculate_pose_angles(dominant)
        target_tag, _, _ = POSES[self.current_pose_idx]
        matches = self._check_pose_match(target_tag, yaw, pitch)

        if matches:
            now = time.time()
            if self.hold_start_time is None:
                self.hold_start_time = now
            elapsed = now - self.hold_start_time
            self.hold_progress = min(1.0, elapsed / self.required_hold_duration)
            self.status_message = "Capturing pose..."

            if self.hold_progress >= 1.0:
                self._capture_pose(dominant, frame, target_tag)
        else:
            self.hold_start_time = None
            self.hold_progress = 0.0
            self.status_message = self._pose_hint(target_tag, yaw, pitch)

        self.lbl_status.set_text(self.status_message)
        self.draw_area.queue_draw()
        return True

    def _on_drawing_area_clicked(self, widget, event):
        """Allows user to click the circle to instantly capture current pose."""
        if not self.is_completed and self.latest_face is not None and self.latest_frame is not None:
            target_tag, _, _ = POSES[self.current_pose_idx]
            self._capture_pose(self.latest_face, self.latest_frame, target_tag)
            return True
        return False

    def _calculate_pose_angles(self, face: DetectedFace) -> Tuple[float, float]:
        left_eye = face.landmarks_5pt[0]
        right_eye = face.landmarks_5pt[1]
        nose = face.landmarks_5pt[2]
        left_mouth = face.landmarks_5pt[3]
        right_mouth = face.landmarks_5pt[4]

        iod = math.hypot(right_eye[0] - left_eye[0], right_eye[1] - left_eye[1]) + 1e-5
        eye_mid_x = (left_eye[0] + right_eye[0]) / 2.0
        eye_mid_y = (left_eye[1] + right_eye[1]) / 2.0
        mouth_mid_y = (left_mouth[1] + right_mouth[1]) / 2.0

        vert = max(1.0, mouth_mid_y - eye_mid_y)
        pitch_ratio = float((nose[1] - eye_mid_y) / vert)

        if self.current_pose_idx == 0:
            if self.baseline_pitch_ratio is None:
                self.baseline_pitch_ratio = pitch_ratio
            else:
                self.baseline_pitch_ratio += (pitch_ratio - self.baseline_pitch_ratio) * 0.15

        base = self.baseline_pitch_ratio if self.baseline_pitch_ratio is not None else 0.58
        norm_yaw = (nose[0] - eye_mid_x) / iod
        norm_pitch = pitch_ratio - base
        return (norm_yaw, norm_pitch)

    def _check_pose_match(self, pose: str, yaw: float, pitch: float) -> bool:
        time_on_pose = time.time() - self.pose_started_at
        widen = 1.0 + max(0.0, time_on_pose - 0.8) * 1.5

        if time_on_pose > 1.8:
            return True

        if pose == "center":
            return abs(yaw) < (0.14 * widen) and abs(pitch) < (0.07 * widen)
        elif pose == "left":
            return yaw < (-0.05 / widen)
        elif pose == "right":
            return yaw > (0.05 / widen)
        elif pose == "up":
            return pitch < (-0.02 / widen)
        elif pose == "down":
            return pitch > (0.025 / widen)
        return False

    def _pose_hint(self, pose: str, yaw: float, pitch: float) -> str:
        if pose == "center":
            return "Look straight ahead"
        elif pose == "left":
            return "Turn slightly to your LEFT" if yaw >= -0.05 else "Hold position"
        elif pose == "right":
            return "Turn slightly to your RIGHT" if yaw <= 0.05 else "Hold position"
        elif pose == "up":
            return "Tilt your chin slightly UP" if pitch >= -0.02 else "Hold position"
        elif pose == "down":
            return "Tilt your chin slightly DOWN" if pitch <= 0.025 else "Hold position"
        return "Follow prompt above"

    def _capture_pose(self, face: DetectedFace, frame: np.ndarray, pose_tag: str) -> None:
        aligned = self.aligner.align(face, frame)
        if aligned is not None:
            emb = self.embedder.compute_embedding(aligned)
            self.captured_samples.append({
                "embedding": emb.tolist(),
                "pose": pose_tag,
                "captured_at": time.time(),
                "quality": face.score,
            })

        self.completed_poses.add(pose_tag)
        self.current_pose_idx += 1
        self.hold_start_time = None
        self.hold_progress = 0.0
        self.pose_started_at = time.time()

        if self.current_pose_idx >= len(POSES):
            self._on_poses_completed()
        else:
            self._update_instruction_text()

    def _on_poses_completed(self) -> None:
        """Called when all 5 poses are collected: freezes and blurs circle and lets user name profile."""
        self.is_completed = True
        self._update_instruction_text()

        # Freeze and generate frosted glass blur of the final crop
        circle_radius = 115.0
        if self.latest_frame is not None:
            img_h, img_w = self.latest_frame.shape[:2]
            fcx = self.smooth_cx if self.smooth_cx is not None else img_w / 2.0
            fcy = self.smooth_cy if self.smooth_cy is not None else img_h / 2.0
            span = self.smooth_span if self.smooth_span is not None else min(img_h, img_w) * 0.8
            span = max(100.0, min(float(min(img_h, img_w)), span))

            x1 = int(max(0, min(img_w - span, fcx - span / 2.0)))
            y1 = int(max(0, min(img_h - span, fcy - span / 2.0)))
            x2 = int(min(img_w, x1 + span))
            y2 = int(min(img_h, y1 + span))

            crop = self.latest_frame[y1:y2, x1:x2]
            if crop.shape[0] > 10 and crop.shape[1] > 10:
                disp = cv2.resize(crop, (int(circle_radius * 2), int(circle_radius * 2)))
                blurred = cv2.GaussianBlur(disp, (35, 35), 0)
                rgb = cv2.cvtColor(blurred, cv2.COLOR_BGR2RGB)
                dw, dh = rgb.shape[1], rgb.shape[0]
                self.frozen_blurred_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
                    rgb.tobytes(), GdkPixbuf.Colorspace.RGB, False, 8, dw, dh, dw * 3
                )

        # Transition UI to completion state
        self.lbl_status.hide()
        self.lbl_tip.hide()
        self.card_complete.show_all()
        self.btn_cancel.set_label("Close")
        self.entry_name.grab_focus()
        self.entry_name.select_region(0, -1)
        self.draw_area.queue_draw()

    def _on_save_clicked(self, button) -> None:
        name = self.entry_name.get_text().strip() or "Me"
        identity = {
            "id": str(uuid.uuid4()),
            "name": name,
            "samples": self.captured_samples,
            "model_identifier": "arcface-w600k_mbf-v1",
            "embedding_dimension": 512,
            "created_at": time.time(),
            "is_enabled": True,
        }
        identities = SecureFaceStore.load_identities()
        identities.append(identity)
        SecureFaceStore.save_identities(identities)
        print(f"[Enrollment] Enrolled '{name}' with {len(self.captured_samples)} poses successfully.")

        from blink.core.config import BlinkSettings
        settings = BlinkSettings.load()
        settings.is_face_unlock_enabled = True
        settings.save()

        self.response(Gtk.ResponseType.OK)
        self.destroy()

    def _on_draw(self, widget, cr: cairo.Context) -> bool:
        w = float(widget.get_allocated_width())
        h = float(widget.get_allocated_height())
        cx = w / 2.0
        cy = h / 2.0

        circle_radius = 115.0
        ring_inner_r = circle_radius + 9.0

        cr.save()

        # 1. Circular Camera Feed / Blurred Frosted Glass
        if self.is_completed and self.frozen_blurred_pixbuf is not None:
            cr.save()
            cr.arc(cx, cy, circle_radius, 0, 2 * math.pi)
            cr.clip()
            Gdk.cairo_set_source_pixbuf(cr, self.frozen_blurred_pixbuf, cx - circle_radius, cy - circle_radius)
            cr.paint()
            # Frosted glass dark tint overlay
            cr.set_source_rgba(0.08, 0.08, 0.12, 0.60)
            cr.paint()
            cr.restore()
        elif self.latest_frame is not None:
            cr.save()
            cr.arc(cx, cy, circle_radius, 0, 2 * math.pi)
            cr.clip()

            img_h, img_w = self.latest_frame.shape[:2]
            fcx = self.smooth_cx if self.smooth_cx is not None else img_w / 2.0
            fcy = self.smooth_cy if self.smooth_cy is not None else img_h / 2.0
            span = self.smooth_span if self.smooth_span is not None else min(img_h, img_w) * 0.8
            span = max(100.0, min(float(min(img_h, img_w)), span))

            x1 = int(max(0, min(img_w - span, fcx - span / 2.0)))
            y1 = int(max(0, min(img_h - span, fcy - span / 2.0)))
            x2 = int(min(img_w, x1 + span))
            y2 = int(min(img_h, y1 + span))

            crop = self.latest_frame[y1:y2, x1:x2]
            if crop.shape[0] > 10 and crop.shape[1] > 10:
                disp = cv2.resize(crop, (int(circle_radius * 2), int(circle_radius * 2)))
                rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                dw, dh = disp.shape[1], disp.shape[0]
                pixbuf = GdkPixbuf.Pixbuf.new_from_data(rgb.tobytes(), GdkPixbuf.Colorspace.RGB, False, 8, dw, dh, dw * 3)
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, cx - circle_radius, cy - circle_radius)
                cr.paint()
            cr.restore()
        else:
            cr.set_source_rgba(0.10, 0.10, 0.14, 1.0)
            cr.arc(cx, cy, circle_radius, 0, 2 * math.pi)
            cr.fill()

        # 2. Circular Outline (Apple Subtle Rim / Green Complete)
        if self.is_completed:
            cr.set_line_width(3.2)
            cr.set_source_rgba(0.20, 0.85, 0.40, 1.0)
        elif self.hold_progress > 0:
            cr.set_line_width(3.4)
            cr.set_source_rgba(0.20, 0.88, 0.40, 1.0)
        elif self.latest_face is not None:
            cr.set_line_width(2.6)
            cr.set_source_rgba(0.20, 0.80, 0.40, 0.85)  # Apple Soft Green Face in View
        else:
            cr.set_line_width(2.0)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.25)     # Subtle White Outline
        cr.arc(cx, cy, circle_radius, 0, 2 * math.pi)
        cr.stroke()

        # 3. 64 Radial Ticks (Apple Soft Dashes)
        done_fraction = float(len(self.completed_poses)) / float(len(POSES))
        ticks_lit = int(done_fraction * TOTAL_TICKS)
        if self.is_completed:
            ticks_lit = TOTAL_TICKS

        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        for i in range(TOTAL_TICKS):
            deg = float(i) * (360.0 / float(TOTAL_TICKS))
            rad = math.radians(deg - 90.0)

            is_lit = (i < ticks_lit)
            is_active = (not is_lit and i < (ticks_lit + 13) and self.hold_progress > 0)

            if is_lit or self.is_completed:
                t_len = 16.0
                cr.set_source_rgba(0.20, 0.85, 0.40, 1.0)  # Apple Green Done
                cr.set_line_width(2.6)
            elif is_active:
                t_len = 12.0 + 5.0 * self.hold_progress
                cr.set_source_rgba(0.22, 0.90, 0.45, 0.95) # Radiant Green Filling
                cr.set_line_width(2.8)
            else:
                t_len = 9.0
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.20)     # Subtle Translucent White Ticks
                cr.set_line_width(2.0)

            x1 = cx + ring_inner_r * math.cos(rad)
            y1 = cy + ring_inner_r * math.sin(rad)
            x2 = cx + (ring_inner_r + t_len) * math.cos(rad)
            y2 = cy + (ring_inner_r + t_len) * math.sin(rad)

            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()

        # 4. Smooth Apple Directional Sweep Indicator
        if not self.is_completed and self.current_pose_idx < len(POSES):
            tag, _, _ = POSES[self.current_pose_idx]
            dir_angle = None
            if tag == "left":
                dir_angle = math.pi  # 180 deg (Left)
            elif tag == "right":
                dir_angle = 0.0      # 0 deg (Right)
            elif tag == "up":
                dir_angle = -math.pi / 2  # -90 deg (Up)
            elif tag == "down":
                dir_angle = math.pi / 2   # 90 deg (Down)

            if dir_angle is not None:
                # Directional glowing pointer arc
                arc_span = 0.35 * math.pi
                pulse = 0.5 + 0.5 * math.sin(self.anim_tick * 4.0)
                cr.set_line_width(3.5)
                cr.set_source_rgba(0.20, 0.85, 0.40, 0.40 + 0.50 * pulse)
                cr.arc(cx, cy, ring_inner_r + 20.0, dir_angle - arc_span / 2, dir_angle + arc_span / 2)
                cr.stroke()

        # 5. Checkmark when done
        if self.is_completed:
            # Checkmark badge circle
            cr.set_source_rgba(0.20, 0.85, 0.40, 0.25)
            cr.arc(cx, cy, 38.0, 0, 2 * math.pi)
            cr.fill()
            cr.set_line_width(2.0)
            cr.set_source_rgba(0.20, 0.85, 0.40, 0.85)
            cr.arc(cx, cy, 38.0, 0, 2 * math.pi)
            cr.stroke()

            # Crisp Checkmark
            cr.set_line_width(5.0)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            cr.move_to(cx - 16, cy + 1)
            cr.line_to(cx - 4, cy + 13)
            cr.line_to(cx + 17, cy - 10)
            cr.stroke()

        cr.restore()
        return False

    def _on_dialog_destroy(self, widget) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self.camera.stop()

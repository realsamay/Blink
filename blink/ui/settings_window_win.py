"""
Native Windows Settings and Face ID Enrollment Interface for Blink.
Designed following Apple iOS 27 and macOS Sequoia glassmorphism guidelines:
dark obsidian background, uncropped whole-sensor camera viewfinder,
real-time neural vision telemetry HUD, and multi-face live highlighting.
"""

from __future__ import annotations

import getpass
import math
import os
import platform
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import cv2
import numpy as np

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector, DetectedFace
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder
from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.cues import LivenessCue, LivenessDecisionKind

from blink.core.paths import get_resources_dir, get_resource_path

RESOURCES_DIR = get_resources_dir()


# ------------------------------------------------------------------------------
# Asynchronous Background Vision Worker for Windows
# ------------------------------------------------------------------------------
class WindowsVisionWorker(threading.Thread):
    def __init__(self, camera: CameraManager, detector: FaceDetector, aligner: FaceAligner,
                 embedder: ArcFaceEmbedder, liveness: LivenessAnalyzer):
        super().__init__(daemon=True, name="BlinkWinVisionWorker")
        self.camera = camera
        self.detector = detector
        self.aligner = aligner
        self.embedder = embedder
        self.liveness = liveness
        self._running = False
        self._lock = threading.Lock()

        self.last_frame_id = -1
        self.detected_faces_telemetry: List[Dict[str, Any]] = []
        self.recognized_identity_ids: Dict[str, Tuple[str, float]] = {}
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

    def run(self) -> None:
        last_frame_change_time = time.time()
        last_face_seen_time = 0.0

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
                        matched_id = None
                        is_match = False

                        if aligned is not None and active_identities:
                            emb = self.embedder.compute_embedding(aligned)
                            best_ident, sim = SecureFaceStore.find_best_match(emb, active_identities)
                            if best_ident is not None:
                                if sim >= 0.58:
                                    matched_name = best_ident.get("name", "User")
                                    matched_sim = sim
                                    is_match = True
                                    matched_id = best_ident.get("id")
                                    recognized_ids[matched_id] = (matched_name, matched_sim)
                                    if sim > top_sim:
                                        top_sim = sim
                                        top_name = matched_name
                                else:
                                    matched_name = f"Unknown ({sim:.2f})"
                                    matched_sim = sim

                        faces_telemetry.append({
                            "bbox": face.bbox,
                            "name": matched_name or "Unknown Face",
                            "sim": matched_sim,
                            "is_match": is_match,
                            "ident_id": matched_id,
                            "landmarks_5pt": face.landmarks_5pt,
                            "landmarks_106pt": getattr(face, "landmarks_106pt", None)
                        })
                else:
                    if now - last_face_seen_time > 0.35:
                        recognized_ids = {}
                        faces_telemetry = []
                        top_sim = 0.0
                        top_name = "No Face"

                cues_data = {}
                verdict = "Ready" if not faces else "Analyzing"
                if faces:
                    dominant = FaceDetector.select_dominant_face(faces, frame_width=w)
                    if dominant:
                        x, y, fw, fh = dominant.bbox
                        crop = frame[max(0, y):min(h, y + fh), max(0, x):min(w, x + fw)]
                        dec = self.liveness.evaluate(crop)
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
# Windows Settings Application (Native Apple Glassmorphism GUI)
# ------------------------------------------------------------------------------
def run_windows_settings():
    """Entry point for native Windows Settings GUI."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    from PIL import Image, ImageTk

    settings = BlinkSettings.load()
    detector = FaceDetector()
    aligner = FaceAligner()
    embedder = ArcFaceEmbedder()
    liveness = LivenessAnalyzer()
    camera = CameraManager(device_index=settings.camera_index)

    vision_worker: Optional[WindowsVisionWorker] = None

    root = tk.Tk()
    root.title("Blink — Face ID & Security")
    root.geometry("960x680")
    root.minsize(880, 620)
    root.configure(bg="#0C0C0E")

    # Dark Theme Styling
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background="#0C0C0E", foreground="#E5E5EA", font=("Segoe UI", 10))
    style.configure("Sidebar.TFrame", background="#141418")
    style.configure("Content.TFrame", background="#0C0C0E")
    style.configure("Card.TFrame", background="#1C1C22", relief="flat")
    style.configure("Accent.TButton", background="#0071E3", foreground="#FFFFFF", font=("Segoe UI", 10, "bold"), padding=6)
    style.map("Accent.TButton", background=[("active", "#0077ED")])
    style.configure("Secondary.TButton", background="#2C2C34", foreground="#FFFFFF", font=("Segoe UI", 9), padding=5)
    style.map("Secondary.TButton", background=[("active", "#3A3A44")])
    style.configure("Danger.TButton", background="#3A1C1C", foreground="#FF453A", font=("Segoe UI", 9), padding=5)
    style.map("Danger.TButton", background=[("active", "#502424")])

    # Layout: Left Sidebar + Right Content Area
    sidebar = tk.Frame(root, bg="#141418", width=220)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    content_area = tk.Frame(root, bg="#0C0C0E")
    content_area.pack(side="right", fill="both", expand=True)

    # Sidebar Header (Logo + Title)
    side_header = tk.Frame(sidebar, bg="#141418")
    side_header.pack(fill="x", padx=16, pady=(20, 16))

    lbl_brand = tk.Label(side_header, text="  Blink", font=("Segoe UI", 15, "bold"), fg="#FFFFFF", bg="#141418")
    lbl_brand.pack(anchor="w")
    lbl_brand_sub = tk.Label(side_header, text="  Face ID & Security", font=("Segoe UI", 9), fg="#8E8E93", bg="#141418")
    lbl_brand_sub.pack(anchor="w")

    # Tab Pages container
    pages: Dict[str, tk.Frame] = {}
    active_page_var = tk.StringVar(value="face_id")

    def show_page(page_name: str):
        active_page_var.set(page_name)
        for name, frame in pages.items():
            if name == page_name:
                frame.pack(fill="both", expand=True, padx=24, pady=20)
            else:
                frame.pack_forget()

        # Update sidebar button states
        for name, btn in nav_buttons.items():
            if name == page_name:
                btn.config(bg="#0071E3", fg="#FFFFFF")
            else:
                btn.config(bg="#141418", fg="#A1A1A6")

        # Camera management per page
        if page_name in ("face_id", "camera", "diagnostics"):
            start_vision()
        else:
            stop_vision()

    # Sidebar Navigation Buttons
    nav_buttons: Dict[str, tk.Button] = {}
    nav_items = [
        ("face_id", "🛡️  Face ID & Passcode"),
        ("camera", "📷  Camera & Security"),
        ("diagnostics", "🔬  Face Lab Diagnostics"),
        ("about", "ℹ️  About Blink"),
    ]

    for key, label in nav_items:
        btn = tk.Button(
            sidebar,
            text=f"  {label}",
            anchor="w",
            font=("Segoe UI", 10, "bold" if key == "face_id" else "normal"),
            bg="#0071E3" if key == "face_id" else "#141418",
            fg="#FFFFFF" if key == "face_id" else "#A1A1A6",
            relief="flat",
            activebackground="#0071E3",
            activeforeground="#FFFFFF",
            padx=12,
            pady=10,
            command=lambda k=key: show_page(k)
        )
        btn.pack(fill="x", padx=10, pady=4)
        nav_buttons[key] = btn

    # Bottom daemon status badge in sidebar
    side_footer = tk.Frame(sidebar, bg="#141418")
    side_footer.pack(side="bottom", fill="x", padx=16, pady=16)
    lbl_daemon_status = tk.Label(side_footer, text="● Service Active", font=("Segoe UI", 9, "bold"), fg="#34C759", bg="#141418")
    lbl_daemon_status.pack(anchor="w")

    # Vision Worker Lifecycle
    def start_vision():
        nonlocal vision_worker
        if not camera.is_running:
            camera.start()
        if vision_worker is None or not vision_worker.is_alive():
            vision_worker = WindowsVisionWorker(camera, detector, aligner, embedder, liveness)
            vision_worker.start_worker()

    def stop_vision():
        nonlocal vision_worker
        if vision_worker is not None:
            vision_worker.stop_worker()
            vision_worker = None
        if camera.is_running:
            camera.stop()

    # ==========================================================================
    # PAGE 1: Face ID & Passcode
    # ==========================================================================
    page_face_id = tk.Frame(content_area, bg="#0C0C0E")
    pages["face_id"] = page_face_id

    # Hero Banner
    hero_card = tk.Frame(page_face_id, bg="#1C1C24", highlightthickness=1, highlightbackground="#2C2C38", padx=18, pady=16)
    hero_card.pack(fill="x", pady=(0, 16))

    hero_top = tk.Frame(hero_card, bg="#1C1C24")
    hero_top.pack(fill="x")
    tk.Label(hero_top, text="Face ID & Passcode", font=("Segoe UI", 16, "bold"), fg="#FFFFFF", bg="#1C1C24").pack(side="left")

    has_identities = len(SecureFaceStore.load_identities()) > 0
    hero_status_txt = "● Active & Guarding" if (settings.is_face_unlock_enabled and has_identities) else "● Disabled"
    hero_status_color = "#34C759" if (settings.is_face_unlock_enabled and has_identities) else "#FF9F0A"
    lbl_hero_status = tk.Label(hero_top, text=hero_status_txt, font=("Segoe UI", 9, "bold"), fg=hero_status_color, bg="#1C1C24")
    lbl_hero_status.pack(side="right")

    tk.Label(hero_card, text="Sub-second offline biometric facial recognition with YuNet CNN and 512-D ArcFace.",
             font=("Segoe UI", 10), fg="#8E8E93", bg="#1C1C24").pack(anchor="w", pady=(4, 0))

    # Master Toggle Row
    card_toggle = tk.Frame(page_face_id, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=12)
    card_toggle.pack(fill="x", pady=6)

    var_face_enabled = tk.BooleanVar(value=settings.is_face_unlock_enabled)
    def on_toggle_face():
        settings.is_face_unlock_enabled = var_face_enabled.get()
        settings.save()
        st = "● Active & Guarding" if settings.is_face_unlock_enabled else "● Disabled"
        cl = "#34C759" if settings.is_face_unlock_enabled else "#FF9F0A"
        lbl_hero_status.config(text=st, fg=cl)

    chk_master = tk.Checkbutton(
        card_toggle, text="Enable Face ID Unlock", variable=var_face_enabled,
        font=("Segoe UI", 11, "bold"), bg="#1C1C22", fg="#FFFFFF", selectcolor="#0071E3",
        activebackground="#1C1C22", activeforeground="#FFFFFF", command=on_toggle_face
    )
    chk_master.pack(side="left")

    def test_preview_scan():
        try:
            stop_vision()
            from blink.core.coordinator import FaceUnlockCoordinator
            coord = FaceUnlockCoordinator()
            coord.overlay._is_desktop_preview = True
            threading.Thread(target=coord.test_scan, daemon=True).start()
            root.after(2600, start_vision)
        except Exception as e:
            messagebox.showerror("Blink", f"Test preview error: {e}")

    btn_test = ttk.Button(card_toggle, text="▶ Test Face ID Card", style="Secondary.TButton", command=test_preview_scan)
    btn_test.pack(side="right", padx=6)

    # Saved Profiles Table Card (With Live Multi-Face Recognition Highlight)
    tk.Label(page_face_id, text="Enrolled Profiles (Recognizes Multiple Saved Faces)", font=("Segoe UI", 11, "bold"), fg="#A1A1A6", bg="#0C0C0E").pack(anchor="w", pady=(12, 4))

    card_profiles = tk.Frame(page_face_id, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=14)
    card_profiles.pack(fill="both", expand=True, pady=4)

    profile_rows_container = tk.Frame(card_profiles, bg="#1C1C22")
    profile_rows_container.pack(fill="both", expand=True)

    profile_row_widgets = {}

    def refresh_profiles():
        for widget in profile_rows_container.winfo_children():
            widget.destroy()
        profile_row_widgets.clear()

        identities = SecureFaceStore.load_identities()
        if not identities:
            lbl_empty = tk.Label(profile_rows_container, text="No faces enrolled yet. Click '➕ Enroll New Face' below.",
                                 font=("Segoe UI", 10), fg="#8E8E93", bg="#1C1C22")
            lbl_empty.pack(pady=20)
            return

        for ident in identities:
            i_id = ident.get("id")
            name = ident.get("name", "User")
            samples = len(ident.get("samples", []))

            row_frame = tk.Frame(profile_rows_container, bg="#24242C", highlightthickness=1, highlightbackground="#34343E", padx=12, pady=10)
            row_frame.pack(fill="x", pady=4)

            lbl_name = tk.Label(row_frame, text=f"👤  {name}  ({samples} poses enrolled)", font=("Segoe UI", 10, "bold"), fg="#FFFFFF", bg="#24242C")
            lbl_name.pack(side="left")

            lbl_match_badge = tk.Label(row_frame, text="● In View", font=("Segoe UI", 9, "bold"), fg="#34C759", bg="#24242C")
            lbl_match_badge.pack(side="left", padx=12)
            lbl_match_badge.pack_forget()  # Hidden until recognized

            def delete_this_profile(pid=i_id, pname=name):
                if messagebox.askyesno("Delete Face Profile", f"Remove biometric profile for '{pname}'?"):
                    existing = [x for x in SecureFaceStore.load_identities() if x.get("id") != pid]
                    SecureFaceStore.save_identities(existing)
                    refresh_profiles()

            def rename_this_profile(pid=i_id, old_name=name):
                rename_win = tk.Toplevel(root)
                rename_win.title("Rename Profile")
                rename_win.geometry("320x130")
                rename_win.configure(bg="#1C1C22")
                tk.Label(rename_win, text=f"New name for '{old_name}':", bg="#1C1C22", fg="#FFFFFF", font=("Segoe UI", 10)).pack(pady=(12, 4))
                e_name = tk.Entry(rename_win, bg="#2C2C34", fg="#FFFFFF", insertbackground="#FFFFFF", font=("Segoe UI", 10))
                e_name.insert(0, old_name)
                e_name.pack(pady=4, padx=16, fill="x")

                def confirm_rename():
                    new_n = e_name.get().strip()
                    if new_n:
                        all_ids = SecureFaceStore.load_identities()
                        for x in all_ids:
                            if x.get("id") == pid:
                                x["name"] = new_n
                        SecureFaceStore.save_identities(all_ids)
                        refresh_profiles()
                    rename_win.destroy()

                ttk.Button(rename_win, text="Save", style="Accent.TButton", command=confirm_rename).pack(pady=8)

            btn_del = tk.Button(row_frame, text="Delete", font=("Segoe UI", 8), bg="#3A1C1C", fg="#FF453A", relief="flat", command=delete_this_profile)
            btn_del.pack(side="right", padx=4)

            btn_ren = tk.Button(row_frame, text="Rename", font=("Segoe UI", 8), bg="#2C2C34", fg="#FFFFFF", relief="flat", command=rename_this_profile)
            btn_ren.pack(side="right", padx=4)

            profile_row_widgets[i_id] = {
                "frame": row_frame,
                "badge": lbl_match_badge,
                "label": lbl_name
            }

    refresh_profiles()

    # Profile action buttons
    profile_actions = tk.Frame(card_profiles, bg="#1C1C22")
    profile_actions.pack(fill="x", pady=(10, 0))

    def open_enrollment():
        stop_vision()
        enroll_win = tk.Toplevel(root)
        enroll_win.title("Enroll Face ID")
        enroll_win.geometry("540x560")
        enroll_win.configure(bg="#0C0C0E")

        tk.Label(enroll_win, text="Face ID Enrollment", font=("Segoe UI", 14, "bold"), fg="#FFFFFF", bg="#0C0C0E").pack(pady=(16, 4))
        tk.Label(enroll_win, text="Look directly at the camera to capture 5 facial angles.", font=("Segoe UI", 10), fg="#8E8E93", bg="#0C0C0E").pack()

        name_box = tk.Frame(enroll_win, bg="#1C1C22", padx=12, pady=10)
        name_box.pack(fill="x", padx=24, pady=10)
        tk.Label(name_box, text="Profile Name:", font=("Segoe UI", 10, "bold"), fg="#FFFFFF", bg="#1C1C22").pack(side="left")
        e_profile = tk.Entry(name_box, font=("Segoe UI", 10), bg="#2C2C2E", fg="#FFFFFF", insertbackground="#FFFFFF")
        e_profile.insert(0, getpass.getuser().capitalize())
        e_profile.pack(side="left", padx=12, fill="x", expand=True)

        cam_canvas = tk.Canvas(enroll_win, width=440, height=330, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        cam_canvas.pack(pady=8)

        lbl_enroll_prompt = tk.Label(enroll_win, text="Position your face in the center...", font=("Segoe UI", 11, "bold"), fg="#0071E3", bg="#0C0C0E")
        lbl_enroll_prompt.pack(pady=4)

        poses = ["Center", "Look Slightly Left", "Look Slightly Right", "Tilt Up", "Tilt Down"]
        current_pose_idx = [0]
        captured_embeddings = []
        camera.start()

        def update_enroll_feed():
            if not enroll_win.winfo_exists():
                camera.stop()
                start_vision()
                return

            res = camera.get_latest_frame()
            if res:
                _, frame = res
                faces = detector.detect_faces(frame)
                if faces:
                    f = faces[0]
                    fx, fy, fw, fh = f.bbox
                    cv2.rectangle(frame, (fx, fy), (fx + fw, fy + fh), (52, 199, 89), 2)
                    for pt in f.landmarks_5pt:
                        cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, (0, 113, 227), -1)

                    if len(captured_embeddings) < len(poses):
                        aligned = aligner.align(f, frame)
                        if aligned is not None:
                            emb = embedder.compute_embedding(aligned)
                            captured_embeddings.append({"embedding": emb.tolist(), "pose": poses[current_pose_idx[0]]})
                            current_pose_idx[0] += 1
                            if current_pose_idx[0] < len(poses):
                                lbl_enroll_prompt.config(text=f"Pose {current_pose_idx[0]+1}/5: {poses[current_pose_idx[0]]}", fg="#FF9F0A")
                            else:
                                lbl_enroll_prompt.config(text="✅ Enrollment Complete!", fg="#34C759")
                                p_name = e_profile.get().strip() or "User"
                                new_id = {
                                    "id": str(uuid.uuid4()),
                                    "name": p_name,
                                    "samples": captured_embeddings,
                                    "is_enabled": True,
                                    "enrolled_at": time.time(),
                                }
                                existing = SecureFaceStore.load_identities()
                                existing.append(new_id)
                                SecureFaceStore.save_identities(existing)
                                if not settings.is_face_unlock_enabled:
                                    var_face_enabled.set(True)
                                    on_toggle_face()
                                refresh_profiles()
                                enroll_win.after(1000, lambda: [enroll_win.destroy(), camera.stop(), start_vision()])
                                return

                rgb = cv2.cvtColor(cv2.resize(frame, (440, 330)), cv2.COLOR_BGR2RGB)
                img = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                cam_canvas.create_image(0, 0, image=img, anchor="nw")
                cam_canvas.image = img

            enroll_win.after(30, update_enroll_feed)

        update_enroll_feed()

    btn_add_face = ttk.Button(profile_actions, text="➕ Enroll New Face", style="Accent.TButton", command=open_enrollment)
    btn_add_face.pack(side="left")

    btn_ref_face = ttk.Button(profile_actions, text="↻ Refresh Watchdog", style="Secondary.TButton", command=refresh_profiles)
    btn_ref_face.pack(side="left", padx=8)

    # Lockscreen Credentials Card
    card_pwd = tk.Frame(page_face_id, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=12)
    card_pwd.pack(fill="x", pady=8)

    pwd_top = tk.Frame(card_pwd, bg="#1C1C22")
    pwd_top.pack(fill="x")
    tk.Label(pwd_top, text="Windows Lockscreen Password / PIN", font=("Segoe UI", 10, "bold"), fg="#FFFFFF", bg="#1C1C22").pack(side="left")

    has_pwd = SecureCredentialManager.has_stored_password()
    lbl_pwd_badge = tk.Label(pwd_top, text="● AES-256 Encrypted" if has_pwd else "● Not Configured",
                             font=("Segoe UI", 9, "bold"), fg="#34C759" if has_pwd else "#FF9F0A", bg="#1C1C22")
    lbl_pwd_badge.pack(side="right")

    pwd_input_row = tk.Frame(card_pwd, bg="#1C1C22")
    pwd_input_row.pack(fill="x", pady=6)

    entry_pwd = tk.Entry(pwd_input_row, show="•", font=("Segoe UI", 11), bg="#2C2C2E", fg="#FFFFFF", insertbackground="#FFFFFF", relief="flat")
    entry_pwd.pack(side="left", fill="x", expand=True, ipady=3)

    def save_pwd():
        p = entry_pwd.get().strip()
        if not p:
            messagebox.showwarning("Blink", "Please enter your Windows password or PIN.")
            return
        SecureCredentialManager.save_password(p)
        lbl_pwd_badge.config(text="● AES-256 Encrypted", fg="#34C759")
        entry_pwd.delete(0, tk.END)
        messagebox.showinfo("Blink", "Password securely encrypted and saved with AES-256-GCM.")

    btn_save_pwd = ttk.Button(pwd_input_row, text="Save Password", style="Accent.TButton", command=save_pwd)
    btn_save_pwd.pack(side="right", padx=(8, 0))

    # ==========================================================================
    # PAGE 2: Camera & Security
    # ==========================================================================
    page_camera = tk.Frame(content_area, bg="#0C0C0E")
    pages["camera"] = page_camera

    tk.Label(page_camera, text="Camera & Security", font=("Segoe UI", 16, "bold"), fg="#FFFFFF", bg="#0C0C0E").pack(anchor="w", pady=(0, 4))
    tk.Label(page_camera, text="Whole-sensor video capture with multi-cue biometric liveness protection.",
             font=("Segoe UI", 10), fg="#8E8E93", bg="#0C0C0E").pack(anchor="w", pady=(0, 14))

    # Whole Sensor Camera Canvas
    card_sensor = tk.Frame(page_camera, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=12)
    card_sensor.pack(fill="x", pady=4)

    sensor_header = tk.Frame(card_sensor, bg="#1C1C22")
    sensor_header.pack(fill="x", pady=(0, 6))
    tk.Label(sensor_header, text="Sensor Viewfinder (Uncropped)", font=("Segoe UI", 10, "bold"), fg="#FFFFFF", bg="#1C1C22").pack(side="left")
    lbl_sensor_spec = tk.Label(sensor_header, text="1280 × 720 • 16:9 Sensor", font=("Segoe UI", 9), fg="#8E8E93", bg="#1C1C22")
    lbl_sensor_spec.pack(side="right")

    canvas_cam_sensor = tk.Canvas(card_sensor, width=540, height=300, bg="#000000", highlightthickness=0)
    canvas_cam_sensor.pack(pady=4)

    # Anti-Spoofing Settings
    card_security = tk.Frame(page_camera, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=12)
    card_security.pack(fill="x", pady=10)

    var_live = tk.BooleanVar(value=settings.liveness_checks_enabled)
    def on_toggle_live():
        settings.liveness_checks_enabled = var_live.get()
        settings.save()

    chk_live = tk.Checkbutton(card_security, text="Multi-Cue Anti-Spoofing Protection (Detects printed photos & screen replays)",
                              variable=var_live, font=("Segoe UI", 10, "bold"), bg="#1C1C22", fg="#FFFFFF",
                              selectcolor="#0071E3", activebackground="#1C1C22", command=on_toggle_live)
    chk_live.pack(anchor="w", pady=4)

    # Controls Row (Camera Index & Match Threshold)
    ctrl_row = tk.Frame(card_security, bg="#1C1C22")
    ctrl_row.pack(fill="x", pady=(10, 4))

    tk.Label(ctrl_row, text="Camera Index:", font=("Segoe UI", 9, "bold"), fg="#E5E5EA", bg="#1C1C22").pack(side="left")
    spin_cam = ttk.Spinbox(ctrl_row, from_=0, to=5, width=4)
    spin_cam.set(settings.camera_index)
    spin_cam.pack(side="left", padx=8)

    def on_change_cam():
        try:
            settings.camera_index = int(spin_cam.get())
            settings.save()
            stop_vision()
            camera.device_index = settings.camera_index
            start_vision()
        except Exception:
            pass

    btn_set_cam = ttk.Button(ctrl_row, text="Switch Camera", style="Secondary.TButton", command=on_change_cam)
    btn_set_cam.pack(side="left", padx=4)

    # ==========================================================================
    # PAGE 3: Face Lab Diagnostics (Zero-Lag Live HUD Viewfinder)
    # ==========================================================================
    page_diag = tk.Frame(content_area, bg="#0C0C0E")
    pages["diagnostics"] = page_diag

    diag_header = tk.Frame(page_diag, bg="#0C0C0E")
    diag_header.pack(fill="x", pady=(0, 10))
    tk.Label(diag_header, text="Real-Time Vision Telemetry", font=("Segoe UI", 16, "bold"), fg="#FFFFFF", bg="#0C0C0E").pack(side="left")

    diag_body = tk.Frame(page_diag, bg="#0C0C0E")
    diag_body.pack(fill="both", expand=True)

    # Left: Camera Feed with Neon HUD Overlays
    canvas_diag = tk.Canvas(diag_body, width=460, height=340, bg="#000000", highlightthickness=1, highlightbackground="#333338")
    canvas_diag.pack(side="left", padx=(0, 16), pady=4)

    # Right: Telemetry Meters Card
    card_diag_meters = tk.Frame(diag_body, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=14)
    card_diag_meters.pack(side="right", fill="both", expand=True)

    tk.Label(card_diag_meters, text="Biometric Telemetry", font=("Segoe UI", 11, "bold"), fg="#FFFFFF", bg="#1C1C22").pack(anchor="w", pady=(0, 8))

    lbl_meter_match = tk.Label(card_diag_meters, text="Match Identity: No Face", font=("Segoe UI", 10), fg="#E5E5EA", bg="#1C1C22")
    lbl_meter_match.pack(anchor="w", pady=2)

    prog_match = ttk.Progressbar(card_diag_meters, length=200, mode="determinate")
    prog_match.pack(fill="x", pady=4)

    lbl_meter_verdict = tk.Label(card_diag_meters, text="Liveness Verdict: Ready", font=("Segoe UI", 10), fg="#34C759", bg="#1C1C22")
    lbl_meter_verdict.pack(anchor="w", pady=(8, 2))

    lbl_meter_latency = tk.Label(card_diag_meters, text="Latency: -- ms", font=("Segoe UI", 9), fg="#8E8E93", bg="#1C1C22")
    lbl_meter_latency.pack(anchor="w", pady=2)

    lbl_meter_fps = tk.Label(card_diag_meters, text="Capture Rate: 30 FPS", font=("Segoe UI", 9), fg="#8E8E93", bg="#1C1C22")
    lbl_meter_fps.pack(anchor="w", pady=2)

    # ==========================================================================
    # PAGE 4: About Blink
    # ==========================================================================
    page_about = tk.Frame(content_area, bg="#0C0C0E")
    pages["about"] = page_about

    card_about = tk.Frame(page_about, bg="#1C1C24", highlightthickness=1, highlightbackground="#2C2C38", padx=24, pady=24)
    card_about.pack(fill="x", pady=(0, 16))

    tk.Label(card_about, text="Blink", font=("Segoe UI", 20, "bold"), fg="#FFFFFF", bg="#1C1C24").pack(anchor="w")
    tk.Label(card_about, text="Version 2.1.0 • Apple Human Interface Edition", font=("Segoe UI", 10, "bold"), fg="#0071E3", bg="#1C1C24").pack(anchor="w", pady=(2, 6))
    tk.Label(card_about, text="Windows 10 & 11 (64-bit) • 100% On-Device Neural Vision", font=("Segoe UI", 9), fg="#8E8E93", bg="#1C1C24").pack(anchor="w")

    card_links = tk.Frame(page_about, bg="#1C1C22", highlightthickness=1, highlightbackground="#2C2C34", padx=16, pady=14)
    card_links.pack(fill="x", pady=8)

    tk.Label(card_links, text="Open Source & Documentation", font=("Segoe UI", 10, "bold"), fg="#FFFFFF", bg="#1C1C22").pack(anchor="w", pady=(0, 6))

    btn_open_web = ttk.Button(card_links, text="Visit Website (blink-web.vercel.app)", style="Secondary.TButton",
                              command=lambda: os.system("start https://blink-web.vercel.app"))
    btn_open_web.pack(anchor="w", pady=4)

    btn_open_git = ttk.Button(card_links, text="View GitHub Repository (github.com/realsamay/blink)", style="Secondary.TButton",
                              command=lambda: os.system("start https://github.com/realsamay/blink"))
    btn_open_git.pack(anchor="w", pady=4)

    # ==========================================================================
    # 60 FPS Camera Update Loop
    # ==========================================================================
    def update_live_ui():
        try:
            curr_page = active_page_var.get()
            if curr_page in ("face_id", "camera", "diagnostics") and camera.is_running:
                res = camera.get_latest_frame()
                if res and vision_worker:
                    _, frame = res
                    telemetry = vision_worker.get_telemetry()
                    rec_ids = telemetry.get("recognized_ids", {})

                    # 1. Update Profile Rows Highlight (Multi-Face Match)
                    if curr_page == "face_id":
                        for pid, wdict in profile_row_widgets.items():
                            frame_row = wdict["frame"]
                            badge = wdict["badge"]
                            if pid in rec_ids:
                                name, sim = rec_ids[pid]
                                frame_row.config(bg="#1E3824", highlightbackground="#34C759")
                                badge.config(text=f"● In View ({int(sim*100)}% Match)", fg="#34C759", bg="#1E3824")
                                badge.pack(side="left", padx=12)
                            else:
                                frame_row.config(bg="#24242C", highlightbackground="#34343E")
                                badge.pack_forget()

                    # 2. Update Camera Page Viewfinder
                    elif curr_page == "camera":
                        h, w = frame.shape[:2]
                        lbl_sensor_spec.config(text=f"{w} × {h} • Whole Sensor")
                        rgb = cv2.cvtColor(cv2.resize(frame, (540, 300)), cv2.COLOR_BGR2RGB)
                        img = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                        canvas_cam_sensor.create_image(0, 0, image=img, anchor="nw")
                        canvas_cam_sensor.image = img

                    # 3. Update Diagnostics Page
                    elif curr_page == "diagnostics":
                        h, w = frame.shape[:2]
                        disp = frame.copy()

                        # Draw Neon Viewfinder Brackets & Name Tags
                        for f_item in telemetry.get("faces", []):
                            fx, fy, fw, fh = f_item["bbox"]
                            is_match = f_item["is_match"]
                            color = (89, 199, 52) if is_match else (200, 200, 200)

                            b_len = 16
                            cv2.line(disp, (fx, fy), (fx + b_len, fy), color, 2)
                            cv2.line(disp, (fx, fy), (fx, fy + b_len), color, 2)
                            cv2.line(disp, (fx + fw, fy), (fx + fw - b_len, fy), color, 2)
                            cv2.line(disp, (fx + fw, fy), (fx + fw, fy + b_len), color, 2)
                            cv2.line(disp, (fx, fy + fh), (fx + b_len, fy + fh), color, 2)
                            cv2.line(disp, (fx, fy + fh), (fx, fy + fh - b_len), color, 2)
                            cv2.line(disp, (fx + fw, fy + fh), (fx + fw - b_len, fy + fh), color, 2)
                            cv2.line(disp, (fx + fw, fy + fh), (fx + fw, fy + fh - b_len), color, 2)

                            sim = f_item["sim"]
                            lbl_txt = f"{f_item['name']} • {int(sim*100)}%" if is_match else f_item["name"]
                            cv2.rectangle(disp, (fx, max(4, fy - 22)), (fx + len(lbl_txt)*9 + 10, max(22, fy - 2)), (18, 18, 24), -1)
                            cv2.rectangle(disp, (fx, max(4, fy - 22)), (fx + len(lbl_txt)*9 + 10, max(22, fy - 2)), color, 1)
                            cv2.putText(disp, lbl_txt, (fx + 5, max(18, fy - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

                            if f_item.get("landmarks_5pt") is not None:
                                for pt in f_item["landmarks_5pt"]:
                                    cv2.circle(disp, (int(pt[0]), int(pt[1])), 3, (0, 255, 255), -1)

                        # Top HUD Badge
                        fps_val = int(1000.0 / max(1.0, telemetry.get("latency_ms", 30.0)))
                        cv2.rectangle(disp, (8, 8), (220, 32), (14, 14, 18), -1)
                        cv2.rectangle(disp, (8, 8), (220, 32), (80, 80, 90), 1)
                        cv2.circle(disp, (18, 20), 4, (52, 199, 89), -1)
                        cv2.putText(disp, f"NEURAL HUD  {w}x{h}  {fps_val} FPS", (28, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 230), 1, cv2.LINE_AA)

                        rgb = cv2.cvtColor(cv2.resize(disp, (460, 340)), cv2.COLOR_BGR2RGB)
                        img = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                        canvas_diag.create_image(0, 0, image=img, anchor="nw")
                        canvas_diag.image = img

                        top_sim = telemetry.get("top_sim", 0.0)
                        top_name = telemetry.get("top_name", "No Face")
                        prog_match["value"] = int(top_sim * 100)
                        lbl_meter_match.config(text=f"Match Identity: {top_name} ({int(top_sim*100)}%)")
                        lbl_meter_latency.config(text=f"Latency: {telemetry.get('latency_ms', 0.0):.1f} ms")
                        lbl_meter_fps.config(text=f"Inference: {fps_val} FPS")
                        lbl_meter_verdict.config(text=f"Liveness: {telemetry.get('verdict', 'Ready')}")

        except Exception:
            pass

        root.after(30, update_live_ui)

    # Clean shutdown handler
    def on_close():
        stop_vision()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Initialize default page
    show_page("face_id")
    update_live_ui()

    root.mainloop()


if __name__ == "__main__":
    run_windows_settings()

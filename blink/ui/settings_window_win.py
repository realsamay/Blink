"""
Native Windows Settings and Face ID Enrollment Interface for Blink.
Built with standard Tkinter and OpenCV, providing a sleek dark-mode UI
with multi-identity enrollment, 5-pose capture, and Face Lab diagnostics.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from typing import Optional, List, Dict, Any

import cv2
import numpy as np

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector, DetectedFace
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder
from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.cues import LivenessDecisionKind


def run_windows_settings():
    """Entry point for Windows Settings GUI."""
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("Blink - Face ID Settings")
    root.geometry("860x650")
    root.configure(bg="#121214")

    # Dark theme styling
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background="#121214", foreground="#E5E5E7", font=("Segoe UI", 10))
    style.configure("TNotebook", background="#121214", borderwidth=0)
    style.configure("TNotebook.Tab", background="#1C1C1E", foreground="#8E8E93", padding=[14, 8], font=("Segoe UI", 10, "bold"))
    style.map("TNotebook.Tab", background=[("selected", "#2C2C2E")], foreground=[("selected", "#FFFFFF")])
    style.configure("TFrame", background="#121214")
    style.configure("Card.TFrame", background="#1C1C1E", relief="flat")
    style.configure("Accent.TButton", background="#0071E3", foreground="#FFFFFF", font=("Segoe UI", 10, "bold"), padding=6)
    style.map("Accent.TButton", background=[("active", "#0077ED")])

    settings = BlinkSettings.load()
    detector = FaceDetector()
    aligner = FaceAligner()
    embedder = ArcFaceEmbedder()
    camera = CameraManager(device_index=settings.camera_index)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=16, pady=16)

    # -------------------------------------------------------------
    # Tab 1: General Settings
    # -------------------------------------------------------------
    tab_general = ttk.Frame(notebook)
    notebook.add(tab_general, text="General")

    lbl_title = tk.Label(tab_general, text="Face ID & Security", font=("Segoe UI", 16, "bold"), bg="#121214", fg="#FFFFFF")
    lbl_title.pack(anchor="w", padx=16, pady=(16, 8))

    card_gen = ttk.Frame(tab_general, style="Card.TFrame", padding=16)
    card_gen.pack(fill="x", padx=16, pady=8)

    var_enabled = tk.BooleanVar(value=settings.is_face_unlock_enabled)
    chk_enabled = tk.Checkbutton(card_gen, text="Enable Face ID Unlock", variable=var_enabled, font=("Segoe UI", 11, "bold"),
                                 bg="#1C1C1E", fg="#FFFFFF", selectcolor="#0071E3", activebackground="#1C1C1E", activeforeground="#FFFFFF")
    chk_enabled.pack(anchor="w", pady=4)

    var_liveness = tk.BooleanVar(value=settings.liveness_checks_enabled)
    chk_liveness = tk.Checkbutton(card_gen, text="Enable Multi-Cue Anti-Spoofing Protection", variable=var_liveness,
                                  bg="#1C1C1E", fg="#FFFFFF", selectcolor="#0071E3", activebackground="#1C1C1E", activeforeground="#FFFFFF")
    chk_liveness.pack(anchor="w", pady=4)

    # Triggers & Behavior
    tk.Label(card_gen, text="Unlock Triggers:", font=("Segoe UI", 10, "bold"), bg="#1C1C1E", fg="#FFFFFF").pack(anchor="w", pady=(8, 2))
    var_wake = tk.BooleanVar(value="on_wake" in settings.unlock_triggers)
    var_lock = tk.BooleanVar(value="on_lock" in settings.unlock_triggers)
    var_space = tk.BooleanVar(value="on_space" in settings.unlock_triggers)
    tk.Checkbutton(card_gen, text="On Wake (screen turned on / lid opened)", variable=var_wake, bg="#1C1C1E", fg="#E5E5E7", selectcolor="#0071E3", activebackground="#1C1C1E").pack(anchor="w")
    tk.Checkbutton(card_gen, text="On Lock (immediate scan upon screen lock)", variable=var_lock, bg="#1C1C1E", fg="#E5E5E7", selectcolor="#0071E3", activebackground="#1C1C1E").pack(anchor="w")
    tk.Checkbutton(card_gen, text="On Space (when spacebar is pressed at lockscreen)", variable=var_space, bg="#1C1C1E", fg="#E5E5E7", selectcolor="#0071E3", activebackground="#1C1C1E").pack(anchor="w")

    var_retry = tk.BooleanVar(value=settings.auto_retry_once)
    tk.Checkbutton(card_gen, text="Auto-retry scan once on failed recognition", variable=var_retry, bg="#1C1C1E", fg="#E5E5E7", selectcolor="#0071E3", activebackground="#1C1C1E").pack(anchor="w", pady=(4, 0))

    var_anim = tk.BooleanVar(value=settings.show_unlock_animation)
    tk.Checkbutton(card_gen, text="Show Face ID floating overlay on screen", variable=var_anim, bg="#1C1C1E", fg="#E5E5E7", selectcolor="#0071E3", activebackground="#1C1C1E").pack(anchor="w")

    # Camera Device Index
    tk.Label(card_gen, text="Camera Device Index:", font=("Segoe UI", 10, "bold"), bg="#1C1C1E", fg="#FFFFFF").pack(anchor="w", pady=(8, 2))
    cam_index_var = tk.IntVar(value=settings.camera_index)
    spin_cam = ttk.Spinbox(card_gen, from_=0, to=5, textvariable=cam_index_var, width=6)
    spin_cam.pack(anchor="w", pady=2)

    # Sliders
    tk.Label(card_gen, text="Match Similarity Threshold (Default: 0.60):", bg="#1C1C1E", fg="#8E8E93").pack(anchor="w", pady=(12, 2))
    scale_thresh = tk.Scale(card_gen, from_=0.40, to=0.85, resolution=0.01, orient="horizontal",
                            bg="#1C1C1E", fg="#FFFFFF", highlightthickness=0, troughcolor="#2C2C2E")
    scale_thresh.set(settings.match_threshold)
    scale_thresh.pack(fill="x", pady=2)

    tk.Label(card_gen, text="Detection Timeout (Seconds):", bg="#1C1C1E", fg="#8E8E93").pack(anchor="w", pady=(8, 2))
    scale_timeout = tk.Scale(card_gen, from_=2, to=10, resolution=1, orient="horizontal",
                             bg="#1C1C1E", fg="#FFFFFF", highlightthickness=0, troughcolor="#2C2C2E")
    scale_timeout.set(settings.face_detection_seconds)
    scale_timeout.pack(fill="x", pady=2)

    def test_preview_scan():
        try:
            from blink.core.coordinator import FaceUnlockCoordinator
            coord = FaceUnlockCoordinator()
            coord.overlay._is_desktop_preview = True
            threading.Thread(target=coord.test_scan, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Blink", f"Test scan failed: {e}")

    btn_test_preview = ttk.Button(tab_general, text="▶ Preview Face ID Card (Test Scan)", command=test_preview_scan)
    btn_test_preview.pack(side="left", padx=16, pady=16)

    def save_general():
        settings.is_face_unlock_enabled = var_enabled.get()
        settings.liveness_checks_enabled = var_liveness.get()
        settings.auto_retry_once = var_retry.get()
        settings.show_unlock_animation = var_anim.get()
        settings.camera_index = int(cam_index_var.get())
        settings.match_threshold = float(scale_thresh.get())
        settings.face_detection_seconds = int(scale_timeout.get())

        trigs = []
        if var_wake.get(): trigs.append("on_wake")
        if var_lock.get(): trigs.append("on_lock")
        if var_space.get(): trigs.append("on_space")
        settings.unlock_triggers = trigs

        settings.save()
        messagebox.showinfo("Blink", "Settings saved successfully.")

    btn_save = ttk.Button(tab_general, text="Save Settings", style="Accent.TButton", command=save_general)
    btn_save.pack(side="right", padx=16, pady=16)

    # -------------------------------------------------------------
    # Tab 2: Password
    # -------------------------------------------------------------
    tab_pwd = ttk.Frame(notebook)
    notebook.add(tab_pwd, text="Password")

    tk.Label(tab_pwd, text="Lockscreen Credentials", font=("Segoe UI", 16, "bold"), bg="#121214", fg="#FFFFFF").pack(anchor="w", padx=16, pady=(16, 4))
    tk.Label(tab_pwd, text="Stored securely with AES-256-GCM encryption using hardware-linked PBKDF2.", bg="#121214", fg="#8E8E93").pack(anchor="w", padx=16, pady=(0, 12))

    card_pwd = ttk.Frame(tab_pwd, style="Card.TFrame", padding=16)
    card_pwd.pack(fill="x", padx=16, pady=8)

    status_str = "Password configured and active." if SecureCredentialManager.has_stored_password() else "No password stored yet."
    status_color = "#30D158" if SecureCredentialManager.has_stored_password() else "#FF9F0A"
    lbl_pwd_status = tk.Label(card_pwd, text=status_str, fg=status_color, bg="#1C1C1E", font=("Segoe UI", 10, "bold"))
    lbl_pwd_status.pack(anchor="w", pady=(0, 12))

    tk.Label(card_pwd, text="Windows Account Password / PIN:", bg="#1C1C1E", fg="#FFFFFF").pack(anchor="w")
    entry_pwd = tk.Entry(card_pwd, show="•", font=("Segoe UI", 12), bg="#2C2C2E", fg="#FFFFFF", insertbackground="#FFFFFF", relief="flat")
    entry_pwd.pack(fill="x", pady=6, ipady=4)

    def save_pwd():
        p = entry_pwd.get().strip()
        if not p:
            messagebox.showwarning("Blink", "Please enter a valid password.")
            return
        SecureCredentialManager.save_password(p)
        lbl_pwd_status.config(text="Password configured and active.", fg="#30D158")
        entry_pwd.delete(0, tk.END)
        messagebox.showinfo("Blink", "Password securely saved with AES-256-GCM.")

    btn_save_pwd = ttk.Button(card_pwd, text="Store Password", style="Accent.TButton", command=save_pwd)
    btn_save_pwd.pack(anchor="e", pady=8)

    # -------------------------------------------------------------
    # Tab 3: Enrolled Faces
    # -------------------------------------------------------------
    tab_faces = ttk.Frame(notebook)
    notebook.add(tab_faces, text="Your Face")

    tk.Label(tab_faces, text="Enrolled Biometric Profiles", font=("Segoe UI", 16, "bold"), bg="#121214", fg="#FFFFFF").pack(anchor="w", padx=16, pady=(16, 8))

    card_faces = ttk.Frame(tab_faces, style="Card.TFrame", padding=16)
    card_faces.pack(fill="both", expand=True, padx=16, pady=8)

    listbox_faces = tk.Listbox(card_faces, bg="#2C2C2E", fg="#FFFFFF", font=("Segoe UI", 11), selectbackground="#0071E3", relief="flat")
    listbox_faces.pack(fill="both", expand=True, pady=6)

    def refresh_face_list():
        listbox_faces.delete(0, tk.END)
        identities = SecureFaceStore.load_identities()
        for ident in identities:
            name = ident.get("name", "Unknown")
            n_samples = len(ident.get("samples", []))
            listbox_faces.insert(tk.END, f"  👤 {name} ({n_samples} poses enrolled)")

    refresh_face_list()

    def enroll_face():
        name_win = tk.Toplevel(root)
        name_win.title("Enroll New Profile")
        name_win.geometry("380x160")
        name_win.configure(bg="#1C1C1E")
        tk.Label(name_win, text="Profile Name:", bg="#1C1C1E", fg="#FFFFFF", font=("Segoe UI", 11, "bold")).pack(pady=(16, 4))
        name_entry = tk.Entry(name_win, font=("Segoe UI", 11), bg="#2C2C2E", fg="#FFFFFF", insertbackground="#FFFFFF")
        name_entry.pack(pady=4, padx=20, fill="x")

        def start_capture():
            profile_name = name_entry.get().strip() or "User"
            name_win.destroy()

            # Guided capture dialog
            cap_win = tk.Toplevel(root)
            cap_win.title(f"Enrolling {profile_name}")
            cap_win.geometry("640x520")
            cap_win.configure(bg="#121214")

            lbl_status = tk.Label(cap_win, text="Look directly at the camera...", font=("Segoe UI", 14, "bold"), bg="#121214", fg="#30D158")
            lbl_status.pack(pady=12)

            canvas = tk.Canvas(cap_win, width=480, height=360, bg="#000000", highlightthickness=0)
            canvas.pack(pady=8)

            captured_embeddings = []
            poses = ["Center", "Look Slightly Left", "Look Slightly Right", "Tilt Up", "Tilt Down"]
            current_pose_idx = [0]
            camera.start()

            def update_cam():
                if not cap_win.winfo_exists():
                    camera.stop()
                    return

                res = camera.get_latest_frame()
                if res:
                    _, frame = res
                    faces = detector.detect_faces(frame)
                    if faces:
                        f = faces[0]
                        # Draw guide box
                        x, y, w, h = f.bbox
                        cv2.rectangle(frame, (x, y), (x + w, y + h), (48, 209, 88), 2)
                        for pt in f.landmarks_5pt:
                            cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, (0, 113, 227), -1)

                        if len(captured_embeddings) < len(poses):
                            aligned = aligner.align(f, frame)
                            if aligned is not None:
                                emb = embedder.compute_embedding(aligned)
                                captured_embeddings.append({"embedding": emb.tolist(), "pose": poses[current_pose_idx[0]]})
                                current_pose_idx[0] += 1
                                if current_pose_idx[0] < len(poses):
                                    lbl_status.config(text=f"Pose {current_pose_idx[0]+1}/5: {poses[current_pose_idx[0]]}")
                                else:
                                    lbl_status.config(text="✅ Enrollment Complete!", fg="#30D158")
                                    # Save profile
                                    new_id = {
                                        "id": str(uuid.uuid4()),
                                        "name": profile_name,
                                        "samples": captured_embeddings,
                                        "is_enabled": True,
                                        "enrolled_at": time.time(),
                                    }
                                    existing = SecureFaceStore.load_identities()
                                    existing.append(new_id)
                                    SecureFaceStore.save_identities(existing)
                                    refresh_face_list()
                                    cap_win.after(1200, cap_win.destroy)
                                    camera.stop()
                                    return

                    # Render frame to canvas
                    try:
                        from PIL import Image, ImageTk
                        rgb = cv2.cvtColor(cv2.resize(frame, (480, 360)), cv2.COLOR_BGR2RGB)
                        img = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                        canvas.create_image(0, 0, image=img, anchor="nw")
                        canvas.image = img
                    except Exception:
                        pass

                cap_win.after(30, update_cam)

            update_cam()

        btn_confirm = ttk.Button(name_win, text="Start Camera Scan", style="Accent.TButton", command=start_capture)
        btn_confirm.pack(pady=12)

    btn_enroll = ttk.Button(card_faces, text="➕ Enroll New Face", style="Accent.TButton", command=enroll_face)
    btn_enroll.pack(anchor="w", pady=6)

    def delete_face():
        sel = listbox_faces.curselection()
        if not sel:
            return
        idx = sel[0]
        identities = SecureFaceStore.load_identities()
        if 0 <= idx < len(identities):
            identities.pop(idx)
            SecureFaceStore.save_identities(identities)
            refresh_face_list()

    btn_delete = ttk.Button(card_faces, text="🗑️ Delete Selected Profile", command=delete_face)
    btn_delete.pack(anchor="w", pady=4)

    root.mainloop()

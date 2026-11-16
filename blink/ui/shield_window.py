"""
Blink Shield Authentication Window.
Provides an Apple-style modal Face ID unlock dialog with Dynamic Island card animation
and fallback passcode verification for protected applications, folders, and files.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.system.platform_info import IS_WINDOWS
from blink.vision.camera import CameraManager
from blink.vision.detector import FaceDetector
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder
from blink.vision.liveness import LivenessAnalyzer, LivenessDecisionKind

if not IS_WINDOWS:
    import cairo
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
    from blink.ui.notch_overlay import AnimationAssets
else:
    import tkinter as tk
    from tkinter import ttk
    from blink.ui.notch_overlay_win import AnimationAssetsWin as AnimationAssets


class BlinkShieldWindow:
    """Entrypoint factory for creating the platform-specific shield window."""

    @classmethod
    def launch(
        cls,
        target_type: str = "app",
        target_name: str = "Application",
        target_key: str = "",
        target_icon: Optional[str] = None,
        on_success: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> bool:
        if IS_WINDOWS:
            return cls._launch_windows(target_type, target_name, target_key, target_icon, on_success, on_cancel)
        return cls._launch_linux(target_type, target_name, target_key, target_icon, on_success, on_cancel)

    @classmethod
    def _launch_linux(
        cls,
        target_type: str,
        target_name: str,
        target_key: str,
        target_icon: Optional[str],
        on_success: Optional[Callable[[], None]],
        on_cancel: Optional[Callable[[], None]],
    ) -> bool:
        win = LinuxBlinkShieldWindow(
            target_type=target_type,
            target_name=target_name,
            target_key=target_key,
            target_icon=target_icon,
            on_success=on_success,
            on_cancel=on_cancel,
        )
        win.show_all()
        Gtk.main()
        return win.authenticated

    @classmethod
    def _launch_windows(
        cls,
        target_type: str,
        target_name: str,
        target_key: str,
        target_icon: Optional[str],
        on_success: Optional[Callable[[], None]],
        on_cancel: Optional[Callable[[], None]],
    ) -> bool:
        win = WindowsBlinkShieldWindow(
            target_type=target_type,
            target_name=target_name,
            target_key=target_key,
            target_icon=target_icon,
            on_success=on_success,
            on_cancel=on_cancel,
        )
        win.run()
        return win.authenticated


if not IS_WINDOWS:
    class LinuxBlinkShieldWindow(Gtk.Window):
        """GTK3 Modern Floating Shield Card for Linux."""

        def __init__(
            self,
            target_type: str = "app",
            target_name: str = "Application",
            target_key: str = "",
            target_icon: Optional[str] = None,
            on_success: Optional[Callable[[], None]] = None,
            on_cancel: Optional[Callable[[], None]] = None,
        ):
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
            self.target_type = target_type
            self.target_name = target_name
            self.target_key = target_key
            self.target_icon = target_icon
            self.on_success = on_success
            self.on_cancel = on_cancel

            self.authenticated = False
            self._camera_running = False
            self._anim_running = True
            self.anim_tick = 0.0

            # Animation state: 0 = idle/scanning, 1 = success, 2 = failure
            self.anim_state = 0
            self.idle_idx = 0
            self.unlock_idx = 0
            self.failure_idx = 0

            # Preload Face ID animation assets
            AnimationAssets.load_all(target_size=116)

            # Window setup
            self.set_title(f"Blink Lock — {self.target_name}")
            self.set_default_size(360, 430)
            self.set_resizable(False)
            self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(False)

            # Dark theme visual styling
            self._apply_styling()

            # Main container
            main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            main_box.set_margin_top(24)
            main_box.set_margin_bottom(24)
            main_box.set_margin_start(24)
            main_box.set_margin_end(24)
            self.add(main_box)

            # Header: Target Icon & Title
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            header_box.set_halign(Gtk.Align.CENTER)

            icon_widget = self._create_icon_widget(target_type, target_icon)
            header_box.pack_start(icon_widget, False, False, 0)

            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl_title = Gtk.Label()
            lbl_title.set_markup(f"<span font_desc='Plus Jakarta Sans Bold 14' color='#FFFFFF'>{self.target_name}</span>")
            lbl_title.set_xalign(0.0)

            lbl_sub = Gtk.Label()
            sub_text = "Protected by Blink" if target_type == "app" else f"Protected {target_type.title()}"
            lbl_sub.set_markup(f"<span font_desc='Plus Jakarta Sans 11' color='#8E8E93'>{sub_text}</span>")
            lbl_sub.set_xalign(0.0)

            title_box.pack_start(lbl_title, False, False, 0)
            title_box.pack_start(lbl_sub, False, False, 0)
            header_box.pack_start(title_box, False, False, 0)
            main_box.pack_start(header_box, False, False, 0)

            # Center: Apple Face ID Animated Drawing Area
            self.draw_area = Gtk.DrawingArea()
            self.draw_area.set_size_request(130, 130)
            self.draw_area.connect("draw", self._on_draw_face_id)
            main_box.pack_start(self.draw_area, False, False, 4)

            # Status label
            self.lbl_status = Gtk.Label()
            self.lbl_status.set_markup("<span font_desc='Plus Jakarta Sans Medium 12' color='#98989D'>Looking for face...</span>")
            main_box.pack_start(self.lbl_status, False, False, 0)

            # Passcode Container (hidden by default)
            self.passcode_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.passcode_box.set_no_show_all(True)

            self.pwd_entry = Gtk.Entry()
            self.pwd_entry.set_visibility(False)
            self.pwd_entry.set_placeholder_text("System Password")
            self.pwd_entry.connect("activate", self._on_passcode_submitted)
            self.passcode_box.pack_start(self.pwd_entry, False, False, 0)

            pwd_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_pwd_unlock = Gtk.Button(label="Unlock")
            btn_pwd_unlock.get_style_context().add_class("btn-primary")
            btn_pwd_unlock.connect("clicked", self._on_passcode_submitted)

            btn_pwd_cancel = Gtk.Button(label="Cancel")
            btn_pwd_cancel.get_style_context().add_class("btn-secondary")
            btn_pwd_cancel.connect("clicked", self._on_cancel_clicked)

            pwd_btn_box.pack_start(btn_pwd_cancel, True, True, 0)
            pwd_btn_box.pack_start(btn_pwd_unlock, True, True, 0)
            self.passcode_box.pack_start(pwd_btn_box, False, False, 0)
            main_box.pack_start(self.passcode_box, False, False, 0)

            # Fallback Actions: "Enter Passcode" & "Cancel"
            self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.btn_passcode_toggle = Gtk.Button(label="Enter Passcode")
            self.btn_passcode_toggle.get_style_context().add_class("btn-text")
            self.btn_passcode_toggle.connect("clicked", self._toggle_passcode_view)

            self.btn_cancel = Gtk.Button(label="Cancel")
            self.btn_cancel.get_style_context().add_class("btn-secondary")
            self.btn_cancel.connect("clicked", self._on_cancel_clicked)

            self.action_box.pack_start(self.btn_cancel, True, True, 0)
            self.action_box.pack_start(self.btn_passcode_toggle, True, True, 0)
            main_box.pack_start(self.action_box, False, False, 0)

            # Window events
            self.connect("destroy", self._on_window_destroy)
            self.connect("key-press-event", self._on_key_press)

            # Start 60 FPS animation loop
            GLib.timeout_add(16, self._on_anim_tick)

            # Start background biometric scan thread
            threading.Thread(target=self._run_biometric_scan, daemon=True).start()

        def _apply_styling(self) -> None:
            css = b"""
            window {
                background-color: #121214;
                border: 1px solid #2C2C2E;
                border-radius: 20px;
            }
            entry {
                background-color: #1C1C1E;
                color: #FFFFFF;
                border: 1px solid #3A3A3C;
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 13px;
            }
            entry:focus {
                border-color: #0A84FF;
            }
            .btn-primary {
                background-color: #0A84FF;
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 10px;
                padding: 8px 16px;
                border: none;
            }
            .btn-primary:hover {
                background-color: #0071E3;
            }
            .btn-secondary {
                background-color: #2C2C2E;
                color: #E5E5EA;
                font-weight: 500;
                border-radius: 10px;
                padding: 8px 16px;
                border: none;
            }
            .btn-secondary:hover {
                background-color: #3A3A3C;
            }
            .btn-text {
                background-color: transparent;
                color: #0A84FF;
                font-weight: 600;
                border: none;
                padding: 8px;
            }
            """
            provider = Gtk.CssProvider()
            provider.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        def _create_icon_widget(self, target_type: str, icon_hint: Optional[str]) -> Gtk.Widget:
            size = 40
            theme = Gtk.IconTheme.get_default()

            if icon_hint:
                if os.path.exists(icon_hint):
                    try:
                        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_hint, size, size, True)
                        return Gtk.Image.new_from_pixbuf(pb)
                    except Exception:
                        pass
                if theme.has_icon(icon_hint):
                    return Gtk.Image.new_from_icon_name(icon_hint, Gtk.IconSize.DND)

            fallback = "folder" if target_type == "folder" else ("application-x-executable" if target_type == "app" else "text-x-generic")
            return Gtk.Image.new_from_icon_name(fallback, Gtk.IconSize.DND)

        def _on_draw_face_id(self, widget, cr):
            w = widget.get_allocated_width()
            h = widget.get_allocated_height()
            cx = w / 2.0
            cy = h / 2.0
            card_sz = 116.0
            rad = 22.0

            # Draw outer squircle card background
            cr.save()
            cr.translate(cx - card_sz / 2.0, cy - card_sz / 2.0)
            cr.new_sub_path()
            cr.arc(card_sz - rad, rad, rad, -math.pi / 2, 0)
            cr.arc(card_sz - rad, card_sz - rad, rad, 0, math.pi / 2)
            cr.arc(rad, card_sz - rad, rad, math.pi / 2, math.pi)
            cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
            cr.close_path()

            # Fill obsidian squircle
            cr.set_source_rgb(0.06, 0.06, 0.07)
            cr.fill_preserve()

            # Subtle border
            if self.anim_state == 1:
                cr.set_source_rgba(0.2, 0.85, 0.4, 0.5)  # Green glow on success
            elif self.anim_state == 2:
                cr.set_source_rgba(1.0, 0.25, 0.25, 0.5)  # Red on failure
            else:
                cr.set_source_rgba(0.25, 0.25, 0.28, 0.4)
            cr.set_line_width(1.5)
            cr.stroke()
            cr.restore()

            # Render cached animation surface
            cr.save()
            cr.translate(cx - card_sz / 2.0, cy - card_sz / 2.0)

            surf = None
            if self.anim_state == 0 and AnimationAssets.idle_surfaces:
                surf = AnimationAssets.idle_surfaces[self.idle_idx % len(AnimationAssets.idle_surfaces)]
            elif self.anim_state == 1 and AnimationAssets.unlock_surfaces:
                surf = AnimationAssets.unlock_surfaces[self.unlock_idx % len(AnimationAssets.unlock_surfaces)]
            elif self.anim_state == 2 and AnimationAssets.failure_surfaces:
                surf = AnimationAssets.failure_surfaces[self.failure_idx % len(AnimationAssets.failure_surfaces)]

            if surf:
                cr.set_source_surface(surf, 0, 0)
                cr.paint()

            cr.restore()

        def _on_anim_tick(self) -> bool:
            if not self._anim_running:
                return False

            self.anim_tick += 0.016
            if self.anim_state == 0:
                self.idle_idx = (self.idle_idx + 1) % max(1, len(AnimationAssets.idle_surfaces))
            elif self.anim_state == 1:
                if self.unlock_idx < len(AnimationAssets.unlock_surfaces) - 1:
                    self.unlock_idx += 1
            elif self.anim_state == 2:
                if self.failure_idx < len(AnimationAssets.failure_surfaces) - 1:
                    self.failure_idx += 1

            self.draw_area.queue_draw()
            return True

        def _run_biometric_scan(self) -> None:
            settings = BlinkSettings.load()
            identities = SecureFaceStore.load_identities()
            active_identities = [i for i in identities if i.get("is_enabled", True)]

            if not active_identities:
                GLib.idle_add(self._trigger_failure, "No Face Profiles Enrolled")
                return

            cam = CameraManager()
            if not cam.start(device_index=settings.camera_index):
                GLib.idle_add(self._trigger_failure, "Camera Not Available")
                return

            self._camera_running = True
            detector = FaceDetector()
            aligner = FaceAligner()
            embedder = ArcFaceEmbedder()
            liveness = LivenessAnalyzer()

            t0 = time.time()
            timeout = 4.0
            matched_user = None

            try:
                while self._camera_running and (time.time() - t0) < timeout:
                    res = cam.get_latest_frame()
                    if res is None:
                        time.sleep(0.015)
                        continue

                    _, frame = res
                    faces = detector.detect_faces(frame)
                    if faces:
                        dominant = FaceDetector.select_dominant_face(faces, frame_width=frame.shape[1])
                        if dominant is not None:
                            aligned = aligner.align(dominant, frame)
                            if aligned is not None:
                                is_live = not settings.liveness_checks_enabled
                                if settings.liveness_checks_enabled:
                                    fx, fy, fw, fh = dominant.bbox
                                    crop = frame[max(0, fy):min(frame.shape[0], fy + fh), max(0, fx):min(frame.shape[1], fx + fw)]
                                    dec = liveness.observe(frame, dominant, crop)
                                    if dec.kind == LivenessDecisionKind.CONFIRMED:
                                        is_live = True
                                    elif dec.kind == LivenessDecisionKind.DENIED:
                                        break

                                emb = embedder.compute_embedding(aligned, flip_augment=True)
                                scored = embedder.score_identities(emb, active_identities)
                                matched = embedder.best_match(scored, settings.match_threshold)
                                if matched and is_live:
                                    matched_user = matched[0]["name"]
                                    break
                    time.sleep(0.016)

            finally:
                cam.stop()
                self._camera_running = False

            if matched_user:
                GLib.idle_add(self._trigger_success, f"Verified as {matched_user}")
            else:
                GLib.idle_add(self._trigger_failure, "Face Not Recognized")

        def _trigger_success(self, text: str) -> None:
            self.authenticated = True
            self.anim_state = 1
            self.unlock_idx = 0
            self.lbl_status.set_markup(f"<span font_desc='Plus Jakarta Sans Bold 12' color='#34C759'>✓ {text}</span>")

            # Allow unlock blossom animation to play for 450ms then resolve
            def _finish():
                if self.on_success:
                    self.on_success()
                self._close_window()

            GLib.timeout_add(450, _finish)

        def _trigger_failure(self, reason: str) -> None:
            self.anim_state = 2
            self.failure_idx = 0
            self.lbl_status.set_markup(f"<span font_desc='Plus Jakarta Sans 12' color='#FF453A'>{reason}</span>")
            # Automatically expose passcode entry for seamless fallback
            self._show_passcode_view()

        def _show_passcode_view(self) -> None:
            self.action_box.hide()
            self.passcode_box.show_all()
            self.pwd_entry.grab_focus()

        def _toggle_passcode_view(self, btn) -> None:
            self._camera_running = False
            self._show_passcode_view()

        def _on_passcode_submitted(self, widget) -> None:
            pwd = self.pwd_entry.get_text()
            if not pwd:
                return

            if SecureCredentialManager.verify_password(pwd):
                self._trigger_success("Password Authenticated")
            else:
                self.lbl_status.set_markup("<span font_desc='Plus Jakarta Sans 12' color='#FF453A'>Incorrect Password</span>")
                self.pwd_entry.set_text("")
                self.pwd_entry.grab_focus()

        def _on_cancel_clicked(self, btn) -> None:
            self.authenticated = False
            if self.on_cancel:
                self.on_cancel()
            self._close_window()

        def _on_key_press(self, widget, event):
            if event.keyval == Gdk.KEY_Escape:
                self._on_cancel_clicked(None)
                return True
            return False

        def _close_window(self) -> None:
            self._camera_running = False
            self._anim_running = False
            self.destroy()
            Gtk.main_quit()

        def _on_window_destroy(self, widget) -> None:
            self._camera_running = False
            self._anim_running = False
            Gtk.main_quit()


if IS_WINDOWS:
    class WindowsBlinkShieldWindow:
        """Tkinter Floating Shield Card for Windows."""

        def __init__(
            self,
            target_type: str = "app",
            target_name: str = "Application",
            target_key: str = "",
            target_icon: Optional[str] = None,
            on_success: Optional[Callable[[], None]] = None,
            on_cancel: Optional[Callable[[], None]] = None,
        ):
            self.target_type = target_type
            self.target_name = target_name
            self.target_key = target_key
            self.target_icon = target_icon
            self.on_success = on_success
            self.on_cancel = on_cancel

            self.authenticated = False
            self._camera_running = False
            self._anim_running = True

            self.anim_state = 0
            self.frame_idx = 0

            self.root = tk.Tk()
            self.root.title(f"Blink Lock — {self.target_name}")
            self.root.geometry("360x420")
            self.root.resizable(False, False)
            self.root.configure(bg="#121214")
            self.root.attributes("-topmost", True)

            # Center on screen
            self.root.eval("tk::PlaceWindow . center")

            AnimationAssets.load_all(target_size=100)

            # Layout widgets
            lbl_title = tk.Label(self.root, text=self.target_name, font=("Segoe UI", 14, "bold"), fg="#FFFFFF", bg="#121214")
            lbl_title.pack(pady=(20, 2))

            sub_text = "Protected by Blink" if target_type == "app" else f"Protected {target_type.title()}"
            lbl_sub = tk.Label(self.root, text=sub_text, font=("Segoe UI", 10), fg="#8E8E93", bg="#121214")
            lbl_sub.pack(pady=(0, 10))

            self.canvas = tk.Canvas(self.root, width=110, height=110, bg="#121214", highlightthickness=0)
            self.canvas.pack(pady=5)

            self.lbl_status = tk.Label(self.root, text="Looking for face...", font=("Segoe UI", 11), fg="#8E8E93", bg="#121214")
            self.lbl_status.pack(pady=5)

            # Passcode frame
            self.pwd_frame = tk.Frame(self.root, bg="#121214")
            self.pwd_entry = tk.Entry(self.pwd_frame, show="●", font=("Segoe UI", 12), bg="#1C1C1E", fg="#FFFFFF", insertbackground="#FFFFFF")
            self.pwd_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.pwd_entry.bind("<Return>", lambda e: self._on_passcode_submitted())

            btn_pwd = tk.Button(self.pwd_frame, text="Unlock", bg="#0A84FF", fg="#FFFFFF", font=("Segoe UI", 10, "bold"), command=self._on_passcode_submitted, relief=tk.FLAT)
            btn_pwd.pack(side=tk.RIGHT, padx=5)

            # Bottom actions
            self.action_frame = tk.Frame(self.root, bg="#121214")
            self.action_frame.pack(side=tk.BOTTOM, pady=20, fill=tk.X, padx=20)

            btn_cancel = tk.Button(self.action_frame, text="Cancel", bg="#2C2C2E", fg="#FFFFFF", font=("Segoe UI", 10), command=self._on_cancel, relief=tk.FLAT)
            btn_cancel.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

            btn_passcode = tk.Button(self.action_frame, text="Enter Passcode", bg="#1C1C1E", fg="#0A84FF", font=("Segoe UI", 10, "bold"), command=self._show_passcode, relief=tk.FLAT)
            btn_passcode.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=4)

            self.root.bind("<Escape>", lambda e: self._on_cancel())
            self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)

            # Start loops
            self.root.after(16, self._on_anim_tick)
            threading.Thread(target=self._run_biometric_scan, daemon=True).start()

        def run(self) -> None:
            self.root.mainloop()

        def _on_anim_tick(self) -> None:
            if not self._anim_running:
                return

            frames = (
                AnimationAssets.idle_frames if self.anim_state == 0
                else (AnimationAssets.unlock_frames if self.anim_state == 1 else AnimationAssets.failure_frames)
            )

            if frames:
                self.frame_idx = (self.frame_idx + 1) % len(frames)
                img = frames[self.frame_idx]
                self.canvas.delete("all")
                self.canvas.create_image(55, 55, image=img)

            self.root.after(16, self._on_anim_tick)

        def _run_biometric_scan(self) -> None:
            settings = BlinkSettings.load()
            identities = SecureFaceStore.load_identities()
            active_identities = [i for i in identities if i.get("is_enabled", True)]
            if not active_identities:
                self.root.after(0, lambda: self._trigger_failure("No Faces Enrolled"))
                return

            cam = CameraManager()
            if not cam.start(device_index=settings.camera_index):
                self.root.after(0, lambda: self._trigger_failure("Camera Not Available"))
                return

            self._camera_running = True
            detector = FaceDetector()
            aligner = FaceAligner()
            embedder = ArcFaceEmbedder()

            t0 = time.time()
            matched_name = None

            try:
                while self._camera_running and (time.time() - t0) < 4.0:
                    res = cam.get_latest_frame()
                    if res is None:
                        time.sleep(0.015)
                        continue
                    _, frame = res
                    faces = detector.detect_faces(frame)
                    if faces:
                        dominant = FaceDetector.select_dominant_face(faces, frame_width=frame.shape[1])
                        if dominant:
                            aligned = aligner.align(dominant, frame)
                            if aligned is not None:
                                emb = embedder.compute_embedding(aligned)
                                scored = embedder.score_identities(emb, active_identities)
                                matched = embedder.best_match(scored, settings.match_threshold)
                                if matched:
                                    matched_name = matched[0]["name"]
                                    break
                    time.sleep(0.016)
            finally:
                cam.stop()

            if matched_name:
                self.root.after(0, lambda: self._trigger_success(f"Verified as {matched_name}"))
            else:
                self.root.after(0, lambda: self._trigger_failure("Face Not Recognized"))

        def _trigger_success(self, text: str) -> None:
            self.authenticated = True
            self.anim_state = 1
            self.frame_idx = 0
            self.lbl_status.config(text=f"✓ {text}", fg="#34C759")
            self.root.after(450, self._finish_success)

        def _finish_success(self) -> None:
            if self.on_success:
                self.on_success()
            self._close()

        def _trigger_failure(self, text: str) -> None:
            self.anim_state = 2
            self.frame_idx = 0
            self.lbl_status.config(text=text, fg="#FF453A")
            self._show_passcode()

        def _show_passcode(self) -> None:
            self._camera_running = False
            self.pwd_frame.pack(pady=10, fill=tk.X, padx=20)
            self.pwd_entry.focus_set()

        def _on_passcode_submitted(self) -> None:
            pwd = self.pwd_entry.get()
            if SecureCredentialManager.verify_password(pwd):
                self._trigger_success("Password Authenticated")
            else:
                self.lbl_status.config(text="Incorrect Password", fg="#FF453A")
                self.pwd_entry.delete(0, tk.END)

        def _on_cancel(self) -> None:
            self.authenticated = False
            if self.on_cancel:
                self.on_cancel()
            self._close()

        def _close(self) -> None:
            self._camera_running = False
            self._anim_running = False
            self.root.destroy()

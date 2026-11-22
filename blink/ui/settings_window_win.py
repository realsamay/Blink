import customtkinter as ctk
import os
import sys
from pathlib import Path
import threading
import time

# Mocking necessary components to mirror Linux UI structure
# In a real scenario, these would import from blink core/vision
class MockCoordinator:
    def test_scan(self):
        print("Mock test scan initiated")

# Appearance setup
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class SettingsWindowWin(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Blink")
        # Match Linux UI size request
        self.geometry("940x660")
        self.minsize(800, 560)

        # Style colors inspired by MACOS_CSS in Linux version
        self.bg_color = "#14141A"
        self.sidebar_color = "#101016"
        self.card_color = "#24242C"
        self.text_color = "#FFFFFF"
        self.accent_color = "#0A84FF"

        # Configure Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # 1. Sidebar
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=self.sidebar_color)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=1)

        # Logo/Brand
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="Blink",
                                       font=ctk.CTkFont(size=20, weight="bold"), text_color=self.text_color)
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        self.sub_label = ctk.CTkLabel(self.sidebar_frame, text="Face ID System",
                                       font=ctk.CTkFont(size=12), text_color="#A1A1A6")
        self.sub_label.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        # Sidebar Buttons (mirroring Linux nav items)
        self.nav_buttons = []
        nav_items = [
            ("Face ID & Password", "face_id"),
            ("Blink Lock", "lock"),
            ("Camera", "camera"),
            ("Anti-Spoofing", "liveness"),
            ("Diagnostics", "diagnostics"),
            ("About", "about")
        ]

        for i, (text, page_id) in enumerate(nav_items):
            btn = ctk.CTkButton(self.sidebar_frame, text=text, fg_color="transparent",
                                hover_color="#2C2C34", anchor="w",
                                command=lambda p=page_id: self.switch_page(p))
            btn.grid(row=i+2, column=0, padx=10, pady=2, sticky="ew")
            self.nav_buttons.append(btn)

        # 2. Main Content
        self.main_frame = ctk.CTkFrame(self, corner_radius=10, fg_color=self.bg_color)
        self.main_frame.grid(row=0, column=1, padx=0, pady=0, sticky="nsew")

        self.pages = {}
        self.setup_pages()
        self.switch_page("face_id")

    def setup_pages(self):
        # Create page containers
        for page_id in ["face_id", "lock", "camera", "liveness", "diagnostics", "about"]:
            page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
            self.pages[page_id] = page

            # Content
            if page_id == "about":
                self.setup_about_page(page)
            elif page_id == "lock":
                self.setup_lock_page(page)
            else:
                lbl = ctk.CTkLabel(page, text=f"{page_id.replace('_', ' ').capitalize()} Page", font=ctk.CTkFont(size=20, weight="bold"))
                lbl.pack(padx=20, pady=20)

        self.pages["face_id"].pack(fill="both", expand=True)

    def setup_lock_page(self, page):
        from blink.core.lock_manager import BlinkLockManager
        mgr = BlinkLockManager.get_instance()

        lbl = ctk.CTkLabel(page, text="Blink Lock", font=ctk.CTkFont(size=20, weight="bold"))
        lbl.pack(padx=20, pady=20, anchor="w")

        # Enable switch
        self.lock_switch = ctk.CTkSwitch(page, text="Enable Blink Lock", command=lambda: mgr.set_enabled(self.lock_switch.get()))
        self.lock_switch.set(mgr.is_enabled())
        self.lock_switch.pack(padx=20, pady=10, anchor="w")

        # List of items
        self.lock_list_frame = ctk.CTkScrollableFrame(page, label_text="Protected Items")
        self.lock_list_frame.pack(padx=20, pady=10, fill="both", expand=True)

        self.refresh_lock_list()

        # Add buttons
        btn_frame = ctk.CTkFrame(page, fg_color="transparent")
        btn_frame.pack(padx=20, pady=20, fill="x")

        ctk.CTkButton(btn_frame, text="Add App", command=self.add_app_dialog).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Refresh", command=self.refresh_lock_list).pack(side="left", padx=5)

    def refresh_lock_list(self):
        from blink.core.lock_manager import BlinkLockManager
        mgr = BlinkLockManager.get_instance()

        # Clear existing
        for widget in self.lock_list_frame.winfo_children():
            widget.destroy()

        # Add items
        apps = mgr.get_locked_apps()
        items = mgr.get_locked_items()

        for app in apps:
            frame = ctk.CTkFrame(self.lock_list_frame)
            frame.pack(fill="x", pady=5)
            ctk.CTkLabel(frame, text=f"🔒 App: {app['name']}").pack(side="left", padx=10)
            ctk.CTkButton(frame, text="Remove", width=60, fg_color="#FF3B30",
                          command=lambda k=app['key']: self.remove_app(k)).pack(side="right", padx=10)

        for item in items:
            frame = ctk.CTkFrame(self.lock_list_frame)
            frame.pack(fill="x", pady=5)
            ctk.CTkLabel(frame, text=f"📦 {item['type'].capitalize()}: {item['name']}").pack(side="left", padx=10)
            # Remove item logic... (omitted for brevity)

    def add_app_dialog(self):
        # Placeholder for dialog, use simple input for now
        from tkinter import simpledialog
        app_name = simpledialog.askstring("Add App", "Enter App Name (e.g. chrome):")
        if app_name:
            from blink.core.lock_manager import BlinkLockManager
            BlinkLockManager.get_instance().add_locked_app(app_name, app_name)
            self.refresh_lock_list()

    def remove_app(self, key):
        from blink.core.lock_manager import BlinkLockManager
        BlinkLockManager.get_instance().remove_locked_app(key)
        self.refresh_lock_list()

    def setup_about_page(self, page):
        lbl = ctk.CTkLabel(page, text="About Blink", font=ctk.CTkFont(size=20, weight="bold"))
        lbl.pack(padx=20, pady=20)

        self.btn_update = ctk.CTkButton(page, text="Check for Updates", command=self.check_updates_event)
        self.btn_update.pack(padx=20, pady=10)

        self.update_label = ctk.CTkLabel(page, text="")
        self.update_label.pack(padx=20, pady=10)

        self.btn_install = ctk.CTkButton(page, text="Install Update", fg_color="#34C759", command=self.install_update_event)
        self.btn_install.pack(padx=20, pady=10)
        self.btn_install.pack_forget() # Initially hidden
        self.latest_exe_path = None

    def check_updates_event(self):
        from blink.core.updater import check_for_updates, DOWNLOAD_CACHE_DIR
        self.update_label.configure(text="Checking...")
        self.btn_install.pack_forget()

        # Run check in thread to avoid freezing UI
        def check():
            res = check_for_updates()
            if res.get("has_update"):
                self.update_label.configure(text=f"Update available: {res.get('latest_version')}")
                self.btn_install.pack(padx=20, pady=10)
                # Store URL and filename for download
                self.latest_exe_url = res.get("exe_url")
                self.latest_exe_filename = res.get("exe_filename")
            else:
                self.update_label.configure(text="Blink is up to date.")
                self.btn_install.pack_forget()

        threading.Thread(target=check, daemon=True).start()

    def install_update_event(self):
        from blink.core.updater import download_package, install_windows_update, DOWNLOAD_CACHE_DIR

        if not hasattr(self, 'latest_exe_url') or not self.latest_exe_url:
            return

        self.update_label.configure(text="Downloading...")
        self.btn_install.pack_forget()

        def download():
            dest_path = DOWNLOAD_CACHE_DIR / self.latest_exe_filename
            success, msg = download_package(self.latest_exe_url, dest_path)

            if success:
                self.update_label.configure(text="Download complete. Installing...")
                # Call the update helper
                install_windows_update(dest_path)
            else:
                self.update_label.configure(text=f"Download failed: {msg}")
                self.btn_install.pack(padx=20, pady=10)

        threading.Thread(target=download, daemon=True).start()

    def switch_page(self, page_id):
        for p in self.pages.values():
            p.pack_forget()
        self.pages[page_id].pack(fill="both", expand=True)

if __name__ == "__main__":
    app = SettingsWindowWin()
    app.mainloop()

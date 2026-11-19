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

            # Content placeholder
            lbl = ctk.CTkLabel(page, text=f"{page_id.capitalize()} Page", font=ctk.CTkFont(size=20, weight="bold"))
            lbl.pack(padx=20, pady=20)

        self.pages["face_id"].pack(fill="both", expand=True)

    def switch_page(self, page_id):
        for p in self.pages.values():
            p.pack_forget()
        self.pages[page_id].pack(fill="both", expand=True)

if __name__ == "__main__":
    app = SettingsWindowWin()
    app.mainloop()

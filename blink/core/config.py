"""
Configuration management for Blink on Linux.
Persists settings to ~/.config/blink/settings.json with atomic save.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List


CONFIG_DIR = Path.home() / ".config" / "blink"
SETTINGS_FILE = CONFIG_DIR / "settings.json"


@dataclass
class BlinkSettings:
    is_face_unlock_enabled: bool = False
    match_threshold: float = 0.60
    face_detection_seconds: int = 5
    liveness_checks_enabled: bool = True
    liveness_mode: str = "light"  # "light" | "heavy"
    unlock_triggers: List[str] = field(default_factory=lambda: ["on_wake", "on_lock", "on_space", "on_activity"])
    auto_retry_once: bool = True
    show_unlock_animation: bool = True
    camera_index: int = 0
    auto_lock_idle_minutes: int = 15

    @classmethod
    def load(cls) -> BlinkSettings:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_FILE.is_file():
            settings = cls()
            settings.save()
            return settings

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            return cls(**filtered)
        except Exception as e:
            print(f"[BlinkSettings] Error loading {SETTINGS_FILE}: {e}. Using defaults.")
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        try:
            with tempfile.NamedTemporaryFile("w", dir=CONFIG_DIR, delete=False, encoding="utf-8") as tf:
                json.dump(data, tf, indent=2)
                temp_path = tf.name
            os.replace(temp_path, SETTINGS_FILE)
        except Exception as e:
            print(f"[BlinkSettings] Failed to save settings: {e}")

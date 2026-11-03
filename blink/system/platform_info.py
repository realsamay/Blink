"""
Cross-platform environment detection and path resolution for Blink.
Supports Linux (X11/Wayland, Cinnamon, GNOME, KDE) and Windows (10/11 64-bit).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path


IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_MACOS: bool = sys.platform == "darwin"


def get_config_dir() -> Path:
    """Returns the platform-standard configuration directory for Blink."""
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / "Blink"
        return Path.home() / ".config" / "blink"
    return Path.home() / ".config" / "blink"


def get_machine_id() -> str:
    """
    Returns a persistent, hardware-linked machine identifier.
    Linux: /etc/machine-id or /var/lib/dbus/machine-id.
    Windows: HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid.
    """
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                val, _ = winreg.QueryValueEx(key, "MachineGuid")
                if val:
                    return str(val).strip()
        except Exception:
            pass

        # Fallback using computer name + system drive serial/volume
        comp = os.environ.get("COMPUTERNAME", "BLINK-WIN")
        user = os.environ.get("USERNAME", "DEFAULT")
        return f"{comp}-{user}"

    # Linux paths
    for path_str in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
        p = Path(path_str)
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except Exception:
                pass

    return f"blink-default-id-{uuid.getnode()}"


def get_current_user() -> str:
    """Returns the current operating system username."""
    if IS_WINDOWS:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "default-user"
    return os.environ.get("USER") or os.environ.get("USERNAME") or "default-user"

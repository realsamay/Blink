"""
Application Discovery Engine.
Enumerates installed user-facing desktop applications on Linux & Windows
for biometric Blink Lock protection.
"""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from blink.system.platform_info import IS_WINDOWS


def list_installed_applications() -> List[Dict[str, Any]]:
    """Discovers all installed user-facing desktop applications.
    Returns a list of dicts:
    [
        {
            "name": "Google Chrome",
            "key": "google-chrome",
            "exec": "google-chrome-stable",
            "icon": "google-chrome",
            "wm_class": "google-chrome",
            "path": "/usr/share/applications/google-chrome.desktop",
            "comment": "Access the Internet"
        },
        ...
    ]
    """
    if IS_WINDOWS:
        return _list_installed_applications_windows()
    return _list_installed_applications_linux()


def _list_installed_applications_linux() -> List[Dict[str, Any]]:
    app_dirs = [
        Path.home() / ".local" / "share" / "applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]

    apps: List[Dict[str, Any]] = []
    seen_keys = set()
    seen_names = set()

    for adir in app_dirs:
        if not adir.exists():
            continue
        for p in sorted(adir.glob("*.desktop")):
            try:
                cp = configparser.RawConfigParser()
                cp.read(p, encoding="utf-8")
                if not cp.has_section("Desktop Entry"):
                    continue
                entry = cp["Desktop Entry"]

                if entry.get("Type") != "Application":
                    continue
                if entry.getboolean("NoDisplay", fallback=False):
                    continue

                name = entry.get("Name", "").strip()
                exec_cmd = entry.get("Exec", "").strip()
                icon = entry.get("Icon", "application-x-executable").strip()
                wm_class = entry.get("StartupWMClass", "").strip()
                comment = entry.get("Comment", "").strip()

                if not name or not exec_cmd:
                    continue

                # Strip arguments from Exec (e.g. "google-chrome %U" -> "google-chrome")
                base_exec = exec_cmd.split()[0].replace('"', '').replace("'", "")
                base_bin = Path(base_exec).name.lower()

                key = (wm_class or base_bin).strip().lower()

                # Clean key for common wrappers (e.g. flatpak run ...)
                if base_bin == "flatpak" and len(exec_cmd.split()) > 2:
                    key = exec_cmd.split()[-1].strip().lower()
                elif base_bin in ("python", "python3") and wm_class:
                    key = wm_class.lower()

                # Deduplicate by key & display name
                if key in seen_keys or name in seen_names:
                    continue

                seen_keys.add(key)
                seen_names.add(name)

                apps.append({
                    "name": name,
                    "key": key,
                    "exec": base_bin,
                    "icon": icon,
                    "wm_class": wm_class.lower() if wm_class else key,
                    "path": str(p),
                    "comment": comment,
                })
            except Exception:
                continue

    apps.sort(key=lambda a: a["name"].lower())
    return apps


def _list_installed_applications_windows() -> List[Dict[str, Any]]:
    apps: List[Dict[str, Any]] = []
    seen_keys = set()
    seen_names = set()

    # Search Start Menu shortcuts (.lnk)
    start_menu_dirs = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        start_menu_dirs.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    programdata = os.environ.get("ProgramData")
    if programdata:
        start_menu_dirs.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    for sdir in start_menu_dirs:
        if not sdir.exists():
            continue
        for lnk in sdir.rglob("*.lnk"):
            try:
                name = lnk.stem
                if name.lower().startswith("uninstall") or "readme" in name.lower() or "help" in name.lower():
                    continue

                key = name.lower().replace(" ", "")
                if key in seen_keys or name in seen_names:
                    continue

                seen_keys.add(key)
                seen_names.add(name)

                apps.append({
                    "name": name,
                    "key": key,
                    "exec": f"{name}.exe".lower(),
                    "icon": str(lnk),
                    "wm_class": key,
                    "path": str(lnk),
                    "comment": f"Windows Application ({name})",
                })
            except Exception:
                continue

    apps.sort(key=lambda a: a["name"].lower())
    return apps


def get_app_info(key_or_name: str) -> Dict[str, Any]:
    """Resolves detailed application info by key, name, or executable name.
    Falls back to a synthetic descriptor if not found in desktop entries.
    """
    clean = key_or_name.strip().lower()
    for app in list_installed_applications():
        if (
            app["key"] == clean
            or app["name"].lower() == clean
            or app["exec"].lower() == clean
            or clean in app["key"]
            or clean in app["name"].lower()
        ):
            return app
    return {
        "name": key_or_name.strip().title(),
        "key": clean,
        "exec": clean,
        "icon": "application-x-executable",
        "wm_class": clean,
        "path": "",
        "comment": f"Application ({key_or_name})",
    }

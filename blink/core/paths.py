"""
Centralized path and asset resolution for Blink.
Provides fail-safe, cross-platform resolution for neural models and visual assets
across source repositories, PyInstaller frozen bundles (onedir/onefile), and system packages.
Includes automatic background healing/download if model assets are missing.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional, List

from blink.system.platform_info import IS_WINDOWS, get_config_dir

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/realsamay/Blink/main"


def get_model_path(filename: str, auto_download: bool = True) -> Path:
    """
    Universally locates an ONNX neural model.
    Checks frozen PyInstaller bundles, relative package locations, system dirs,
    and downloads automatically to user AppData/config if missing.
    """
    candidates: List[Path] = []

    # 1. PyInstaller runtime extraction folder (sys._MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        candidates.append(meipass / "models" / filename)
        candidates.append(meipass / "blink" / "models" / filename)

    # 2. PyInstaller application executable folder (sys.executable parent)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "models" / filename)
        candidates.append(exe_dir / "blink" / "models" / filename)
        candidates.append(exe_dir / "_internal" / "models" / filename)
        candidates.append(exe_dir / "_internal" / "blink" / "models" / filename)

    # 3. Development / Source Tree relatives
    this_file = Path(__file__).resolve()
    # Paths relative to blink/core/paths.py:
    # parent.parent is <repo_root>/blink or <repo_root>
    candidates.append(this_file.parent.parent.parent / "models" / filename)
    candidates.append(this_file.parent.parent / "models" / filename)
    candidates.append(this_file.parent / "models" / filename)

    # 4. Standard Linux system installations
    candidates.append(Path("/usr/lib/blink/models") / filename)
    candidates.append(Path("/usr/share/blink/models") / filename)

    # 5. User Config/AppData persistent models directory
    user_models_dir = get_config_dir() / "models"
    candidates.append(user_models_dir / filename)

    # Search for an existing file
    for cand in candidates:
        try:
            if cand.is_file() and cand.stat().st_size > 1024:
                return cand
        except Exception:
            continue

    # If missing and auto_download is enabled, self-heal by downloading
    if auto_download:
        try:
            user_models_dir.mkdir(parents=True, exist_ok=True)
            target_file = user_models_dir / filename
            url = f"{GITHUB_RAW_BASE}/models/{filename}"
            print(f"[Paths] Model '{filename}' not found locally. Auto-downloading from {url}...")
            urllib.request.urlretrieve(url, str(target_file))
            if target_file.is_file() and target_file.stat().st_size > 1024:
                print(f"[Paths] Successfully downloaded and cached '{filename}' to {target_file}")
                return target_file
        except Exception as e:
            print(f"[Paths] Failed to auto-download '{filename}': {e}")

    # Fallback: return the user models location or first candidate
    return candidates[0] if candidates else Path(filename)


def get_resources_dir() -> Path:
    """Universally locates the resources directory containing UI videos, icons, and sounds."""
    candidates: List[Path] = []

    if hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        candidates.append(meipass / "blink" / "resources")
        candidates.append(meipass / "resources")

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "blink" / "resources")
        candidates.append(exe_dir / "_internal" / "blink" / "resources")
        candidates.append(exe_dir / "resources")
        candidates.append(exe_dir / "_internal" / "resources")

    this_file = Path(__file__).resolve()
    candidates.append(this_file.parent.parent / "resources")
    candidates.append(this_file.parent.parent.parent / "blink" / "resources")
    candidates.append(Path("/usr/lib/blink/blink/resources"))

    for cand in candidates:
        try:
            if cand.is_dir() and (cand / "final.png").is_file():
                return cand
        except Exception:
            continue

    return candidates[0] if candidates else this_file.parent.parent / "resources"


def get_resource_path(filename: str) -> Path:
    """Returns the full path to a named resource asset."""
    res_dir = get_resources_dir()
    return res_dir / filename

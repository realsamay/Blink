"""
In-app self-updater module for Blink.
Queries GitHub Releases for latest versions, changelogs, and performs
in-place Debian package downloads and installations via pkexec.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from blink import __version__ as CURRENT_VERSION

IS_WINDOWS = platform.system() == "Windows"
GITHUB_REPO = "realsamay/Blink"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DOWNLOAD_CACHE_DIR = Path(tempfile.gettempdir()) / "blink_updates"


def restart_service() -> None:
    """Restarts the background Blink daemon service."""
    if IS_WINDOWS:
        return
    try:
        subprocess.run(["systemctl", "--user", "restart", "blink.service"], check=False)
    except Exception as e:
        print(f"[Updater] Could not restart blink.service: {e}")


def parse_semver(version_str: str) -> Tuple[int, ...]:
    """Extract numeric semver tuple (e.g. 'v2.2.0' -> (2, 2, 0))."""
    cleaned = version_str.strip().lstrip("vV")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", cleaned)
    if not match:
        return (0, 0, 0)
    parts = [int(p) if p is not None else 0 for p in match.groups()]
    return tuple(parts)


def is_newer_version(latest_str: str, current_str: str) -> bool:
    """Returns True if latest_str is strictly newer than current_str."""
    return parse_semver(latest_str) > parse_semver(current_str)


def check_for_updates() -> Dict[str, Any]:
    """
    Checks GitHub Releases API for the latest published Blink release.
    Returns status, version metadata, changelog, and .deb download link.
    """
    req = urllib.request.Request(
        GITHUB_API_LATEST,
        headers={
            "User-Agent": f"Blink-Updater/{CURRENT_VERSION} (Linux; x86_64)",
            "Accept": "application/vnd.github.v3+json",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {
            "status": "error",
            "has_update": False,
            "current_version": CURRENT_VERSION,
            "error": f"Network error checking for updates: {e.reason if hasattr(e, 'reason') else e}",
        }
    except Exception as e:
        return {
            "status": "error",
            "has_update": False,
            "current_version": CURRENT_VERSION,
            "error": f"Failed to check for updates: {e}",
        }

    tag_name = payload.get("tag_name", "").strip()
    latest_version = tag_name.lstrip("vV")
    release_name = payload.get("name") or f"Blink {tag_name}"
    changelog = payload.get("body", "").strip()
    published_at = payload.get("published_at", "")
    html_url = payload.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")

    # Locate .deb asset
    deb_url = None
    deb_size = 0
    deb_filename = None
    assets = payload.get("assets", [])
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".deb") and ("amd64" in name or "x86_64" in name or "blink" in name):
            deb_url = asset.get("browser_download_url")
            deb_size = asset.get("size", 0)
            deb_filename = name
            break

    # If no specific amd64 deb matched, pick first .deb
    if not deb_url and not IS_WINDOWS:
        for asset in assets:
            if asset.get("name", "").endswith(".deb"):
                deb_url = asset.get("browser_download_url")
                deb_size = asset.get("size", 0)
                deb_filename = asset.get("name")
                break

    # If still no .deb on latest release, check recent releases for the latest Linux release
    if not deb_url and not IS_WINDOWS:
        try:
            req_all = urllib.request.Request(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases",
                headers={
                    "User-Agent": f"Blink-Updater/{CURRENT_VERSION} (Linux; x86_64)",
                    "Accept": "application/vnd.github.v3+json",
                }
            )
            with urllib.request.urlopen(req_all, timeout=10) as resp_all:
                all_releases = json.loads(resp_all.read().decode("utf-8"))
                for rel in all_releases:
                    for a in rel.get("assets", []):
                        aname = a.get("name", "")
                        if aname.endswith(".deb"):
                            deb_url = a.get("browser_download_url")
                            deb_size = a.get("size", 0)
                            deb_filename = aname
                            tag_name = rel.get("tag_name", "").strip()
                            latest_version = tag_name.lstrip("vV")
                            release_name = rel.get("name") or f"Blink {tag_name}"
                            changelog = rel.get("body", "").strip()
                            published_at = rel.get("published_at", "")
                            html_url = rel.get("html_url", html_url)
                            break
                    if deb_url:
                        break
        except Exception:
            pass

    # Locate .exe asset for Windows
    exe_url = None
    exe_size = 0
    exe_filename = None
    if IS_WINDOWS:
        for asset in assets:
            name = asset.get("name", "")
            if name.endswith(".exe") and "Blink" in name:
                exe_url = asset.get("browser_download_url")
                exe_size = asset.get("size", 0)
                exe_filename = name
                break

    has_update = is_newer_version(latest_version, CURRENT_VERSION)

    return {
        "status": "success",
        "has_update": has_update,
        "current_version": CURRENT_VERSION,
        "latest_version": latest_version,
        "tag_name": tag_name,
        "release_name": release_name,
        "changelog": changelog,
        "published_at": published_at,
        "deb_url": deb_url,
        "exe_url": exe_url,
        "deb_size": deb_size,
        "exe_size": exe_size,
        "deb_filename": deb_filename or f"blink_{latest_version}_amd64.deb",
        "exe_filename": exe_filename or f"Blink-{latest_version}.exe",
        "html_url": html_url,
        "error": None,
    }


def download_package(
    url: str,
    dest_path: Path,
    progress_callback: Optional[Callable[[float, int, int], None]] = None
) -> Tuple[bool, str]:
    """
    Downloads a package from url to dest_path with optional progress callback.
    Callback signature: progress_callback(fraction, downloaded_bytes, total_bytes)
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest_path.with_suffix(".tmp")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"Blink-Updater/{CURRENT_VERSION}"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response, open(temp_dest, "wb") as out_file:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024

            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    fraction = (downloaded / total_size) if total_size > 0 else 0.0
                    progress_callback(fraction, downloaded, total_size)

        if temp_dest.exists():
            os.replace(temp_dest, dest_path)
            return True, f"Downloaded {dest_path.name}"
        return False, "Downloaded file not found on disk"
    except Exception as e:
        if temp_dest.exists():
            try:
                temp_dest.unlink()
            except OSError:
                pass
        return False, str(e)


def _sync_local_checkout_if_needed() -> None:
    """If running from a local checkout, sync newly installed package files."""
    try:
        sys_lib = Path("/usr/lib/blink/blink")
        current_pkg = Path(__file__).resolve().parent.parent
        if sys_lib.is_dir() and current_pkg.is_dir() and sys_lib != current_pkg:
            for item in sys_lib.rglob("*"):
                if item.is_file() and "__pycache__" not in str(item):
                    rel = item.relative_to(sys_lib)
                    dest = current_pkg / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
    except Exception as e:
        print(f"[Updater] Notice: Local checkout sync skipped: {e}")


def install_deb_package(deb_path: Path) -> Tuple[bool, str]:
    """
    Installs the downloaded .deb package using pkexec (Polkit root elevation).
    Attempts apt-get install first, falling back to dpkg -i.
    """
    if not deb_path.is_file():
        return False, f"Package file not found at {deb_path}"

    deb_abs = str(deb_path.resolve())

    # Check pkexec availability
    if not shutil.which("pkexec"):
        return False, "pkexec is required for graphical privilege elevation but is not installed."

    # Try apt install first for clean dependency handling
    cmd_apt = ["pkexec", "apt-get", "install", "--reinstall", "-y", deb_abs]
    try:
        proc = subprocess.run(cmd_apt, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            _sync_local_checkout_if_needed()
            restart_service()
            return True, "Successfully installed via apt."
    except subprocess.TimeoutExpired:
        return False, "Installation timed out after 120 seconds."
    except Exception:
        pass

    # Fallback to dpkg -i
    cmd_dpkg = ["pkexec", "dpkg", "-i", deb_abs]
    try:
        proc = subprocess.run(cmd_dpkg, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            _sync_local_checkout_if_needed()
            restart_service()
            return True, "Successfully installed via dpkg."
        return False, f"Package installation failed: {proc.stderr or proc.stdout}"
    except Exception as e:
        return False, f"Execution failed: {e}"


def install_windows_update(exe_path: Path) -> Tuple[bool, str]:
    """
    Spawns a helper process to replace the running Blink.exe
    and restart the application.
    """
    if not exe_path.is_file():
        return False, "Update executable not found."

    current_exe = sys.executable
    helper_script = Path(__file__).resolve().parent.parent / "scripts" / "win_update_helper.py"

    # Launch helper to perform replacement
    subprocess.Popen([sys.executable, str(helper_script), str(exe_path), current_exe])

    # Close the current app
    os._exit(0)
    return True, "Update initiated."

"""
Blink CLI and Application Entrypoint for Linux.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
import signal
from pathlib import Path

# Ensure project root is always in sys.path regardless of execution context
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, GLib

GLib.set_prgname("blink")
GLib.set_application_name("Blink")

from blink.core.config import BlinkSettings, CONFIG_DIR
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.core.coordinator import FaceUnlockCoordinator
from blink.ui.notch_overlay import NotchOverlayWindow
from blink.ui.settings_window import SettingsWindow
from blink.system.keystroke_injector import KeystrokeInjector


def run_daemon():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(CONFIG_DIR / "blink.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, BlockingIOError):
        print("[Blink] Another Blink daemon instance is already active. Exiting.")
        sys.exit(0)

    print("[Blink] Starting Blink daemon for Linux...")
    overlay = NotchOverlayWindow()
    coordinator = FaceUnlockCoordinator(overlay=overlay)
    coordinator.start()

    def handle_sigint(sig, frame):
        print("\n[Blink] Shutting down daemon...")
        coordinator.stop()
        Gtk.main_quit()

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    Gtk.main()


def run_settings():
    install_desktop_entry()
    win = SettingsWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


def run_status():
    settings = BlinkSettings.load()
    print("=" * 50)
    print("              BLINK FOR LINUX")
    print("=" * 50)
    print(f"Face Unlock Enabled:    {settings.is_face_unlock_enabled}")
    print(f"Similarity Threshold:   {settings.match_threshold}")
    print(f"Detection Timeout:      {settings.face_detection_seconds}s")
    print(f"Liveness Strictness:    {settings.liveness_mode.upper()} (Enabled: {settings.liveness_checks_enabled})")
    print(f"Active Triggers:        {', '.join(settings.unlock_triggers)}")
    print(f"Camera Device Index:    /dev/video{settings.camera_index}")
    print(f"Keystroke Injection:    {'Available (/dev/uinput writable)' if KeystrokeInjector.is_available() else 'NOT Available'}")
    print(f"Password Stored:        {'YES (AES-256-GCM)' if SecureCredentialManager.has_stored_password() else 'NO'}")

    identities = SecureFaceStore.load_identities()
    print(f"Enrolled Identities:    {len(identities)}")
    for ident in identities:
        status = "Active" if ident.get("is_enabled", True) else "Disabled"
        print(f"  - {ident['name']} ({len(ident.get('samples', []))} poses, {status})")
    print("=" * 50)


def install_desktop_entry():
    """Installs Blink into the system application menu and user desktop."""
    app_dir = Path.home() / ".local" / "share" / "applications"
    app_dir.mkdir(parents=True, exist_ok=True)
    bin_path = Path.home() / "blink" / "bin" / "blink"
    icon_path = Path.home() / "blink" / "blink" / "resources" / "appicon.png"
    svg_icon = Path.home() / "blink" / "blink" / "resources" / "blink-logo.svg"

    # Install icons into hicolor at all standard resolutions
    hicolor_base = Path.home() / ".local" / "share" / "icons" / "hicolor"
    import shutil
    try:
        import cv2
        if icon_path.exists():
            orig_img = cv2.imread(str(icon_path), cv2.IMREAD_UNCHANGED)
            for sz in [16, 24, 32, 48, 64, 128, 256, 512]:
                sz_dir = hicolor_base / f"{sz}x{sz}" / "apps"
                sz_dir.mkdir(parents=True, exist_ok=True)
                resized = cv2.resize(orig_img, (sz, sz), interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(sz_dir / "blink.png"), resized)
        if svg_icon.exists():
            svg_dir = hicolor_base / "scalable" / "apps"
            svg_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(svg_icon, svg_dir / "blink.svg")
    except Exception:
        pass

    app_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Blink
GenericName=Face ID
Comment=Native Face ID Biometric Authentication for Linux
Exec={bin_path} settings
Icon={icon_path}
Terminal=false
StartupNotify=true
StartupWMClass=blink
Categories=Utility;Security;Settings;System;GTK;
Keywords=face;id;unlock;biometric;security;blink;camera;
Actions=DaemonRestart;LockscreenTest;

[Desktop Action DaemonRestart]
Name=Restart Face ID Daemon
Exec=systemctl --user restart blink.service

[Desktop Action LockscreenTest]
Name=Test Dynamic Island
Exec={bin_path} test-scan
"""
    app_desktop_file = app_dir / "blink.desktop"
    app_desktop_file.write_text(app_entry, encoding="utf-8")
    app_desktop_file.chmod(0o755)

    # Desktop shortcut
    desktop_dir = Path.home() / "Desktop"
    if desktop_dir.is_dir():
        desktop_shortcut = desktop_dir / "blink.desktop"
        desktop_shortcut.write_text(app_entry, encoding="utf-8")
        desktop_shortcut.chmod(0o755)
        try:
            import subprocess
            subprocess.run(["gio", "set", str(desktop_shortcut), "metadata::trusted", "true"], check=False)
        except Exception:
            pass

    try:
        import subprocess
        subprocess.run(["update-desktop-database", str(app_dir)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(Path.home() / ".local" / "share" / "icons" / "hicolor")], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    print(f"[Blink] System application menu entry installed at: {app_desktop_file}")


def setup_autostart():
    install_desktop_entry()
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = autostart_dir / "blink.desktop"
    bin_path = Path.home() / "blink" / "bin" / "blink"
    icon_path = Path.home() / "blink" / "blink" / "resources" / "appicon.png"

    content = f"""[Desktop Entry]
Type=Application
Name=Blink Face Unlock
Comment=Face ID unlock daemon for Linux
Exec={bin_path} daemon
Icon={icon_path}
StartupNotify=false
Terminal=false
Categories=Utility;Security;
X-GNOME-Autostart-enabled=true
"""
    desktop_file.write_text(content, encoding="utf-8")
    print(f"[Blink] Autostart desktop entry created at: {desktop_file}")


def run_test_scan():
    print("[Blink] Previewing Face ID squarish card animation on desktop...")
    overlay = NotchOverlayWindow()
    overlay._is_desktop_preview = True
    coordinator = FaceUnlockCoordinator(overlay=overlay)
    coordinator.test_scan()
    # Quit GTK main loop 6 seconds after scan test concludes
    GLib.timeout_add_seconds(6, Gtk.main_quit)
    Gtk.main()


def main():
    parser = argparse.ArgumentParser(description="Blink: Native Face ID for Linux")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("daemon", help="Run Blink face unlock background daemon")
    subparsers.add_parser("settings", help="Open Blink Settings & Face Lab GUI")
    subparsers.add_parser("status", help="Show current Blink status and configuration")
    subparsers.add_parser("autostart", help="Configure Blink to launch on user login")
    subparsers.add_parser("install-desktop", help="Install Blink into system application menu and desktop")
    subparsers.add_parser("test-scan", help="Test Face ID Dynamic Island scan & unlock animation on desktop")

    args = parser.parse_args()

    if args.command == "settings":
        run_settings()
    elif args.command == "status":
        run_status()
    elif args.command == "autostart":
        setup_autostart()
    elif args.command == "install-desktop":
        install_desktop_entry()
    elif args.command == "test-scan":
        run_test_scan()
    else:
        # Default action: if no args or daemon, run daemon
        if args.command is None:
            run_settings()
        else:
            run_daemon()


if __name__ == "__main__":
    main()

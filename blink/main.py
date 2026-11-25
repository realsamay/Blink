"""
Blink CLI and Application Entrypoint for Linux.
"""

from __future__ import annotations

import argparse
import os
import sys
import signal
import subprocess
from pathlib import Path

# Ensure project root is always in sys.path regardless of execution context
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from blink.system.platform_info import IS_WINDOWS

if not IS_WINDOWS:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, GLib

    GLib.set_prgname("blink")
    GLib.set_application_name("Blink")
else:
    Gtk = None
    GLib = None

from blink.core.config import BlinkSettings, CONFIG_DIR
from blink.core.crypto import SecureCredentialManager, SecureFaceStore


def acquire_single_instance_lock():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, True, "Global\\BlinkFaceUnlockDaemonMutex")
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                print("[Blink] Another Blink daemon instance is already active. Exiting.")
                sys.exit(0)
            return mutex
        except Exception:
            return None
    else:
        import fcntl
        lock_file = open(CONFIG_DIR / "blink.lock", "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, BlockingIOError):
            print("[Blink] Another Blink daemon instance is already active. Exiting.")
            sys.exit(0)
        return lock_file


def run_daemon():
    from blink.core.coordinator import FaceUnlockCoordinator
    from blink.ui.notch_overlay import NotchOverlayWindow
    lock = acquire_single_instance_lock()
    os_name = "Windows" if IS_WINDOWS else "Linux"
    print(f"[Blink] Starting Blink daemon for {os_name}...")
    overlay = NotchOverlayWindow()
    coordinator = FaceUnlockCoordinator(overlay=overlay)
    coordinator.start()

    if IS_WINDOWS:
        import time

        def handle_sigint(sig, frame):
            print("\n[Blink] Shutting down daemon...")
            coordinator.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_sigint)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            coordinator.stop()
    else:
        def handle_sigint(sig, frame):
            print("\n[Blink] Shutting down daemon...")
            coordinator.stop()
            Gtk.main_quit()

        signal.signal(signal.SIGINT, handle_sigint)
        signal.signal(signal.SIGTERM, handle_sigint)
        Gtk.main()


def run_settings():
    # Attempt to load native UI first
    if IS_WINDOWS:
        try:
            from blink.ui.settings_window_win import SettingsWindowWin
            win = SettingsWindowWin()
            win.mainloop()
            return
        except ImportError:
            print("[Blink] Native Windows UI not found, falling back to Web UI.")
        except Exception as e:
            print(f"[Blink] Native Windows UI failed to load: {e}")

    # Fallback to Linux GTK UI or Web UI
    if Gtk is None:
        try:
            from blink.ui.web_server import run_web_server
            run_web_server(port=5050, open_browser=True)
            return
        except Exception as e:
            import traceback
            err_msg = f"Failed to start Blink settings UI:\n{e}\n\n{traceback.format_exc()}"
            print(f"[Blink] Error: {err_msg}")

            # If running in windowed GUI mode without a console, show native Windows alert
            has_tty = sys.stdin is not None and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
            if not has_tty and IS_WINDOWS:
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0, f"Blink could not start settings:\n\n{e}", "Blink Error", 0x10)
                except Exception:
                    pass
            return

    from blink.ui.settings_window import SettingsWindow
    install_desktop_entry()
    win = SettingsWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


def run_terminal_settings():
    """Interactive terminal settings wizard for headless/Windows setups."""
    has_tty = sys.stdin is not None and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if not has_tty:
        print("[Blink] No interactive terminal available (sys.stdin is None). Cannot prompt.")
        return

    settings = BlinkSettings.load()
    print("=" * 50)
    print("          BLINK CONFIGURATION WIZARD")
    print("=" * 50)
    print(f"1. Face Unlock Enabled: {settings.is_face_unlock_enabled}")
    print(f"2. Match Threshold:     {settings.match_threshold}")
    print(f"3. Detection Timeout:   {settings.face_detection_seconds}s")
    print(f"4. Password Stored:     {'YES' if SecureCredentialManager.has_stored_password() else 'NO'}")
    print("=" * 50)
    pwd = input("Enter new desktop unlock password (or press Enter to keep): ").strip()
    if pwd:
        SecureCredentialManager.save_password(pwd)
        print("[Blink] Password saved with AES-256-GCM encryption.")
    enable = input("Enable Face Unlock? (y/n, Enter to skip): ").strip().lower()
    if enable == 'y':
        settings.is_face_unlock_enabled = True
        settings.save()
        print("[Blink] Face Unlock enabled.")
    elif enable == 'n':
        settings.is_face_unlock_enabled = False
        settings.save()
        print("[Blink] Face Unlock disabled.")


def run_status():
    from blink.system.keystroke_injector import KeystrokeInjector
    settings = BlinkSettings.load()
    os_name = "WINDOWS" if IS_WINDOWS else "LINUX"
    print("=" * 50)
    print(f"              BLINK FOR {os_name}")
    print("=" * 50)
    print(f"Face Unlock Enabled:    {settings.is_face_unlock_enabled}")
    print(f"Similarity Threshold:   {settings.match_threshold}")
    print(f"Detection Timeout:      {settings.face_detection_seconds}s")
    print(f"Liveness Strictness:    {settings.liveness_mode.upper()} (Enabled: {settings.liveness_checks_enabled})")
    print(f"Active Triggers:        {', '.join(settings.unlock_triggers)}")
    cam_str = f"Device Index {settings.camera_index} (DirectShow)" if IS_WINDOWS else f"/dev/video{settings.camera_index}"
    print(f"Camera Device Index:    {cam_str}")
    inj_str = "Available (Win32 SendInput API)" if IS_WINDOWS else ("Available (/dev/uinput writable)" if KeystrokeInjector.is_available() else "NOT Available")
    print(f"Keystroke Injection:    {inj_str}")
    print(f"Password Stored:        {'YES (AES-256-GCM)' if SecureCredentialManager.has_stored_password() else 'NO'}")

    identities = SecureFaceStore.load_identities()
    print(f"Enrolled Identities:    {len(identities)}")
    for ident in identities:
        status = "Active" if ident.get("is_enabled", True) else "Disabled"
        print(f"  - {ident['name']} ({len(ident.get('samples', []))} poses, {status})")
    print("=" * 50)


def notify(title: str, message: str) -> None:
    if not IS_WINDOWS:
        try:
            subprocess.run(["notify-send", "-i", "blink", title, message], check=False)
        except Exception:
            pass


def run_lock_command(target_path: str) -> None:
    from blink.core.lock_manager import BlinkLockManager
    mgr = BlinkLockManager.get_instance()
    try:
        # Smart toggle: if item is already locked, invoke shield unlock instead of failing
        if mgr.is_path_locked(target_path):
            print(f"[Blink Lock] '{Path(target_path).name}' is already locked — switching to Face ID unlock.")
            run_shield_command(target_path=target_path)
            return
        res = mgr.lock_path(target_path)
        msg = f"Locked: {res['name']} -> {Path(res['locked_path']).name}"
        print(f"[Blink Lock] {msg}")
        notify("Blink Lock", f"Successfully locked and encrypted {res['name']}")
    except Exception as e:
        print(f"[Blink Lock] Error locking '{target_path}': {e}")
        notify("Blink Lock Error", str(e))


def _unminimize_window(win_id: Optional[str]) -> None:
    if not win_id:
        return
    if IS_WINDOWS:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(win_id)
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    else:
        from blink.core.lock_manager import unminimize_x11_window
        unminimize_x11_window(win_id)


def run_shield_command(
    target_path: Optional[str] = None,
    app_key: Optional[str] = None,
    win_id: Optional[str] = None,
) -> None:
    from blink.core.lock_manager import BlinkLockManager
    from blink.core.app_discovery import get_app_info
    from blink.core.coordinator import FaceUnlockCoordinator
    from blink.ui.notch_overlay import NotchOverlayWindow

    mgr = BlinkLockManager.get_instance()

    if app_key:
        clean_key = app_key.strip().lower()
        if mgr.is_app_session_unlocked(clean_key):
            print(f"[Blink Shield] App '{clean_key}' is already authorized within grace window.")
            _unminimize_window(win_id)
            sys.exit(0)

        info = get_app_info(clean_key)
        app_name = info["name"]
        app_icon = info.get("icon")

        print(f"[Blink Shield] Presenting Face ID verification for '{app_name}'...")
        if not IS_WINDOWS:
            overlay = NotchOverlayWindow()
            overlay._is_desktop_preview = True
            coordinator = FaceUnlockCoordinator(overlay=overlay)
            authenticated = coordinator.authenticate_with_fallback(
                target_name=app_name,
                target_icon=app_icon,
                max_attempts=2
            )
        else:
            from blink.ui.shield_window import BlinkShieldWindow
            authenticated = BlinkShieldWindow.launch(
                target_type="app",
                target_name=app_name,
                target_key=clean_key,
                target_icon=app_icon,
            )

        if authenticated:
            print(f"[Blink Shield] Access granted for '{app_name}'.")
            mgr.grant_app_session_unlock(clean_key)
            if not IS_WINDOWS and "overlay" in locals() and overlay:
                overlay.clean_close()
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            _unminimize_window(win_id)
            sys.exit(0)
        else:
            print(f"[Blink Shield] Access denied or cancelled for '{app_name}'.")
            sys.exit(1)

    elif target_path:
        p = Path(target_path).resolve()
        if not p.exists():
            print(f"[Blink Shield] Target not found: {target_path}")
            sys.exit(1)

        meta = mgr.get_container_metadata(p)
        if meta:
            item_name = meta.get("name", p.name)
            item_type = meta.get("type", "folder" if p.is_dir() else "file")
        else:
            item_name = p.name[:-10] if p.name.endswith(".blinklock") else p.name
            item_type = "folder" if p.is_dir() else "file"

        print(f"[Blink Shield] Presenting Face ID verification for {item_type} '{item_name}'...")
        if not IS_WINDOWS:
            overlay = NotchOverlayWindow()
            overlay._is_desktop_preview = True
            coordinator = FaceUnlockCoordinator(overlay=overlay)
            authenticated = coordinator.authenticate_with_fallback(
                target_name=item_name,
                max_attempts=2
            )
        else:
            from blink.ui.shield_window import BlinkShieldWindow
            authenticated = BlinkShieldWindow.launch(
                target_type=item_type,
                target_name=item_name,
                target_key=str(p),
            )

        if authenticated:
            try:
                restored = mgr.unlock_path(p)
                print(f"[Blink Shield] Decrypted & restored {item_type}: {restored}")
                if not IS_WINDOWS:
                    if "overlay" in locals() and overlay:
                        overlay.clean_close()
                        while Gtk.events_pending():
                            Gtk.main_iteration_do(False)
                    subprocess.Popen(
                        ["gio", "open", str(restored)],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.run(["explorer.exe", f"/select,{restored}"], check=False)
                notify("Blink Lock", f"Unlocked {item_name}")
                sys.exit(0)
            except Exception as e:
                print(f"[Blink Shield] Error unlocking {p.name}: {e}")
                notify("Blink Lock Error", str(e))
                sys.exit(1)
        else:
            print(f"[Blink Shield] Access denied or cancelled for {item_type} '{item_name}'.")
            sys.exit(1)
    else:
        print("[Blink Shield] Missing --app or --path parameter.")
        sys.exit(1)


def run_lock_desktop_app(desktop_file_path: str) -> None:
    from blink.core.lock_manager import BlinkLockManager
    import configparser

    p = Path(desktop_file_path).resolve()
    if not p.exists():
        print(f"[Blink Lock] Desktop file not found: {p}")
        return

    try:
        cp = configparser.RawConfigParser()
        cp.read(p, encoding="utf-8")
        entry = cp["Desktop Entry"]
        name = entry.get("Name", p.stem).strip()
        exec_cmd = entry.get("Exec", "").strip()
        wm_class = entry.get("StartupWMClass", "").strip()
        base_bin = exec_cmd.split()[0].replace('"', '').replace("'", "")
        base_name = Path(base_bin).name.lower()
        key = (wm_class or base_name).strip().lower()

        mgr = BlinkLockManager.get_instance()
        ok = mgr.add_locked_app(name=name, key=key, exe=base_name)
        if ok:
            print(f"[Blink Lock] Successfully locked application '{name}' ({key}).")
            notify("Blink Lock", f"Protected '{name}' with Face ID")
        else:
            print(f"[Blink Lock] Application '{name}' is already protected.")
            notify("Blink Lock", f"'{name}' is already protected")
    except Exception as e:
        print(f"[Blink Lock] Failed to parse desktop file: {e}")
        notify("Blink Lock Error", f"Failed to lock app: {e}")


def run_unlock_command(target_path: str) -> None:
    run_shield_command(target_path=target_path)


def run_list_locked() -> None:
    from blink.core.lock_manager import BlinkLockManager
    mgr = BlinkLockManager.get_instance()
    print("=" * 55)
    print("               BLINK LOCK REGISTRY")
    print("=" * 55)
    print(f"Protection Active: {mgr.is_enabled()}")
    print(f"Grace Period:      {mgr.get_grace_period()}s")
    print("-" * 55)
    print("[Protected Applications]")
    apps = mgr.get_locked_apps()
    if apps:
        for a in apps:
            print(f"  🔒 {a['name']} ({a['key']})")
    else:
        print("  (None enrolled yet. Use 'blink lock-app <name>')")

    print("-" * 55)
    print("[Encrypted Files & Folders (Vault)]")
    items = mgr.get_locked_items()
    if items:
        for it in items:
            sz = f"{it.get('size_bytes', 0) / 1024:.1f} KB"
            print(f"  📦 {it['name']} [{it['type'].upper()}] - {sz}")
            print(f"     Path: {it['locked_path']}")
    else:
        print("  (Vault empty. Right click any folder or use 'blink lock <path>')")
    print("=" * 55)


def install_desktop_entry():
    """Installs Blink into the system application menu and user desktop."""
    if IS_WINDOWS:
        try:
            appdata = os.environ.get("APPDATA", "")
            start_menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            start_menu.mkdir(parents=True, exist_ok=True)
            desktop = Path.home() / "Desktop"

            # Create Windows batch launcher
            bat_content = f'@echo off\r\nstart "" pythonw "{ROOT_DIR}\\blink\\main.py" settings\r\n'
            (start_menu / "Blink.bat").write_text(bat_content, encoding="utf-8")
            if desktop.is_dir():
                (desktop / "Blink.bat").write_text(bat_content, encoding="utf-8")
            print(f"[Blink] Windows shortcuts installed at: {start_menu / 'Blink.bat'}")
        except Exception as e:
            print(f"[Blink] Notice: Failed to create Windows shortcut: {e}")
        return

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

    try:
        from blink.core.lock_manager import BlinkLockManager
        BlinkLockManager.get_instance().install_context_menus()
    except Exception as e:
        print(f"[Blink] Notice: Failed to install context menus: {e}")

    print(f"[Blink] System application menu entry installed at: {app_desktop_file}")


def setup_autostart():
    install_desktop_entry()
    if IS_WINDOWS:
        try:
            appdata = os.environ.get("APPDATA", "")
            startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            startup_dir.mkdir(parents=True, exist_ok=True)
            bat_file = startup_dir / "BlinkDaemon.bat"
            bat_content = f'@echo off\r\nstart "" pythonw "{ROOT_DIR}\\blink\\main.py" daemon\r\n'
            bat_file.write_text(bat_content, encoding="utf-8")
            print(f"[Blink] Windows startup launcher created at: {bat_file}")
        except Exception as e:
            print(f"[Blink] Notice: Failed to configure Windows startup: {e}")
        return

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
    from blink.core.coordinator import FaceUnlockCoordinator
    from blink.ui.notch_overlay import NotchOverlayWindow
    print("[Blink] Previewing Face ID squarish card animation on desktop...")
    overlay = NotchOverlayWindow()
    overlay._is_desktop_preview = True
    coordinator = FaceUnlockCoordinator(overlay=overlay)
    coordinator.test_scan()
    if IS_WINDOWS:
        import time
        time.sleep(6.0)
        overlay.clean_close()
    else:
        # Quit GTK main loop 6 seconds after scan test concludes
        GLib.timeout_add_seconds(6, Gtk.main_quit)
        Gtk.main()


def run_web_preview(port: int = 5050):
    """Launches the 1:1 Apple-Grade Web Interface & Localhost Preview."""
    try:
        from blink.ui.web_server import run_web_server
        run_web_server(port=port, open_browser=True)
    except Exception as e:
        print(f"Failed to launch web preview: {e}")


def run_check_update():
    from blink.core import updater
    print(f"[Blink] Current version: v{updater.CURRENT_VERSION}")
    print("[Blink] Checking GitHub for updates...")
    res = updater.check_for_updates()
    if res.get("status") == "error":
        print(f"[Blink] Error: {res.get('error')}")
        return
    if res.get("has_update"):
        latest = res.get("latest_version")
        print(f"[Blink] New version available: v{latest} ({res.get('release_name')})")
        print(f"[Blink] Release notes:\n{res.get('changelog')}")
        print("\nRun 'blink update' or use the About tab in Settings to install.")
    else:
        print(f"[Blink] ✓ Blink is up to date (v{updater.CURRENT_VERSION}).")


def run_update():
    from blink.core import updater
    print(f"[Blink] Current version: v{updater.CURRENT_VERSION}")
    print("[Blink] Checking GitHub for updates...")
    res = updater.check_for_updates()
    if res.get("status") == "error":
        print(f"[Blink] Error: {res.get('error')}")
        return
    if not res.get("has_update"):
        print(f"[Blink] ✓ Blink is already up to date (v{updater.CURRENT_VERSION}).")
        return

    latest = res.get("latest_version")
    deb_url = res.get("deb_url")
    if not deb_url:
        print("[Blink] No Debian package (.deb) found in the latest release.")
        return

    print(f"[Blink] Downloading Blink v{latest}...")
    dest = updater.DOWNLOAD_CACHE_DIR / res.get("deb_filename", f"blink_{latest}_amd64.deb")

    def _prog(fraction, dl, total):
        pct = int(fraction * 100)
        mb_dl = dl / (1024 * 1024)
        mb_tot = total / (1024 * 1024) if total > 0 else 0
        sys.stdout.write(f"\rDownloading: {pct}% ({mb_dl:.1f}MB / {mb_tot:.1f}MB)")
        sys.stdout.flush()

    ok, msg = updater.download_package(deb_url, dest, progress_callback=_prog)
    print()
    if not ok:
        print(f"[Blink] Download failed: {msg}")
        return

    print(f"[Blink] Installing package via pkexec: {dest}...")
    ok_inst, msg_inst = updater.install_deb_package(dest)
    if ok_inst:
        print(f"[Blink] ✓ Successfully updated to v{latest}! Background services restarted.")
    else:
        print(f"[Blink] Update installation failed: {msg_inst}")


def main():
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("realsamay.blink.faceid.2.1")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Blink: Native Face ID")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("daemon", help="Run Blink face unlock background daemon")
    subparsers.add_parser("settings", help="Open Blink Settings & Face Lab GUI")
    subparsers.add_parser("preview", help="Preview Blink 1:1 Apple Web UI on localhost:5050")
    subparsers.add_parser("web", help="Preview Blink 1:1 Apple Web UI on localhost:5050")
    subparsers.add_parser("status", help="Show current Blink status and configuration")
    subparsers.add_parser("autostart", help="Configure Blink to launch on user login")
    subparsers.add_parser("install-desktop", help="Install Blink into system application menu and desktop")
    subparsers.add_parser("test-scan", help="Test Face ID Dynamic Island scan & unlock animation on desktop")

    # Blink Lock (App, Folder & File Protection)
    p_lock = subparsers.add_parser("lock", help="Lock & encrypt a file or folder with Blink Face ID")
    p_lock.add_argument("path", help="Path to file or folder to lock")

    p_unlock = subparsers.add_parser("unlock", help="Authenticate with Face ID to decrypt & unlock an item")
    p_unlock.add_argument("path", help="Path to locked item to unlock")

    p_shield = subparsers.add_parser("shield", help="Present iOS-style Face ID shield authentication dialog")
    p_shield.add_argument("--path", help="Path to locked file or folder to unlock", default=None)
    p_shield.add_argument("--app", help="Application key to authorize", default=None)
    p_shield.add_argument("--win-id", help="Window ID to restore upon authorization", default=None)

    p_lock_desktop = subparsers.add_parser("lock-desktop-app", help="Lock application from a .desktop file")
    p_lock_desktop.add_argument("desktop_file", help="Path to .desktop file")

    p_lock_app = subparsers.add_parser("lock-app", help="Add an application to Blink App Lock")
    p_lock_app.add_argument("app", help="Application name or process identifier (e.g. 'chrome', 'telegram')")

    p_unlock_app = subparsers.add_parser("unlock-app", help="Remove an application from Blink App Lock")
    p_unlock_app.add_argument("app", help="Application name or process identifier to remove")

    subparsers.add_parser("check-update", help="Check for available Blink updates")
    subparsers.add_parser("update", help="Download and install latest Blink update")
    subparsers.add_parser("list-locked", help="List all protected apps, files, and folders")
    subparsers.add_parser("install-context-menu", help="Install right-click context menu integration")

    args = parser.parse_args()

    if args.command in ("preview", "web"):
        run_web_preview()
    elif args.command == "settings":
        run_settings()
    elif args.command == "status":
        run_status()
    elif args.command == "autostart":
        setup_autostart()
    elif args.command == "install-desktop":
        install_desktop_entry()
    elif args.command == "test-scan":
        run_test_scan()
    elif args.command == "lock":
        run_lock_command(args.path)
    elif args.command == "unlock":
        run_unlock_command(args.path)
    elif args.command == "shield":
        run_shield_command(target_path=args.path, app_key=args.app, win_id=args.win_id)
    elif args.command == "lock-desktop-app":
        run_lock_desktop_app(args.desktop_file)
    elif args.command == "lock-app":
        from blink.core.lock_manager import BlinkLockManager
        mgr = BlinkLockManager.get_instance()
        ok = mgr.add_locked_app(args.app.capitalize(), args.app.lower())
        if ok:
            print(f"[Blink Lock] Added application '{args.app}' to App Lock protection.")
        else:
            print(f"[Blink Lock] Application '{args.app}' is already locked.")
    elif args.command == "unlock-app":
        from blink.core.lock_manager import BlinkLockManager
        mgr = BlinkLockManager.get_instance()
        ok = mgr.remove_locked_app(args.app.lower())
        if ok:
            print(f"[Blink Lock] Removed application '{args.app}' from App Lock protection.")
        else:
            print(f"[Blink Lock] Application '{args.app}' was not in the locked list.")
    elif args.command == "check-update":
        run_check_update()
    elif args.command == "update":
        run_update()
    elif args.command == "list-locked":
        run_list_locked()
    elif args.command == "install-context-menu":
        from blink.core.lock_manager import BlinkLockManager
        BlinkLockManager.get_instance().install_context_menus()
    else:
        # Default action: if no args or daemon, run daemon
        if args.command is None:
            run_settings()
        else:
            run_daemon()


if __name__ == "__main__":
    main()

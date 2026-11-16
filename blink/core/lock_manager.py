"""
Blink Lock & Vault Manager.
Provides biometric application locking, file & folder encryption,
and native desktop file manager context menu integration for Linux & Windows.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from blink.core.config import CONFIG_DIR, BlinkSettings
from blink.core.crypto import SecureCredentialManager
from blink.system.platform_info import IS_WINDOWS

MAGIC_HEADER = b"BLINKLOCK\x02"
ASSOCIATED_DATA = b"BLINKLOCK_V2"
LOCK_STORE_FILE = CONFIG_DIR / "locked_items.json"


class BlinkLockManager:
    """Manages locked applications, encrypted files/folders, and session tokens."""

    _instance: Optional["BlinkLockManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._unlocked_app_sessions: Dict[str, float] = {}  # {app_key: timestamp}
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_store()

    @classmethod
    def get_instance(cls) -> "BlinkLockManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = BlinkLockManager()
            return cls._instance

    def _ensure_store(self) -> None:
        if not LOCK_STORE_FILE.exists():
            initial_data = {
                "enabled": True,
                "grace_period_seconds": 60,
                "locked_apps": [],
                "locked_items": [],
            }
            self._save_raw(initial_data)

    def _load_raw(self) -> Dict[str, Any]:
        try:
            if LOCK_STORE_FILE.exists():
                with open(LOCK_STORE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[BlinkLock] Error loading {LOCK_STORE_FILE}: {e}")
        return {
            "enabled": True,
            "grace_period_seconds": 60,
            "locked_apps": [],
            "locked_items": [],
        }

    def _save_raw(self, data: Dict[str, Any]) -> None:
        try:
            with tempfile.NamedTemporaryFile("w", dir=CONFIG_DIR, delete=False, encoding="utf-8") as tf:
                json.dump(data, tf, indent=2)
                temp_path = tf.name
            os.replace(temp_path, LOCK_STORE_FILE)
        except Exception as e:
            print(f"[BlinkLock] Error saving {LOCK_STORE_FILE}: {e}")

    # --------------------------------------------------------------------------
    # Settings & State
    # --------------------------------------------------------------------------
    def is_enabled(self) -> bool:
        data = self._load_raw()
        return bool(data.get("enabled", True))

    def set_enabled(self, enabled: bool) -> None:
        data = self._load_raw()
        data["enabled"] = bool(enabled)
        self._save_raw(data)

    def get_grace_period(self) -> int:
        data = self._load_raw()
        return int(data.get("grace_period_seconds", 60))

    def set_grace_period(self, seconds: int) -> None:
        data = self._load_raw()
        data["grace_period_seconds"] = max(0, int(seconds))
        self._save_raw(data)

    # --------------------------------------------------------------------------
    # Application Lock Management
    # --------------------------------------------------------------------------
    def get_locked_apps(self) -> List[Dict[str, Any]]:
        data = self._load_raw()
        return list(data.get("locked_apps", []))

    def is_app_locked(self, app_key: str) -> bool:
        if not self.is_enabled():
            return False
        clean_key = app_key.strip().lower()
        for app in self.get_locked_apps():
            ak = app.get("key", "").strip().lower()
            aexe = app.get("exe", "").strip().lower()
            if ak == clean_key or aexe == clean_key or clean_key in ak or (aexe and aexe in clean_key):
                return True
        return False

    def add_locked_app(self, name: str, key: str, exe: Optional[str] = None) -> bool:
        data = self._load_raw()
        locked = data.get("locked_apps", [])
        clean_key = key.strip().lower()
        if any(a.get("key", "").lower() == clean_key for a in locked):
            return False

        locked.append({
            "id": str(uuid.uuid4()),
            "name": name.strip(),
            "key": clean_key,
            "exe": (exe or key).strip().lower(),
            "locked_at": time.time(),
        })
        data["locked_apps"] = locked
        self._save_raw(data)
        return True

    def remove_locked_app(self, key: str) -> bool:
        data = self._load_raw()
        locked = data.get("locked_apps", [])
        clean_key = key.strip().lower()
        initial_len = len(locked)
        filtered = [a for a in locked if a.get("key", "").lower() != clean_key and a.get("id") != key]
        if len(filtered) != initial_len:
            data["locked_apps"] = filtered
            self._save_raw(data)
            self._unlocked_app_sessions.pop(clean_key, None)
            return True
        return False

    def is_app_session_unlocked(self, app_key: str) -> bool:
        clean_key = app_key.strip().lower()
        unlocked_at = self._unlocked_app_sessions.get(clean_key)
        if unlocked_at is None:
            return False
        grace = self.get_grace_period()
        if grace <= 0:
            return False  # Always require re-verification
        return (time.time() - unlocked_at) < grace

    def grant_app_session_unlock(self, app_key: str) -> None:
        clean_key = app_key.strip().lower()
        self._unlocked_app_sessions[clean_key] = time.time()

    def revoke_all_app_sessions(self) -> None:
        self._unlocked_app_sessions.clear()

    # --------------------------------------------------------------------------
    # File & Folder Encryption (Vault)
    # --------------------------------------------------------------------------
    def get_locked_items(self) -> List[Dict[str, Any]]:
        data = self._load_raw()
        return list(data.get("locked_items", []))

    def lock_path(self, target_path: str | Path) -> Dict[str, Any]:
        """Encrypts a file or directory into a .blinklock AES-256-GCM package.
        Deletes the plain source upon successful encryption.
        """
        p = Path(target_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: {p}")

        if p.name.endswith(".blinklock"):
            raise ValueError(f"Target is already a locked item: {p.name}")

        is_folder = p.is_dir()
        dest_path = p.with_name(f"{p.name}.blinklock")
        if dest_path.exists():
            raise FileExistsError(f"A locked container already exists at {dest_path}")

        key = SecureCredentialManager.get_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)

        meta: Dict[str, Any] = {
            "version": 2,
            "type": "folder" if is_folder else "file",
            "name": p.name,
            "original_path": str(p),
            "created_at": time.time(),
        }

        if is_folder:
            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
                tar.add(str(p), arcname=p.name)
            raw_payload = tar_buf.getvalue()
            total_size = len(raw_payload)
        else:
            raw_payload = p.read_bytes()
            total_size = len(raw_payload)

        meta["size_bytes"] = total_size
        meta_bytes = json.dumps(meta).encode("utf-8")
        header_len = len(meta_bytes).to_bytes(4, byteorder="big")

        full_blob = header_len + meta_bytes + raw_payload
        ciphertext = aesgcm.encrypt(nonce, full_blob, ASSOCIATED_DATA)

        # Atomic write of the encrypted .blinklock package
        temp_dest = dest_path.with_name(f".{dest_path.name}.tmp_{os.getpid()}")
        try:
            with open(temp_dest, "wb") as f:
                f.write(MAGIC_HEADER)
                f.write(nonce)
                f.write(ciphertext)
            os.replace(temp_dest, dest_path)
        except Exception:
            if temp_dest.exists():
                temp_dest.unlink()
            raise

        # Cleanly remove the unencrypted original
        if is_folder:
            shutil.rmtree(p)
        else:
            p.unlink()

        # Update registry
        item_record = {
            "id": str(uuid.uuid4()),
            "type": "folder" if is_folder else "file",
            "name": p.name,
            "original_path": str(p),
            "locked_path": str(dest_path),
            "size_bytes": total_size,
            "locked_at": time.time(),
        }
        data = self._load_raw()
        locked = data.get("locked_items", [])
        locked.append(item_record)
        data["locked_items"] = locked
        self._save_raw(data)

        print(f"[BlinkLock] Successfully locked {p.name} -> {dest_path.name}")
        return item_record

    def unlock_path(self, locked_path: str | Path, restore_dir: Optional[str | Path] = None) -> str:
        """Decrypts a .blinklock package and restores the original file or directory structure."""
        lp = Path(locked_path).resolve()
        if not lp.exists():
            raise FileNotFoundError(f"Locked file not found: {lp}")

        if not lp.name.endswith(".blinklock"):
            raise ValueError(f"File is not a .blinklock container: {lp.name}")

        with open(lp, "rb") as f:
            header = f.read(len(MAGIC_HEADER))
            if header != MAGIC_HEADER:
                raise ValueError(f"Invalid Blink Lock container header: {lp.name}")
            nonce = f.read(12)
            ciphertext = f.read()

        key = SecureCredentialManager.get_key()
        aesgcm = AESGCM(key)
        try:
            decrypted = aesgcm.decrypt(nonce, ciphertext, ASSOCIATED_DATA)
        except Exception as e:
            raise PermissionError(f"Failed to decrypt item. Key mismatch or corrupted data: {e}")

        header_len = int.from_bytes(decrypted[:4], byteorder="big")
        meta = json.loads(decrypted[4:4 + header_len].decode("utf-8"))
        payload = decrypted[4 + header_len:]

        item_type = meta.get("type", "file")
        original_name = meta.get("name", lp.name[:-10])

        if restore_dir is not None:
            dest_target = Path(restore_dir).resolve() / original_name
        else:
            orig_path_str = meta.get("original_path")
            if orig_path_str:
                dest_target = Path(orig_path_str)
            else:
                dest_target = lp.with_name(original_name)

        # Restore destination directory parent
        dest_target.parent.mkdir(parents=True, exist_ok=True)

        if item_type == "folder":
            tar_buf = io.BytesIO(payload)
            with tarfile.open(fileobj=tar_buf, mode="r:gz") as tar:
                # Path traversal check
                target_dir = dest_target.parent
                for member in tar.getmembers():
                    member_path = (target_dir / member.name).resolve()
                    if not str(member_path).startswith(str(target_dir)):
                        raise SecurityError(f"Malicious archive member path detected: {member.name}")
                if sys.version_info >= (3, 12):
                    try:
                        tar.extractall(path=target_dir, filter="data")
                    except TypeError:
                        tar.extractall(path=target_dir)
                else:
                    tar.extractall(path=target_dir)
        else:
            dest_target.write_bytes(payload)

        # Remove the .blinklock file
        lp.unlink()

        # Update registry
        data = self._load_raw()
        data["locked_items"] = [
            it for it in data.get("locked_items", [])
            if Path(it.get("locked_path", "")).resolve() != lp
        ]
        self._save_raw(data)

        print(f"[BlinkLock] Successfully unlocked {lp.name} -> {dest_target}")
        return str(dest_target)

    # --------------------------------------------------------------------------
    # File Manager Context Menu Integration (Linux & Windows)
    # --------------------------------------------------------------------------
    def install_context_menus(self) -> None:
        """Installs right-click context menu actions on Linux (Nemo, Nautilus, Caja) & Windows."""
        if IS_WINDOWS:
            self._install_context_menus_windows()
        else:
            self._install_context_menus_linux()

    def _install_context_menus_linux(self) -> None:
        bin_path = shutil.which("blink")
        if not bin_path or not os.path.exists(bin_path):
            if (Path.home() / "blink" / "bin" / "blink").exists():
                bin_path = str(Path.home() / "blink" / "bin" / "blink")
            else:
                bin_path = str(Path.home() / ".local" / "bin" / "blink")

        # 1. Nemo Actions (~/.local/share/nemo/actions)
        nemo_actions_dir = Path.home() / ".local" / "share" / "nemo" / "actions"
        nemo_actions_dir.mkdir(parents=True, exist_ok=True)

        lock_action = f"""[Nemo Action]
Active=true
Name=Lock with Blink
Comment=Protect and encrypt this file or folder with Blink Face ID
Exec={bin_path} lock %F
Icon-Name=blink
Selection=notnone
Extensions=any;
Quote=double
"""
        unlock_action = f"""[Nemo Action]
Active=true
Name=Unlock with Blink
Comment=Authenticate with Face ID to decrypt this item
Exec={bin_path} unlock %F
Icon-Name=blink
Selection=notnone
Extensions=blinklock;
Quote=double
"""
        (nemo_actions_dir / "blink_lock.nemo_action").write_text(lock_action, encoding="utf-8")
        (nemo_actions_dir / "blink_unlock.nemo_action").write_text(unlock_action, encoding="utf-8")

        # 2. File Manager Scripts (Nemo, Nautilus, Caja)
        script_dirs = [
            Path.home() / ".local" / "share" / "nemo" / "scripts",
            Path.home() / ".local" / "share" / "nautilus" / "scripts",
            Path.home() / ".config" / "caja" / "scripts",
        ]

        lock_script = f"""#!/usr/bin/env bash
for file in "$@"; do
    "{bin_path}" lock "$file"
done
"""
        unlock_script = f"""#!/usr/bin/env bash
for file in "$@"; do
    "{bin_path}" unlock "$file"
done
"""
        for sdir in script_dirs:
            try:
                sdir.mkdir(parents=True, exist_ok=True)
                ls = sdir / "Lock with Blink"
                ls.write_text(lock_script, encoding="utf-8")
                ls.chmod(0o755)

                us = sdir / "Unlock with Blink"
                us.write_text(unlock_script, encoding="utf-8")
                us.chmod(0o755)
            except Exception:
                pass

        # 3. Register .blinklock MIME type and default handler
        mime_dir = Path.home() / ".local" / "share" / "mime" / "packages"
        mime_dir.mkdir(parents=True, exist_ok=True)
        mime_xml = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
    <mime-type type="application/x-blink-lock">
        <comment>Blink Encrypted Container</comment>
        <glob pattern="*.blinklock"/>
        <icon name="blink"/>
    </mime-type>
</mime-info>
"""
        (mime_dir / "blink-lock.xml").write_text(mime_xml, encoding="utf-8")

        # 4. Desktop entry for opening .blinklock files
        app_dir = Path.home() / ".local" / "share" / "applications"
        app_dir.mkdir(parents=True, exist_ok=True)
        desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Blink Unlock
GenericName=Face ID Unlocker
Comment=Unlock encrypted files and folders with Face ID
Exec={bin_path} unlock %f
Icon=blink
Terminal=false
MimeType=application/x-blink-lock;
Categories=Utility;Security;
NoDisplay=true
"""
        (app_dir / "blink-unlock.desktop").write_text(desktop_entry, encoding="utf-8")

        try:
            subprocess.run(["update-mime-database", str(Path.home() / ".local" / "share" / "mime")], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["update-desktop-database", str(app_dir)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        print("[BlinkLock] Installed Linux file manager context menu actions and MIME associations.")

    def _install_context_menus_windows(self) -> None:
        """Configures Windows Explorer context menu entries via HKCU registry."""
        try:
            import winreg
            python_exe = sys.executable
            main_script = str(CONFIG_DIR.parent / "blink" / "blink" / "main.py")
            if not os.path.exists(main_script):
                main_script = str(Path(__file__).resolve().parent.parent / "main.py")

            cmd_lock = f'"{python_exe}" "{main_script}" lock "%1"'
            cmd_unlock = f'"{python_exe}" "{main_script}" unlock "%1"'

            # File context menu
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\BlinkLock") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Lock with Blink")
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, python_exe)
                with winreg.CreateKey(key, "command") as cmd_key:
                    winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd_lock)

            # Folder context menu
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\BlinkLock") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Lock with Blink")
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, python_exe)
                with winreg.CreateKey(key, "command") as cmd_key:
                    winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd_lock)

            # .blinklock file context menu
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.blinklock") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "BlinkLockFile")

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\BlinkLockFile\shell\open\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd_unlock)

            print("[BlinkLock] Windows registry context menu shortcuts configured.")
        except Exception as e:
            print(f"[BlinkLock] Notice: Could not write Windows registry context menu: {e}")


# ------------------------------------------------------------------------------
# Asynchronous Background App Lock Interceptor Watcher
# ------------------------------------------------------------------------------
class AppLockWatcher(threading.Thread):
    """Monitors active foreground window and enforces biometric verification on locked apps."""

    def __init__(self, coordinator: Any, lock_manager: Optional[BlinkLockManager] = None):
        super().__init__(daemon=True, name="BlinkAppLockWatcher")
        self.coordinator = coordinator
        self.lock_manager = lock_manager or BlinkLockManager.get_instance()
        self._running = False
        self._last_active_window_id: Optional[str] = None
        self._authenticating_lock = threading.Lock()

    def start_watcher(self) -> None:
        if not self._running:
            self._running = True
            if not self.is_alive():
                self.start()

    def stop_watcher(self) -> None:
        self._running = False

    def run(self) -> None:
        print("[AppLockWatcher] Active window interception service started.")
        while self._running:
            try:
                time.sleep(0.20)
                if not self.lock_manager.is_enabled():
                    continue

                from blink.system.lock_monitor import LockMonitor
                if LockMonitor.is_screen_actually_locked():
                    continue  # Screen lock handles whole-system lock

                locked_apps = self.lock_manager.get_locked_apps()
                if not locked_apps:
                    continue

                if IS_WINDOWS:
                    self._check_windows_active_window(locked_apps)
                else:
                    self._check_linux_active_window(locked_apps)
            except Exception as e:
                time.sleep(0.5)

    def _check_linux_active_window(self, locked_apps: List[Dict[str, Any]]) -> None:
        try:
            # Query active window ID
            out = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], stderr=subprocess.DEVNULL, timeout=0.15).decode("utf-8")
            if "window id #" not in out:
                return
            win_id_hex = out.split("window id #")[-1].strip().split()[0]
            if not win_id_hex or win_id_hex == "0x0":
                return

            if win_id_hex == self._last_active_window_id:
                return

            # Query WM_CLASS
            wm_out = subprocess.check_output(["xprop", "-id", win_id_hex, "WM_CLASS"], stderr=subprocess.DEVNULL, timeout=0.15).decode("utf-8")
            if "WM_CLASS" not in wm_out:
                return

            # Extract window class tokens
            # e.g.: WM_CLASS(STRING) = "google-chrome", "Google-chrome"
            parts = [p.strip().strip('"').lower() for p in wm_out.split("=")[-1].split(",")]

            matched_app = None
            for app in locked_apps:
                ak = app.get("key", "").lower()
                aexe = app.get("exe", "").lower()
                for part in parts:
                    if part == ak or part == aexe or ak in part or (aexe and aexe in part):
                        matched_app = app
                        break
                if matched_app:
                    break

            if matched_app is None:
                self._last_active_window_id = win_id_hex
                return

            app_key = matched_app.get("key", "")
            app_name = matched_app.get("name", "Application")

            if self.lock_manager.is_app_session_unlocked(app_key):
                self._last_active_window_id = win_id_hex
                return

            # App is locked! Immediately minimize the window to protect privacy
            print(f"[AppLockWatcher] Intercepted locked app '{app_name}' (Window {win_id_hex}). Minimizing...")
            try:
                # X11 Iconify window
                import ctypes
                x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
                x11.XOpenDisplay.restype = ctypes.c_void_p
                disp = x11.XOpenDisplay(None)
                if disp:
                    win_num = int(win_id_hex, 16)
                    x11.XIconifyWindow(ctypes.c_void_p(disp), ctypes.c_ulong(win_num), ctypes.c_int(0))
                    x11.XFlush(ctypes.c_void_p(disp))
                    x11.XCloseDisplay(ctypes.c_void_p(disp))
            except Exception:
                pass

            # Prompt biometric authentication on desktop
            threading.Thread(
                target=self._authenticate_app_unlock,
                args=(matched_app, win_id_hex),
                daemon=True
            ).start()

        except Exception:
            pass

    def _check_windows_active_window(self, locked_apps: List[Dict[str, Any]]) -> None:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return

            # Get process image name
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not hproc:
                return

            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
            kernel32.CloseHandle(hproc)

            exe_name = Path(buf.value).name.lower()
            matched_app = None
            for app in locked_apps:
                ak = app.get("key", "").lower()
                aexe = app.get("exe", "").lower()
                if exe_name == ak or exe_name == aexe or ak in exe_name or aexe in exe_name:
                    matched_app = app
                    break

            if matched_app is None:
                return

            app_key = matched_app.get("key", "")
            app_name = matched_app.get("name", "Application")

            if self.lock_manager.is_app_session_unlocked(app_key):
                return

            # Minimize window
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE

            threading.Thread(
                target=self._authenticate_app_unlock_win,
                args=(matched_app, hwnd),
                daemon=True
            ).start()
        except Exception:
            pass

    def _authenticate_app_unlock(self, app: Dict[str, Any], win_id_hex: str) -> None:
        if not self._authenticating_lock.acquire(blocking=False):
            return  # Already authenticating
        try:
            app_name = app.get("name", "Application")
            app_key = app.get("key", "")
            print(f"[AppLockWatcher] Prompting Face ID for '{app_name}'...")
            verified = self.coordinator.authenticate_face(purpose=f"Unlock {app_name}")
            if verified:
                self.lock_manager.grant_app_session_unlock(app_key)
                # Restore and raise window
                try:
                    import ctypes
                    x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
                    x11.XOpenDisplay.restype = ctypes.c_void_p
                    disp = x11.XOpenDisplay(None)
                    if disp:
                        win_num = int(win_id_hex, 16)
                        x11.XMapWindow(ctypes.c_void_p(disp), ctypes.c_ulong(win_num))
                        x11.XRaiseWindow(ctypes.c_void_p(disp), ctypes.c_ulong(win_num))
                        x11.XFlush(ctypes.c_void_p(disp))
                        x11.XCloseDisplay(ctypes.c_void_p(disp))
                except Exception:
                    pass
                self._last_active_window_id = win_id_hex
            else:
                print(f"[AppLockWatcher] Access denied for '{app_name}'.")
        finally:
            self._authenticating_lock.release()

    def _authenticate_app_unlock_win(self, app: Dict[str, Any], hwnd: int) -> None:
        if not self._authenticating_lock.acquire(blocking=False):
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            app_name = app.get("name", "Application")
            app_key = app.get("key", "")
            verified = self.coordinator.authenticate_face(purpose=f"Unlock {app_name}")
            if verified:
                self.lock_manager.grant_app_session_unlock(app_key)
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        finally:
            self._authenticating_lock.release()

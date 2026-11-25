"""
Blink Lock & Vault Manager.
Provides biometric application locking, file & folder encryption,
and native desktop file manager context menu integration for Linux & Windows.
"""

from __future__ import annotations

import ctypes
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
FOLDER_PAYLOAD_FILE = ".blink_payload.enc"
FOLDER_LAUNCHER_NAME = "🔒 Unlock with Face ID.desktop"


def set_folder_red_styling(folder_path: Path) -> None:
    if IS_WINDOWS or not folder_path.is_dir():
        return
    path_str = str(folder_path)
    try:
        cmds = [
            ["gio", "set", "-t", "string", path_str, "metadata::custom-icon-name", "folder-red"],
            ["gio", "set", "-t", "string", path_str, "metadata::nautilus-icon-name", "folder-red"],
            ["gio", "set", "-t", "string", path_str, "metadata::folder-color", "red"],
            ["gio", "set", "-t", "stringv", path_str, "metadata::emblems", "emblem-locked"],
        ]
        red_icon_candidates = [
            Path.home() / ".local/share/icons/WhiteSur-dark/places/scalable/folder-red.svg",
            Path.home() / ".local/share/icons/WhiteSur/places/scalable/folder-red.svg",
            Path("/usr/share/icons/Papirus/scalable/places/folder-red.svg"),
            Path("/usr/share/icons/ePapirus-Dark/16x16/places/folder-red.svg"),
        ]
        for cand in red_icon_candidates:
            if cand.exists():
                cmds.append(["gio", "set", "-t", "string", path_str, "metadata::custom-icon", cand.as_uri()])
                break
        for c in cmds:
            subprocess.Popen(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[BlinkLock] Note: could not set folder red styling: {e}")


def remove_folder_red_styling(folder_path: Path) -> None:
    if IS_WINDOWS or not folder_path.is_dir():
        return
    path_str = str(folder_path)
    try:
        attrs = [
            "metadata::custom-icon-name",
            "metadata::nautilus-icon-name",
            "metadata::folder-color",
            "metadata::custom-icon",
            "metadata::emblems",
        ]
        for attr in attrs:
            subprocess.Popen(["gio", "set", "-t", "unset", path_str, attr], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[BlinkLock] Note: could not remove folder red styling: {e}")


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5)
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xclient", _XClientMessageEvent),
        ("pad", ctypes.c_long * 24)
    ]


def unminimize_x11_window(win_id: str | int) -> bool:
    """Restores, unminimizes, and raises an X11 window over the current active window."""
    if IS_WINDOWS or not win_id:
        return False
    try:
        win_num = int(win_id, 16) if isinstance(win_id, str) and win_id.startswith("0x") else int(win_id)
        win_hex = hex(win_num)

        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
        x11.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        # Guard against BadMatch/BadWindow errors killing daemon process
        XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        _err_cb = XErrorHandler(lambda d, e: 0)
        x11.XSetErrorHandler.argtypes = [XErrorHandler]
        x11.XSetErrorHandler(_err_cb)

        disp = x11.XOpenDisplay(None)
        if not disp:
            return False
        try:
            root = x11.XDefaultRootWindow(disp)
            atom_active = x11.XInternAtom(disp, b"_NET_ACTIVE_WINDOW", False)
            atom_state = x11.XInternAtom(disp, b"_NET_WM_STATE", False)
            atom_hidden = x11.XInternAtom(disp, b"_NET_WM_STATE_HIDDEN", False)
            atom_demands = x11.XInternAtom(disp, b"_NET_WM_STATE_DEMANDS_ATTENTION", False)
            mask = 0x00100000 | 0x00020000  # SubstructureRedirectMask | SubstructureNotifyMask

            # 1. Remove _NET_WM_STATE_HIDDEN and _NET_WM_STATE_DEMANDS_ATTENTION
            ev_state = _XEvent()
            ev_state.type = 33
            ev_state.xclient.window = win_num
            ev_state.xclient.message_type = atom_state
            ev_state.xclient.format = 32
            ev_state.xclient.data[0] = 0  # _NET_WM_STATE_REMOVE
            ev_state.xclient.data[1] = atom_hidden
            ev_state.xclient.data[2] = atom_demands
            ev_state.xclient.data[3] = 2  # pager/user interaction
            ev_state.xclient.data[4] = 0
            x11.XSendEvent(disp, root, False, mask, ctypes.byref(ev_state))

            # 2. Direct map and raise to top of stacking order
            x11.XMapWindow(disp, win_num)
            x11.XMapRaised(disp, win_num)
            x11.XRaiseWindow(disp, win_num)

            # 3. Request window manager activate/focus window over current window
            ev_act = _XEvent()
            ev_act.type = 33
            ev_act.xclient.window = win_num
            ev_act.xclient.message_type = atom_active
            ev_act.xclient.format = 32
            ev_act.xclient.data[0] = 2  # pager source bypasses focus-stealing prevention
            ev_act.xclient.data[1] = 0
            ev_act.xclient.data[2] = 0
            ev_act.xclient.data[3] = 0
            ev_act.xclient.data[4] = 0
            x11.XSendEvent(disp, root, False, mask, ctypes.byref(ev_act))
            x11.XFlush(disp)
        finally:
            x11.XCloseDisplay(disp)

        # 4. Also dispatch non-blocking wmctrl raise/activate so Muffin brings it onto current workspace & above active window
        if shutil.which("wmctrl"):
            try:
                subprocess.Popen(
                    ["wmctrl", "-i", "-a", win_hex],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

        return True
    except Exception as e:
        print(f"[BlinkLock] Error unminimizing X11 window {win_id}: {e}")
        return False


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

    def get_auto_relock_on_screen_lock(self) -> bool:
        data = self._load_raw()
        return bool(data.get("auto_relock_on_screen_lock", False))

    def set_auto_relock_on_screen_lock(self, enabled: bool) -> None:
        data = self._load_raw()
        data["auto_relock_on_screen_lock"] = bool(enabled)
        self._save_raw(data)

    def get_active_app_sessions(self) -> Dict[str, float]:
        """Returns map of currently unlocked app keys still within their grace window."""
        grace = self.get_grace_period()
        if grace <= 0:
            return {}
        now = time.time()
        return {k: ts for k, ts in self._unlocked_app_sessions.items() if (now - ts) < grace}

    def get_recent_unlocked(self) -> List[Dict[str, Any]]:
        """Returns recently unlocked files/folders that still exist unencrypted on disk."""
        data = self._load_raw()
        recents = data.get("recent_unlocked", [])
        valid = []
        for r in recents:
            p = Path(r.get("path", ""))
            if p.exists() and not self.is_path_locked(p):
                valid.append(r)
        return valid

    def dismiss_recent_unlocked(self, target_path: str | Path) -> None:
        resolved = str(Path(target_path).resolve())
        data = self._load_raw()
        data["recent_unlocked"] = [
            r for r in data.get("recent_unlocked", [])
            if str(Path(r.get("path", "")).resolve()) != resolved
        ]
        self._save_raw(data)

    def relock_recent_items(self) -> int:
        """Smartly re-encrypts all tracked recently unlocked folders & files in-place."""
        count = 0
        for r in self.get_recent_unlocked():
            p = Path(r.get("path", ""))
            if p.exists() and not self.is_path_locked(p):
                try:
                    self.lock_path(p)
                    count += 1
                except Exception as e:
                    print(f"[BlinkLock] Could not auto re-lock {p}: {e}")
        return count

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

    def is_path_locked(self, target_path: str | Path) -> bool:
        """Checks if target path is an existing Blink Lock encrypted container."""
        p = Path(target_path).resolve()
        if not p.exists():
            return False
        if p.is_dir():
            payload = p / FOLDER_PAYLOAD_FILE
            if payload.exists():
                try:
                    with open(payload, "rb") as f:
                        return f.read(len(MAGIC_HEADER)) == MAGIC_HEADER
                except Exception:
                    return False
            for it in self.get_locked_items():
                if Path(it.get("locked_path", "")).resolve() == p:
                    return True
            return False
        try:
            with open(p, "rb") as f:
                return f.read(len(MAGIC_HEADER)) == MAGIC_HEADER
        except Exception:
            return False

    def get_container_metadata(self, target_path: str | Path) -> Optional[Dict[str, Any]]:
        """Reads metadata from an encrypted container without full payload decryption."""
        p = Path(target_path).resolve()
        if not p.exists():
            return None
        container_file = (p / FOLDER_PAYLOAD_FILE) if p.is_dir() else p
        if not container_file.exists():
            return None
        try:
            with open(container_file, "rb") as f:
                header = f.read(len(MAGIC_HEADER))
                if header != MAGIC_HEADER:
                    return None
                nonce = f.read(12)
                ciphertext = f.read()
            key = SecureCredentialManager.get_key()
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(nonce, ciphertext, ASSOCIATED_DATA)
            header_len = int.from_bytes(decrypted[:4], byteorder="big")
            return json.loads(decrypted[4:4 + header_len].decode("utf-8"))
        except Exception:
            return None

    def lock_path(self, target_path: str | Path) -> Dict[str, Any]:
        """Encrypts a file or directory into a clean AES-256-GCM package.
        Keeps original clean name without ugly .blinklock extension.
        Folders remain in-place as directories and visually turn red.
        """
        p = Path(target_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: {p}")

        if self.is_path_locked(p):
            raise ValueError(f"Target '{p.name}' is already protected by Blink.")

        is_folder = p.is_dir()
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
                for entry in p.iterdir():
                    if entry.name in (FOLDER_PAYLOAD_FILE, FOLDER_LAUNCHER_NAME):
                        continue
                    tar.add(str(entry), arcname=entry.name)
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

        if is_folder:
            # For a folder, keep the directory in-place and turn it RED
            payload_path = p / FOLDER_PAYLOAD_FILE
            temp_payload = p / f".tmp_{FOLDER_PAYLOAD_FILE}_{os.getpid()}"
            try:
                with open(temp_payload, "wb") as f:
                    f.write(MAGIC_HEADER)
                    f.write(nonce)
                    f.write(ciphertext)
                os.replace(temp_payload, payload_path)
            except Exception:
                if temp_payload.exists():
                    temp_payload.unlink()
                raise

            # Cleanly remove all plain unencrypted items inside folder
            for entry in list(p.iterdir()):
                if entry.name != FOLDER_PAYLOAD_FILE:
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()

            # Create desktop unlock launcher inside the folder
            bin_path = shutil.which("blink") or str(Path.home() / "blink" / "bin" / "blink")
            launcher_content = f"""[Desktop Entry]
Type=Application
Name=🔒 Unlock Folder with Face ID
Comment=Authenticate with Face ID to decrypt and open this folder
Exec={bin_path} shield --path "{p}"
Icon=blink
Terminal=false
"""
            launcher_path = p / FOLDER_LAUNCHER_NAME
            launcher_path.write_text(launcher_content, encoding="utf-8")
            try:
                launcher_path.chmod(0o755)
            except Exception:
                pass

            # Apply red styling to folder
            set_folder_red_styling(p)
            dest_path = p
        else:
            # Atomic write of the encrypted package to a hidden temp file
            temp_dest = p.with_name(f".{p.name}.blink_tmp_{os.getpid()}")
            try:
                with open(temp_dest, "wb") as f:
                    f.write(MAGIC_HEADER)
                    f.write(nonce)
                    f.write(ciphertext)
            except Exception:
                if temp_dest.exists():
                    temp_dest.unlink()
                raise

            # Cleanly remove unencrypted original file and replace with encrypted package
            p.unlink()
            os.replace(temp_dest, p)
            dest_path = p

            # Apply locked emblem to file
            if not IS_WINDOWS:
                try:
                    subprocess.run(["gio", "set", "-t", "stringv", str(p), "metadata::emblems", "emblem-locked"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

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
        # Remove from recent_unlocked since it is now re-locked
        data["recent_unlocked"] = [
            r for r in data.get("recent_unlocked", [])
            if str(Path(r.get("path", "")).resolve()) != str(dest_path)
        ]
        self._save_raw(data)

        print(f"[BlinkLock] Successfully locked {p.name} in-place.")
        return item_record

    def unlock_path(self, locked_path: str | Path, restore_dir: Optional[str | Path] = None) -> str:
        """Decrypts a Blink Lock package and restores the original file or directory structure."""
        lp = Path(locked_path).resolve()
        if not lp.exists():
            raise FileNotFoundError(f"Locked item not found: {lp}")

        is_folder_container = lp.is_dir()
        if is_folder_container:
            payload_path = lp / FOLDER_PAYLOAD_FILE
            if not payload_path.exists():
                raise ValueError(f"Not a valid Blink locked folder: {lp.name}")
            container_file = payload_path
        else:
            container_file = lp

        with open(container_file, "rb") as f:
            header = f.read(len(MAGIC_HEADER))
            if header != MAGIC_HEADER:
                raise ValueError(f"File is not a valid Blink Lock container: {container_file.name}")
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

        item_type = meta.get("type", "folder" if is_folder_container else "file")
        original_name = meta.get("name", lp.name[:-10] if lp.name.endswith(".blinklock") else lp.name)

        if is_folder_container:
            # Clean folder payload and launcher before restoring contents
            if (lp / FOLDER_PAYLOAD_FILE).exists():
                (lp / FOLDER_PAYLOAD_FILE).unlink()
            if (lp / FOLDER_LAUNCHER_NAME).exists():
                (lp / FOLDER_LAUNCHER_NAME).unlink()

            tar_buf = io.BytesIO(payload)
            with tarfile.open(fileobj=tar_buf, mode="r:gz") as tar:
                for member in tar.getmembers():
                    member_path = (lp / member.name).resolve()
                    if not str(member_path).startswith(str(lp)):
                        raise PermissionError(f"Malicious archive member path detected: {member.name}")
                if sys.version_info >= (3, 12):
                    try:
                        tar.extractall(path=lp, filter="data")
                    except TypeError:
                        tar.extractall(path=lp)
                else:
                    tar.extractall(path=lp)

            remove_folder_red_styling(lp)
            dest_target = lp

        elif item_type == "folder":
            # Backward compatibility for single-file folder containers
            lp.unlink()
            dest_target = lp
            dest_target.mkdir(parents=True, exist_ok=True)
            tar_buf = io.BytesIO(payload)
            with tarfile.open(fileobj=tar_buf, mode="r:gz") as tar:
                for member in tar.getmembers():
                    member_path = (dest_target / member.name).resolve()
                    if not str(member_path).startswith(str(dest_target)):
                        raise PermissionError(f"Malicious archive member path detected: {member.name}")
                if sys.version_info >= (3, 12):
                    try:
                        tar.extractall(path=dest_target, filter="data")
                    except TypeError:
                        tar.extractall(path=dest_target)
                else:
                    tar.extractall(path=dest_target)
            remove_folder_red_styling(dest_target)

        else:
            # File restore
            dest_target = lp
            dest_target.write_bytes(payload)
            if not IS_WINDOWS:
                try:
                    subprocess.run(["gio", "set", "-t", "unset", str(dest_target), "metadata::emblems"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

        # Update registry & recent unlocked history for 1-click re-lock
        data = self._load_raw()
        data["locked_items"] = [
            it for it in data.get("locked_items", [])
            if Path(it.get("locked_path", "")).resolve() != lp
        ]
        recents = [
            r for r in data.get("recent_unlocked", [])
            if str(Path(r.get("path", "")).resolve()) != str(dest_target)
        ]
        recents.insert(0, {
            "name": original_name,
            "path": str(dest_target),
            "type": item_type,
            "unlocked_at": time.time(),
        })
        data["recent_unlocked"] = recents[:10]
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
Exec={bin_path} shield --path %F
Icon-Name=blink
Selection=notnone
Extensions=any;
Quote=double
"""
        lock_app_action = f"""[Nemo Action]
Active=true
Name=Lock App with Blink
Comment=Require Face ID every time this application is opened
Exec={bin_path} lock-desktop-app %F
Icon-Name=blink
Selection=notnone
Extensions=desktop;
Quote=double
"""
        (nemo_actions_dir / "blink_lock.nemo_action").write_text(lock_action, encoding="utf-8")
        (nemo_actions_dir / "blink_unlock.nemo_action").write_text(unlock_action, encoding="utf-8")
        (nemo_actions_dir / "blink_lock_app.nemo_action").write_text(lock_app_action, encoding="utf-8")

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
    "{bin_path}" shield --path "$file"
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

        # 3. Register .blinklock & magic byte MIME type and default handler
        mime_dir = Path.home() / ".local" / "share" / "mime" / "packages"
        mime_dir.mkdir(parents=True, exist_ok=True)
        mime_xml = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
    <mime-type type="application/x-blink-lock">
        <comment>Blink Protected Container</comment>
        <magic priority="95">
            <match type="string" offset="0" value="BLINKLOCK"/>
        </magic>
        <glob pattern="*.blinklock"/>
        <icon name="blink"/>
    </mime-type>
</mime-info>
"""
        (mime_dir / "blink-lock.xml").write_text(mime_xml, encoding="utf-8")

        # 4. Desktop entry for opening Blink locked files
        app_dir = Path.home() / ".local" / "share" / "applications"
        app_dir.mkdir(parents=True, exist_ok=True)
        desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Blink Unlock
GenericName=Face ID Unlocker
Comment=Unlock encrypted files and folders with Face ID
Exec={bin_path} shield --path %f
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
        self._intercepted_windows: Set[str] = set()

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
                time.sleep(0.06)
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

            if win_id_hex == self._last_active_window_id or win_id_hex in self._intercepted_windows:
                return

            # Query WM_CLASS
            wm_out = subprocess.check_output(["xprop", "-id", win_id_hex, "WM_CLASS"], stderr=subprocess.DEVNULL, timeout=0.15).decode("utf-8")
            if "WM_CLASS" not in wm_out:
                return

            # Extract window class tokens + process comm name fallback for Snap/Flatpak/Electron apps
            parts = [p.strip().strip('"').lower() for p in wm_out.split("=")[-1].split(",")]
            try:
                pid_out = subprocess.check_output(["xprop", "-id", win_id_hex, "_NET_WM_PID"], stderr=subprocess.DEVNULL, timeout=0.10).decode("utf-8")
                if "=" in pid_out:
                    pid_str = pid_out.split("=")[-1].strip()
                    comm_path = Path(f"/proc/{pid_str}/comm")
                    if comm_path.exists():
                        parts.append(comm_path.read_text(encoding="utf-8", errors="ignore").strip().lower())
            except Exception:
                pass

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

            # Mark window as intercepted to prevent duplicate minimize loops
            self._intercepted_windows.add(win_id_hex)
            self._last_active_window_id = win_id_hex

            # App is locked! Immediately minimize the window to protect privacy
            print(f"[AppLockWatcher] Intercepted locked app '{app_name}' (Window {win_id_hex}). Minimizing...")
            try:
                # X11 Iconify window
                import ctypes
                x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
                x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
                x11.XOpenDisplay.restype = ctypes.c_void_p
                x11.XIconifyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
                x11.XFlush.argtypes = [ctypes.c_void_p]
                x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
                disp = x11.XOpenDisplay(None)
                if disp:
                    win_num = int(win_id_hex, 16)
                    x11.XIconifyWindow(disp, win_num, 0)
                    x11.XFlush(disp)
                    x11.XCloseDisplay(disp)
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
            app_icon = app.get("icon")
            print(f"[AppLockWatcher] Launching Face ID verification for '{app_name}'...")

            verified = False
            if self.coordinator and hasattr(self.coordinator, "authenticate_with_fallback"):
                verified = self.coordinator.authenticate_with_fallback(
                    target_name=app_name,
                    target_icon=app_icon,
                    max_attempts=2
                )
            else:
                main_py = Path(__file__).resolve().parent.parent / "main.py"
                bin_path = shutil.which("blink")
                if bin_path and os.path.exists(bin_path):
                    cmd = [bin_path, "shield", "--app", app_key, "--win-id", win_id_hex]
                else:
                    cmd = [sys.executable, str(main_py), "shield", "--app", app_key, "--win-id", win_id_hex]

                env = os.environ.copy()
                if "DISPLAY" not in env:
                    env["DISPLAY"] = ":0"
                res = subprocess.run(cmd, env=env)
                verified = (res.returncode == 0)

            if verified:
                print(f"[AppLockWatcher] Biometric unlock confirmed for '{app_name}'. Restoring window {win_id_hex}.")
                self.lock_manager.grant_app_session_unlock(app_key)
                self._last_active_window_id = win_id_hex
                unminimize_x11_window(win_id_hex)
            else:
                print(f"[AppLockWatcher] Authentication cancelled or denied for '{app_name}'. Window remains minimized.")
        finally:
            self._authenticating_lock.release()
            # Allow interacting with window
            time.sleep(0.5)
            self._intercepted_windows.discard(win_id_hex)

    def _authenticate_app_unlock_win(self, app: Dict[str, Any], hwnd: int) -> None:
        if not self._authenticating_lock.acquire(blocking=False):
            return
        try:
            app_name = app.get("name", "Application")
            app_key = app.get("key", "")
            main_py = Path(__file__).resolve().parent.parent / "main.py"
            res = subprocess.run([sys.executable, str(main_py), "shield", "--app", app_key, "--win-id", str(hwnd)])
            if res.returncode == 0:
                self.lock_manager.grant_app_session_unlock(app_key)
                import ctypes
                user32 = ctypes.windll.user32
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        finally:
            self._authenticating_lock.release()

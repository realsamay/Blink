"""
Secure credential and face data storage with AES-256-GCM encryption.
Supports session-key management, auto-locking idle sessions, and safe memory clearing.
"""

from __future__ import annotations

import base64
import os
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from blink.core.config import CONFIG_DIR

CREDENTIALS_FILE = CONFIG_DIR / "credentials.enc"
FACES_FILE = CONFIG_DIR / "faces.enc"
SALT_FILE = CONFIG_DIR / "salt.bin"


class SecureCredentialManager:
    """Manages encrypted password storage with session auto-locking."""
    _cached_key: Optional[bytes] = None
    _last_activity: float = 0.0

    @classmethod
    def _get_or_create_salt(cls) -> bytes:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not SALT_FILE.exists():
            salt = os.urandom(16)
            SALT_FILE.write_bytes(salt)
            return salt
        return SALT_FILE.read_bytes()

    @classmethod
    def derive_key(cls, secret: str) -> bytes:
        salt = cls._get_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        return kdf.derive(secret.encode("utf-8"))

    @classmethod
    def is_session_unlocked(cls) -> bool:
        return cls._cached_key is not None

    @classmethod
    def unlock_session(cls, session_key: Optional[bytes] = None) -> None:
        if session_key is None:
            # Generate or load active session key
            salt = cls._get_or_create_salt()
            # Derivation from machine-id + user for seamless session unlock
            machine_id = Path("/etc/machine-id")
            mid = machine_id.read_text().strip() if machine_id.exists() else "blink-default-id"
            user = os.environ.get("USER", "default-user")
            session_key = cls.derive_key(f"{mid}:{user}")
        cls._cached_key = session_key
        cls._last_activity = time.time()

    @classmethod
    def lock_session(cls) -> None:
        cls._cached_key = None
        cls._last_activity = 0.0

    @classmethod
    def check_idle_timeout(cls, max_idle_minutes: int) -> None:
        if not cls.is_session_unlocked():
            return
        if max_idle_minutes <= 0:
            return
        idle_seconds = time.time() - cls._last_activity
        if idle_seconds > (max_idle_minutes * 60):
            print(f"[Crypto] Session idle timeout reached ({idle_seconds:.1f}s). Locking session.")
            cls.lock_session()

    @classmethod
    def record_activity(cls) -> None:
        if cls.is_session_unlocked():
            cls._last_activity = time.time()

    @classmethod
    def has_stored_password(cls) -> bool:
        return CREDENTIALS_FILE.exists() and CREDENTIALS_FILE.stat().st_size > 0

    @classmethod
    def save_password(cls, password: str) -> None:
        if not cls.is_session_unlocked():
            cls.unlock_session()
        assert cls._cached_key is not None

        aesgcm = AESGCM(cls._cached_key)
        nonce = os.urandom(12)
        plaintext = password.encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        payload = {
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "data": base64.b64encode(ciphertext).decode("ascii"),
        }
        CREDENTIALS_FILE.write_text(json.dumps(payload), encoding="utf-8")
        cls.record_activity()

    @classmethod
    def read_password(cls) -> Optional[str]:
        if not cls.has_stored_password():
            return None
        if not cls.is_session_unlocked():
            cls.unlock_session()
        assert cls._cached_key is not None

        try:
            payload = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["data"])
            aesgcm = AESGCM(cls._cached_key)
            plaintext_bytes = bytearray(aesgcm.decrypt(nonce, ciphertext, None))
            password = plaintext_bytes.decode("utf-8")
            # Clear sensitive bytearray
            for i in range(len(plaintext_bytes)):
                plaintext_bytes[i] = 0
            cls.record_activity()
            return password
        except Exception as e:
            print(f"[Crypto] Failed to decrypt password: {e}")
            return None

    @classmethod
    def remove_password(cls) -> None:
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
        cls.lock_session()


class SecureFaceStore:
    """Encrypted store for enrolled face identities and embeddings."""

    @classmethod
    def load_identities(cls) -> List[Dict[str, Any]]:
        if not FACES_FILE.exists():
            return []
        if not SecureCredentialManager.is_session_unlocked():
            SecureCredentialManager.unlock_session()
        key = SecureCredentialManager._cached_key
        if key is None:
            return []

        try:
            payload = json.loads(FACES_FILE.read_text(encoding="utf-8"))
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["data"])
            aesgcm = AESGCM(key)
            data = aesgcm.decrypt(nonce, ciphertext, None)
            identities = json.loads(data.decode("utf-8"))
            return identities
        except Exception as e:
            print(f"[SecureFaceStore] Error reading {FACES_FILE}: {e}")
            return []

    @classmethod
    def save_identities(cls, identities: List[Dict[str, Any]]) -> None:
        if not SecureCredentialManager.is_session_unlocked():
            SecureCredentialManager.unlock_session()
        key = SecureCredentialManager._cached_key
        assert key is not None

        data = json.dumps(identities).encode("utf-8")
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, data, None)

        payload = {
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "data": base64.b64encode(ciphertext).decode("ascii"),
        }
        FACES_FILE.write_text(json.dumps(payload), encoding="utf-8")

"""
Unit tests for Blink Lock & Vault Manager.
Tests file & folder encryption, archive integrity, and application lock management.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from blink.core.lock_manager import BlinkLockManager, MAGIC_HEADER
from blink.core.crypto import SecureCredentialManager


class TestBlinkLockManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="blink_lock_test_")
        self.mgr = BlinkLockManager.get_instance()
        # Ensure session key is available
        SecureCredentialManager.unlock_session()

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_file_lock_and_unlock(self):
        # 1. Create a plain text test file
        test_file = Path(self.temp_dir) / "classified_document.txt"
        secret_content = b"Super secret credentials and biometric data." * 20
        test_file.write_bytes(secret_content)

        # 2. Lock the file
        record = self.mgr.lock_path(test_file)
        self.assertFalse(test_file.exists(), "Original file must be removed after locking!")
        locked_path = Path(record["locked_path"])
        self.assertTrue(locked_path.exists(), "Encrypted .blinklock file must exist!")

        # 3. Verify magic bytes on disk
        raw_header = locked_path.read_bytes()[:len(MAGIC_HEADER)]
        self.assertEqual(raw_header, MAGIC_HEADER)

        # 4. Unlock the file
        restored_path = self.mgr.unlock_path(locked_path)
        self.assertTrue(Path(restored_path).exists(), "Original file must be restored!")
        self.assertFalse(locked_path.exists(), ".blinklock container must be removed after unlock!")
        self.assertEqual(Path(restored_path).read_bytes(), secret_content)

    def test_folder_lock_and_unlock(self):
        # 1. Create a test directory with nested files
        test_folder = Path(self.temp_dir) / "MyVaultFolder"
        test_folder.mkdir(parents=True)
        (test_folder / "file1.txt").write_text("Hello inside folder 1")
        sub = test_folder / "nested"
        sub.mkdir()
        (sub / "file2.txt").write_text("Hello inside nested folder 2")

        # 2. Lock the folder
        record = self.mgr.lock_path(test_folder)
        self.assertFalse(test_folder.exists(), "Original directory must be removed after locking!")
        locked_path = Path(record["locked_path"])
        self.assertTrue(locked_path.exists())
        self.assertTrue(str(locked_path).endswith(".blinklock"))

        # 3. Unlock the folder
        restored = self.mgr.unlock_path(locked_path)
        self.assertTrue(Path(restored).is_dir(), "Restored item must be a directory!")
        self.assertFalse(locked_path.exists())
        self.assertEqual((Path(restored) / "file1.txt").read_text(), "Hello inside folder 1")
        self.assertEqual((Path(restored) / "nested" / "file2.txt").read_text(), "Hello inside nested folder 2")

    def test_app_lock_management(self):
        app_name = "Test Discord"
        app_key = "discord-test-app"

        self.mgr.add_locked_app(app_name, app_key)
        self.assertTrue(self.mgr.is_app_locked(app_key))

        # Initially session is not unlocked
        self.assertFalse(self.mgr.is_app_session_unlocked(app_key))

        # Grant session unlock
        self.mgr.grant_app_session_unlock(app_key)
        self.assertTrue(self.mgr.is_app_session_unlocked(app_key))

        # Revoke sessions
        self.mgr.revoke_all_app_sessions()
        self.assertFalse(self.mgr.is_app_session_unlocked(app_key))

        # Remove app
        self.mgr.remove_locked_app(app_key)
        self.assertFalse(self.mgr.is_app_locked(app_key))


if __name__ == "__main__":
    unittest.main()

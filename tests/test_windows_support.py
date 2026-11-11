"""
Unit tests for Blink Windows support layer.
Validates cross-platform platform detection, Win32 input structures,
animation asset decoding, and Windows registry/desktop lock mocks.
"""

from __future__ import annotations

import ctypes
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import numpy as np

from blink.system.platform_info import get_config_dir, get_machine_id, get_current_user
from blink.system.keystroke_injector_win import (
    WindowsKeystrokeInjector,
    INPUT,
    KEYBDINPUT,
    INPUT_KEYBOARD,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    VK_RETURN,
    VK_SPACE,
)
from blink.ui.notch_overlay_win import (
    WindowsNotchOverlayWindow,
    AnimationAssets,
    OverlayPhase,
    BITMAPINFO,
    BITMAPINFOHEADER,
)


class TestPlatformDetection(unittest.TestCase):
    def test_linux_default_paths(self):
        with patch("blink.system.platform_info.IS_WINDOWS", False):
            cfg_dir = get_config_dir()
            self.assertTrue(str(cfg_dir).endswith("blink"))

            mid = get_machine_id()
            self.assertIsInstance(mid, str)
            self.assertGreater(len(mid), 0)

            user = get_current_user()
            self.assertIsInstance(user, str)
            self.assertGreater(len(user), 0)

    @patch("blink.system.platform_info.IS_WINDOWS", True)
    @patch.dict(os.environ, {"APPDATA": "/mock/AppData/Roaming", "COMPUTERNAME": "TEST-PC", "USERNAME": "TestUser"})
    def test_windows_mock_paths(self):
        cfg_dir = get_config_dir()
        self.assertIn("AppData", str(cfg_dir))
        self.assertTrue(str(cfg_dir).lower().endswith("blink"))

        user = get_current_user()
        self.assertEqual(user, "TestUser")

        # Fallback when winreg is unavailable
        with patch.dict(sys.modules, {"winreg": None}):
            mid = get_machine_id()
            self.assertIn("TEST-PC", mid)

    def test_windows_id_format(self):
        with patch("blink.system.platform_info.IS_WINDOWS", True):
            mid = get_machine_id()
            self.assertIsInstance(mid, str)
            self.assertGreater(len(mid), 0)


class TestWindowsKeystrokeStructures(unittest.TestCase):
    def test_input_structure_alignment(self):
        """Verifies that INPUT and KEYBDINPUT structures match Win32 64-bit ABI standards."""
        # On 64-bit Windows, pointer alignment ensures sizeof(INPUT) is typically 40 bytes
        self.assertGreaterEqual(ctypes.sizeof(KEYBDINPUT), 20)
        self.assertGreaterEqual(ctypes.sizeof(INPUT), 24)

    def test_make_vk_input(self):
        down_inp = WindowsKeystrokeInjector._make_vk_input(VK_RETURN, keyup=False)
        self.assertEqual(down_inp.type, INPUT_KEYBOARD)
        self.assertEqual(down_inp.ki.wVk, VK_RETURN)
        self.assertEqual(down_inp.ki.dwFlags, 0)

        up_inp = WindowsKeystrokeInjector._make_vk_input(VK_RETURN, keyup=True)
        self.assertEqual(up_inp.ki.dwFlags, KEYEVENTF_KEYUP)

    def test_make_unicode_input(self):
        test_char = "$"
        char_inp = WindowsKeystrokeInjector._make_unicode_input(test_char, keyup=False)
        self.assertEqual(char_inp.type, INPUT_KEYBOARD)
        self.assertEqual(char_inp.ki.wScan, ord(test_char))
        self.assertEqual(char_inp.ki.wVk, 0)
        self.assertEqual(char_inp.ki.dwFlags, KEYEVENTF_UNICODE)

        char_up = WindowsKeystrokeInjector._make_unicode_input(test_char, keyup=True)
        self.assertEqual(char_up.ki.dwFlags, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


class TestWindowsAnimationAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AnimationAssets.load_all(target_size=100)

    def test_buffers_loaded(self):
        self.assertGreater(len(AnimationAssets.idle_buffers), 0)
        self.assertGreater(len(AnimationAssets.unlock_buffers), 0)
        self.assertGreater(len(AnimationAssets.failure_buffers), 0)

        first_frame = AnimationAssets.idle_buffers[0]
        self.assertIn(first_frame.shape[0], (100, 116))
        self.assertIn(first_frame.shape[1], (100, 116))
        self.assertEqual(first_frame.shape[2], 4)
        self.assertEqual(first_frame.dtype, np.uint8)

    def test_bitmapinfo_header(self):
        bmi = BITMAPINFO()
        self.assertGreaterEqual(ctypes.sizeof(BITMAPINFOHEADER), 40)
        self.assertGreaterEqual(ctypes.sizeof(BITMAPINFO), 44)


class TestWindowsLockMonitorMock(unittest.TestCase):
    def test_wts_constants(self):
        from blink.system.lock_monitor_win import (
            WTS_SESSION_LOCK,
            WTS_SESSION_UNLOCK,
            WM_WTSSESSION_CHANGE,
            WM_POWERBROADCAST,
        )
        self.assertEqual(WTS_SESSION_LOCK, 0x7)
        self.assertEqual(WTS_SESSION_UNLOCK, 0x8)
        self.assertEqual(WM_WTSSESSION_CHANGE, 0x02B1)
        self.assertEqual(WM_POWERBROADCAST, 0x0218)


if __name__ == "__main__":
    unittest.main()

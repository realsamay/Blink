"""
Windows Keystroke Injector using Win32 SendInput API.
Performs ultra-fast, kernel-level credential injection on Windows 10/11 lockscreens.
Uses KEYEVENTF_UNICODE for layout-independent, instantaneous (<5ms) character entry.
"""

from __future__ import annotations

import ctypes
import time
from typing import Optional


# Win32 Constants
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_SPACE = 0x20
VK_ESCAPE = 0x1B
VK_A = 0x41


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", _INPUT_UNION),
    ]


class WindowsKeystrokeInjector:
    """High-performance Windows credential injector via SendInput."""

    @classmethod
    def is_available(cls) -> bool:
        """SendInput is available on all Windows NT versions (10, 11, Server)."""
        return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32")

    @classmethod
    def warmup(cls) -> None:
        """No-op on Windows as user32.SendInput is a persistent system DLL."""
        pass

    @classmethod
    def _make_vk_input(cls, vk: int, keyup: bool = False) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        flags = KEYEVENTF_KEYUP if keyup else 0
        inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None)
        return inp

    @classmethod
    def _make_unicode_input(cls, char: str, keyup: bool = False) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        flags = KEYEVENTF_UNICODE
        if keyup:
            flags |= KEYEVENTF_KEYUP
        inp.ki = KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0, dwExtraInfo=None)
        return inp

    @classmethod
    def wake_lockscreen(cls) -> bool:
        """
        Sends a single keypress (Space or Enter) to dismiss the Windows 10/11
        lockscreen photo curtain and reveal the credential input field.
        """
        if not cls.is_available():
            return False
        try:
            inputs = (INPUT * 2)(
                cls._make_vk_input(VK_SPACE, keyup=False),
                cls._make_vk_input(VK_SPACE, keyup=True),
            )
            ctypes.windll.user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
            return True
        except Exception as e:
            print(f"[WindowsKeystrokeInjector] Wake lockscreen error: {e}")
            return False

    @classmethod
    def type_password(cls, password: str, clear_field_first: bool = True) -> bool:
        """
        Batches and injects all credential keystrokes into the OS input queue in < 5ms.
        Uses KEYEVENTF_UNICODE for 100% layout invariance (QWERTY, AZERTY, QWERTZ, etc.).
        """
        if not cls.is_available():
            print("[WindowsKeystrokeInjector] user32.SendInput is not available.")
            return False

        try:
            input_list = []

            if clear_field_first:
                # 1. Ctrl + A (Select all in password box)
                input_list.append(cls._make_vk_input(VK_CONTROL, keyup=False))
                input_list.append(cls._make_vk_input(VK_A, keyup=False))
                input_list.append(cls._make_vk_input(VK_A, keyup=True))
                input_list.append(cls._make_vk_input(VK_CONTROL, keyup=True))

                # 2. Backspace (Clear)
                input_list.append(cls._make_vk_input(VK_BACK, keyup=False))
                input_list.append(cls._make_vk_input(VK_BACK, keyup=True))

            # 3. Stream characters as UNICODE scancodes
            for char in password:
                input_list.append(cls._make_unicode_input(char, keyup=False))
                input_list.append(cls._make_unicode_input(char, keyup=True))

            # 4. Press Enter to submit password
            input_list.append(cls._make_vk_input(VK_RETURN, keyup=False))
            input_list.append(cls._make_vk_input(VK_RETURN, keyup=True))

            # Send the entire batch in a single atomic Win32 syscall
            n_events = len(input_list)
            input_array = (INPUT * n_events)(*input_list)
            sent = ctypes.windll.user32.SendInput(n_events, input_array, ctypes.sizeof(INPUT))

            if sent != n_events:
                print(f"[WindowsKeystrokeInjector] Warning: Sent {sent}/{n_events} inputs.")
            return sent > 0
        except Exception as e:
            print(f"[WindowsKeystrokeInjector] Failed to inject credentials: {e}")
            return False

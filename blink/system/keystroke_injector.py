"""
KeystrokeInjector for Linux using /dev/uinput and evdev.
Synthesizes keyboard events at the kernel input layer, reaching lock screens
across X11 and Wayland (Cinnamon, GNOME, KDE, SDDM, LightDM, etc.).
"""

from __future__ import annotations

import os
import time
from typing import Optional, Tuple
import evdev
from evdev import ecodes


# Character to (keycode, requires_shift) mapping
CHAR_MAP = {
    # Lowercase letters
    'a': (ecodes.KEY_A, False), 'b': (ecodes.KEY_B, False), 'c': (ecodes.KEY_C, False),
    'd': (ecodes.KEY_D, False), 'e': (ecodes.KEY_E, False), 'f': (ecodes.KEY_F, False),
    'g': (ecodes.KEY_G, False), 'h': (ecodes.KEY_H, False), 'i': (ecodes.KEY_I, False),
    'j': (ecodes.KEY_J, False), 'k': (ecodes.KEY_K, False), 'l': (ecodes.KEY_L, False),
    'm': (ecodes.KEY_M, False), 'n': (ecodes.KEY_N, False), 'o': (ecodes.KEY_O, False),
    'p': (ecodes.KEY_P, False), 'q': (ecodes.KEY_Q, False), 'r': (ecodes.KEY_R, False),
    's': (ecodes.KEY_S, False), 't': (ecodes.KEY_T, False), 'u': (ecodes.KEY_U, False),
    'v': (ecodes.KEY_V, False), 'w': (ecodes.KEY_W, False), 'x': (ecodes.KEY_X, False),
    'y': (ecodes.KEY_Y, False), 'z': (ecodes.KEY_Z, False),
    # Uppercase letters
    'A': (ecodes.KEY_A, True), 'B': (ecodes.KEY_B, True), 'C': (ecodes.KEY_C, True),
    'D': (ecodes.KEY_D, True), 'E': (ecodes.KEY_E, True), 'F': (ecodes.KEY_F, True),
    'G': (ecodes.KEY_G, True), 'H': (ecodes.KEY_H, True), 'I': (ecodes.KEY_I, True),
    'J': (ecodes.KEY_J, True), 'K': (ecodes.KEY_K, True), 'L': (ecodes.KEY_L, True),
    'M': (ecodes.KEY_M, True), 'N': (ecodes.KEY_N, True), 'O': (ecodes.KEY_O, True),
    'P': (ecodes.KEY_P, True), 'Q': (ecodes.KEY_Q, True), 'R': (ecodes.KEY_R, True),
    'S': (ecodes.KEY_S, True), 'T': (ecodes.KEY_T, True), 'U': (ecodes.KEY_U, True),
    'V': (ecodes.KEY_V, True), 'W': (ecodes.KEY_W, True), 'X': (ecodes.KEY_X, True),
    'Y': (ecodes.KEY_Y, True), 'Z': (ecodes.KEY_Z, True),
    # Numbers
    '1': (ecodes.KEY_1, False), '2': (ecodes.KEY_2, False), '3': (ecodes.KEY_3, False),
    '4': (ecodes.KEY_4, False), '5': (ecodes.KEY_5, False), '6': (ecodes.KEY_6, False),
    '7': (ecodes.KEY_7, False), '8': (ecodes.KEY_8, False), '9': (ecodes.KEY_9, False),
    '0': (ecodes.KEY_0, False),
    # Shifted symbols (US QWERTY)
    '!': (ecodes.KEY_1, True), '@': (ecodes.KEY_2, True), '#': (ecodes.KEY_3, True),
    '$': (ecodes.KEY_4, True), '%': (ecodes.KEY_5, True), '^': (ecodes.KEY_6, True),
    '&': (ecodes.KEY_7, True), '*': (ecodes.KEY_8, True), '(': (ecodes.KEY_9, True),
    ')': (ecodes.KEY_0, True), '_': (ecodes.KEY_MINUS, True), '+': (ecodes.KEY_EQUAL, True),
    '{': (ecodes.KEY_LEFTBRACE, True), '}': (ecodes.KEY_RIGHTBRACE, True),
    '|': (ecodes.KEY_BACKSLASH, True), ':': (ecodes.KEY_SEMICOLON, True),
    '"': (ecodes.KEY_APOSTROPHE, True), '<': (ecodes.KEY_COMMA, True),
    '>': (ecodes.KEY_DOT, True), '?': (ecodes.KEY_SLASH, True),
    '~': (ecodes.KEY_GRAVE, True),
    # Unshifted symbols
    ' ': (ecodes.KEY_SPACE, False), '-': (ecodes.KEY_MINUS, False), '=': (ecodes.KEY_EQUAL, False),
    '[': (ecodes.KEY_LEFTBRACE, False), ']': (ecodes.KEY_RIGHTBRACE, False),
    '\\': (ecodes.KEY_BACKSLASH, False), ';': (ecodes.KEY_SEMICOLON, False),
    "'": (ecodes.KEY_APOSTROPHE, False), ',': (ecodes.KEY_COMMA, False),
    '.': (ecodes.KEY_DOT, False), '/': (ecodes.KEY_SLASH, False),
    '`': (ecodes.KEY_GRAVE, False), '\t': (ecodes.KEY_TAB, False),
}


class KeystrokeInjector:
    @classmethod
    def is_available(cls) -> bool:
        return os.access("/dev/uinput", os.W_OK)

    @classmethod
    def type_password(cls, password: str, clear_field_first: bool = True) -> bool:
        """
        Emits virtual keyboard strokes to type the password and press Enter.
        """
        if not cls.is_available():
            print("[KeystrokeInjector] /dev/uinput is not writable.")
            return False

        # Build capability dictionary for UInput
        all_keys = {code for code, _ in CHAR_MAP.values()}
        all_keys.update([
            ecodes.KEY_LEFTSHIFT, ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT,
            ecodes.KEY_A, ecodes.KEY_BACKSPACE, ecodes.KEY_DELETE,
            ecodes.KEY_ENTER, ecodes.KEY_SPACE, ecodes.KEY_END
        ])
        cap = {ecodes.EV_KEY: list(all_keys)}

        try:
            ui = evdev.UInput(cap, name="blink-virtual-keyboard")
        except Exception as e:
            print(f"[KeystrokeInjector] Error creating UInput: {e}")
            return False

        try:
            time.sleep(0.05)

            if clear_field_first:
                # Clear existing lockscreen text field: Ctrl + A, then Backspace
                ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
                ui.write(ecodes.EV_KEY, ecodes.KEY_A, 1)
                ui.syn()
                time.sleep(0.015)
                ui.write(ecodes.EV_KEY, ecodes.KEY_A, 0)
                ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
                ui.syn()
                time.sleep(0.015)

                ui.write(ecodes.EV_KEY, ecodes.KEY_BACKSPACE, 1)
                ui.syn()
                time.sleep(0.015)
                ui.write(ecodes.EV_KEY, ecodes.KEY_BACKSPACE, 0)
                ui.syn()
                time.sleep(0.02)

            # Type password characters
            for char in password:
                if char not in CHAR_MAP:
                    continue
                keycode, shift = CHAR_MAP[char]

                if shift:
                    ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
                    ui.syn()
                    time.sleep(0.01)

                ui.write(ecodes.EV_KEY, keycode, 1)
                ui.syn()
                time.sleep(0.012)
                ui.write(ecodes.EV_KEY, keycode, 0)
                ui.syn()
                time.sleep(0.01)

                if shift:
                    ui.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
                    ui.syn()
                    time.sleep(0.01)

            # Press Enter / Return
            time.sleep(0.02)
            ui.write(ecodes.EV_KEY, ecodes.KEY_ENTER, 1)
            ui.syn()
            time.sleep(0.015)
            ui.write(ecodes.EV_KEY, ecodes.KEY_ENTER, 0)
            ui.syn()
            time.sleep(0.02)

            return True
        except Exception as e:
            print(f"[KeystrokeInjector] Failed to inject keystrokes: {e}")
            return False
        finally:
            ui.close()

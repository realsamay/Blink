"""
Cross-platform Windows Workstation Lock, Sleep, and Activity Monitor.
Uses Win32 WTSRegisterSessionNotification, WM_POWERBROADCAST, OpenInputDesktop,
and GetLastInputInfo to deliver instantaneous (<1ms) lockscreen lifecycle events.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
from typing import Callable, Optional


# Win32 Constants
WM_DESTROY = 0x0002
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1

WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WTS_SESSION_LOGON = 0x5
WTS_SESSION_LOGOFF = 0x6

NOTIFY_FOR_THIS_SESSION = 0
NOTIFY_FOR_ALL_SESSIONS = 1

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

HWND_MESSAGE = ctypes.c_void_p(-3)
DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_ulong),
    ]


def get_system_idle_ms() -> int:
    """Returns exact milliseconds elapsed since the last user mouse/keyboard input."""
    if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
        return 0
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            curr_tick = ctypes.windll.kernel32.GetTickCount()
            return max(0, int(curr_tick - lii.dwTime))
    except Exception:
        pass
    return 0


class WindowsLockMonitor:
    """Monitors workstation lock, unlock, sleep, resume, and lockscreen idle on Windows 10/11."""

    def __init__(
        self,
        on_locked: Optional[Callable[[], None]] = None,
        on_unlocked: Optional[Callable[[], None]] = None,
        on_wake: Optional[Callable[[], None]] = None,
        on_sleep: Optional[Callable[[], None]] = None,
        on_activity: Optional[Callable[[], None]] = None,
    ):
        self.on_locked = on_locked
        self.on_unlocked = on_unlocked
        self.on_wake = on_wake
        self.on_sleep = on_sleep
        self.on_activity = on_activity

        self.is_screen_locked: bool = False
        self.is_sleeping: bool = False
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_poll_idle = 0

    @classmethod
    def is_screen_actually_locked(cls) -> bool:
        """
        Authoritatively queries Windows desktop state.
        When workstation is locked, input desktop switches to 'Winlogon'.
        """
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "user32"):
            return False
        try:
            user32 = ctypes.windll.user32
            # OpenInputDesktop returns 0 or the Winlogon desktop when workstation is locked
            hdesk = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
            if not hdesk:
                return True
            name_buf = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_ulong()
            user32.GetUserObjectInformationW(
                hdesk,
                UOI_NAME,
                ctypes.byref(name_buf),
                ctypes.sizeof(name_buf),
                ctypes.byref(needed),
            )
            user32.CloseDesktop(hdesk)
            return name_buf.value.lower() == "winlogon"
        except Exception:
            return False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.is_screen_locked = self.is_screen_actually_locked()

        self._thread = threading.Thread(
            target=self._run_message_loop, daemon=True, name="BlinkWinLockMonitor"
        )
        self._thread.start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="BlinkWinLockPoller"
        )
        self._poll_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._hwnd and hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
            try:
                ctypes.windll.user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=0.5)

    def _run_message_loop(self) -> None:
        """Creates a hidden message-only window and pumps Win32 session change messages."""
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            wtsapi32 = ctypes.windll.wtsapi32

            WNDPROC = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            def wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_WTSSESSION_CHANGE:
                    if wparam == WTS_SESSION_LOCK:
                        print("[WindowsLockMonitor] Event: Workstation Locked.")
                        self.is_screen_locked = True
                        if self.on_locked:
                            self.on_locked()
                    elif wparam in (WTS_SESSION_UNLOCK, WTS_SESSION_LOGON):
                        print("[WindowsLockMonitor] Event: Workstation Unlocked.")
                        self.is_screen_locked = False
                        if self.on_unlocked:
                            self.on_unlocked()

                elif msg == WM_POWERBROADCAST:
                    if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                        print("[WindowsLockMonitor] Event: System Resumed from Sleep.")
                        self.is_sleeping = False
                        if self.on_wake:
                            self.on_wake()
                    elif wparam == PBT_APMSUSPEND:
                        print("[WindowsLockMonitor] Event: System Suspending.")
                        self.is_sleeping = True
                        if self.on_sleep:
                            self.on_sleep()

                elif msg == WM_DESTROY:
                    wtsapi32.WTSUnRegisterSessionNotification(hwnd)
                    user32.PostQuitMessage(0)
                    return 0

                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            # Prevent garbage collection of callback
            self._wnd_proc_cb = WNDPROC(wnd_proc)

            class WNDCLASSEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HICON),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON),
                ]

            wndclass = WNDCLASSEXW()
            wndclass.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wndclass.lpfnWndProc = self._wnd_proc_cb
            wndclass.hInstance = kernel32.GetModuleHandleW(None)
            wndclass.lpszClassName = "BlinkSessionMonitorClass"

            user32.RegisterClassExW(ctypes.byref(wndclass))

            self._hwnd = user32.CreateWindowExW(
                0,
                wndclass.lpszClassName,
                "BlinkSessionMonitorWindow",
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                None,
                wndclass.hInstance,
                None,
            )

            if self._hwnd:
                wtsapi32.WTSRegisterSessionNotification(
                    self._hwnd, NOTIFY_FOR_THIS_SESSION
                )
                print("[WindowsLockMonitor] Registered WTSSessionNotification successfully.")

            msg = wintypes.MSG()
            while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        except Exception as e:
            print(f"[WindowsLockMonitor] Message loop error: {e}")

    def _poll_loop(self) -> None:
        """Polls for screen lock changes and user interaction at the lockscreen."""
        last_lock_state = self.is_screen_locked
        while self._running:
            time.sleep(0.12)
            try:
                locked = self.is_screen_actually_locked()
                if locked != last_lock_state:
                    last_lock_state = locked
                    self.is_screen_locked = locked
                    if locked:
                        print("[WindowsLockMonitor] Poller detected screen locked.")
                        if self.on_locked:
                            self.on_locked()
                    else:
                        print("[WindowsLockMonitor] Poller detected screen unlocked.")
                        if self.on_unlocked:
                            self.on_unlocked()

                # User activity detection at lockscreen
                if self.is_screen_locked and self.on_activity:
                    idle_ms = get_system_idle_ms()
                    if self._last_poll_idle > 600 and idle_ms < 300:
                        print(f"[WindowsLockMonitor] Detected user interaction on lockscreen (idle reset: {self._last_poll_idle}ms -> {idle_ms}ms).")
                        self.on_activity()
                    self._last_poll_idle = idle_ms

            except Exception:
                pass

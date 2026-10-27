"""
Cross-distribution Linux Screen Lock and Wake Monitor via D-Bus, X11 idle detection, and systemd-logind.
Supports Cinnamon (Linux Mint), GNOME, KDE Plasma, MATE, XFCE, and standard systemd sleep/wake.
"""

from __future__ import annotations

import ctypes
import subprocess
import threading
import time
from typing import Callable, Optional

import pydbus
from gi.repository import GLib


class XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("eventMask", ctypes.c_ulong),
    ]


try:
    _x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    _xss = ctypes.cdll.LoadLibrary("libXss.so.1")
    _x11_dpy = _x11.XOpenDisplay(None)
    _x11_root = _x11.XDefaultRootWindow(_x11_dpy) if _x11_dpy else None
except Exception:
    _x11 = None
    _xss = None
    _x11_dpy = None
    _x11_root = None


def get_system_idle_ms() -> int:
    """Returns exact user idle time in milliseconds using XScreenSaverQueryInfo (<0.01ms)."""
    if not _xss or not _x11_dpy or not _x11_root:
        return 0
    try:
        info = XScreenSaverInfo()
        _xss.XScreenSaverQueryInfo(_x11_dpy, _x11_root, ctypes.byref(info))
        return int(info.idle)
    except Exception:
        return 0


class LockMonitor:
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
        self._loop: Optional[GLib.MainLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._subscriptions = []

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.is_screen_locked = self.is_screen_actually_locked()

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BlinkLockMonitor")
        self._thread.start()

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="BlinkLockPoller")
        self._poll_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.quit()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=0.5)

    _cached_bus = None
    _cached_ss_obj = None

    @classmethod
    def _get_dbus_screensaver(cls):
        if cls._cached_ss_obj is not None:
            return cls._cached_ss_obj
        try:
            if cls._cached_bus is None:
                cls._cached_bus = pydbus.SessionBus()
            for service, path in [
                ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver"),
                ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
                ("org.mate.ScreenSaver", "/org/mate/ScreenSaver"),
            ]:
                try:
                    obj = cls._cached_bus.get(service, path)
                    if hasattr(obj, "GetActive"):
                        cls._cached_ss_obj = obj
                        return obj
                except Exception:
                    continue
        except Exception:
            cls._cached_bus = None
        return None

    @classmethod
    def is_screen_actually_locked(cls) -> bool:
        """Authoritatively checks screen lock state across supported environments."""
        # 1. Fast, ultra-low-overhead in-process D-Bus query (<1ms, 0 processes spawned)
        try:
            obj = cls._get_dbus_screensaver()
            if obj is not None and hasattr(obj, "GetActive"):
                return bool(obj.GetActive())
        except Exception:
            cls._cached_ss_obj = None

        # 2. Check via loginctl query
        try:
            res = subprocess.run(
                ["loginctl", "show-session", "self", "-p", "LockedHint"],
                capture_output=True,
                text=True,
                timeout=0.2,
            )
            if "LockedHint=yes" in res.stdout:
                return True
            if "LockedHint=no" in res.stdout:
                return False
        except Exception:
            pass

        # 3. Fallback: cinnamon-screensaver-command -q
        try:
            res = subprocess.run(
                ["cinnamon-screensaver-command", "-q"],
                capture_output=True,
                text=True,
                timeout=0.3,
            )
            if "The screensaver is active" in res.stdout:
                return True
            if "The screensaver is inactive" in res.stdout:
                return False
        except Exception:
            pass

        return False

    def _poll_loop(self) -> None:
        """Background watchdog loop tracking screen lock state and user return activity."""
        was_idle_while_locked = False

        while self._running:
            time.sleep(0.35)
            try:
                locked = self.is_screen_actually_locked()
                if locked != self.is_screen_locked:
                    self._on_screensaver_active_changed(locked)
                    was_idle_while_locked = False

                if self.is_screen_locked:
                    idle_ms = get_system_idle_ms()
                    # Flag idle after 2.5s of inactivity on lockscreen
                    if idle_ms > 2500:
                        was_idle_while_locked = True
                    # When user returns and touches mouse/keyboard after being idle on lockscreen:
                    elif was_idle_while_locked and idle_ms < 800:
                        was_idle_while_locked = False
                        print("[LockMonitor] User activity detected on lockscreen (mouse/keyboard wake).")
                        if self.on_activity:
                            self.on_activity()
            except Exception:
                pass

    def _run_loop(self) -> None:
        try:
            session_bus = pydbus.SessionBus()
        except Exception as e:
            print(f"[LockMonitor] SessionBus connection failed: {e}")
            session_bus = None

        try:
            system_bus = pydbus.SystemBus()
        except Exception as e:
            print(f"[LockMonitor] SystemBus connection failed: {e}")
            system_bus = None

        # 1. Listen for Screensaver ActiveChanged
        if session_bus is not None:
            def handle_active_changed(active: bool):
                self._on_screensaver_active_changed(active)

            try:
                sub_global = session_bus.subscribe(
                    signal="ActiveChanged",
                    signal_fired=lambda *args: handle_active_changed(bool(args[4][0])),
                )
                self._subscriptions.append(sub_global)
            except Exception:
                pass

            for service in [
                ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver"),
                ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
                ("org.mate.ScreenSaver", "/org/mate/ScreenSaver"),
            ]:
                try:
                    sub = session_bus.subscribe(
                        sender=service,
                        signal="ActiveChanged",
                        signal_fired=lambda *args: handle_active_changed(bool(args[4][0])),
                    )
                    self._subscriptions.append(sub)
                except Exception:
                    pass

        # 2. Listen for systemd PrepareForSleep
        if system_bus is not None:
            try:
                def handle_sleep(sleeping: bool):
                    if sleeping:
                        self.is_sleeping = True
                        print("[LockMonitor] System preparing for sleep.")
                        if self.on_sleep:
                            self.on_sleep()
                    else:
                        self.is_sleeping = False
                        print("[LockMonitor] System woke from sleep.")
                        time.sleep(0.3)
                        if self.on_wake:
                            self.on_wake()

                sub_sleep = system_bus.subscribe(
                    sender="org.freedesktop.login1",
                    signal="PrepareForSleep",
                    signal_fired=lambda *args: handle_sleep(bool(args[4][0])),
                )
                self._subscriptions.append(sub_sleep)
            except Exception as e:
                print(f"[LockMonitor] Failed to subscribe to PrepareForSleep: {e}")

        self._loop = GLib.MainLoop()
        try:
            self._loop.run()
        except Exception as e:
            print(f"[LockMonitor] MainLoop terminated: {e}")

    def _on_screensaver_active_changed(self, active: bool) -> None:
        if self.is_screen_locked == active:
            return
        self.is_screen_locked = active
        if active:
            print("[LockMonitor] Screen locked.")
            if self.on_locked:
                self.on_locked()
        else:
            print("[LockMonitor] Screen unlocked.")
            if self.on_unlocked:
                self.on_unlocked()

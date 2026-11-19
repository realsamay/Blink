# CLAUDE.md — Blink Co-Pilot Guide: Windows & Linux Division

Welcome, Claude Code! This project is **Blink** (`https://blink-faceunlock.vercel.app`), an Apple Face ID experience engineered for PC.

## 🤝 Division of Labor
- **Claude Code CLI**: Owns the **WINDOWS** implementation of Blink (UI, Win32 APIs, SendInput, Lock Monitor, Windows packaging, Inno Setup, `.exe` builds).
- **Antigravity**: Owns the **LINUX** implementation of Blink (GTK3, X11 XShape, PAM module, systemd daemon, `.deb` packaging, web/landing page).
- **Shared Core**: Neural vision pipeline (YuNet + ArcFace 512-D), AES-256 encrypted biometric store, core config.

---

## 🐙 GitHub Repository Access

You have full access to GitHub CLI (`gh`) and Git commands on this machine:
- **Repository**: `git@github.com:realsamay/Blink.git`
- **HTTPS URL**: `https://github.com/realsamay/Blink`
- **Authenticated Account**: `realsamay` (pre-authenticated with active token in keyring)
- **Primary Branch**: `main`
- **Useful Commands**:
  ```bash
  cd /home/samayy/blink
  git status
  git pull origin main
  # Create a feature branch for your Windows work:
  git checkout -b feat/windows-enhancements
  # Push changes or open PR:
  git push -u origin feat/windows-enhancements
  gh pr create --title "feat(windows): ..." --body "..."
  ```

---

## 🪟 Claude's Territory: Windows Architecture & Files

All files in `/home/samayy/blink` related to Windows:

### 1. UI & Overlays (Windows)
- `blink/ui/notch_overlay_win.py`:
  - The Windows Dynamic Island squircle overlay.
  - Built with Tkinter / GDI layered transparent window (`WS_EX_LAYERED`, `WS_EX_TOOLWINDOW`).
  - Renders 148×148 squircle with hardware rounded corners and smooth 60 FPS animation playback.
- `blink/ui/settings_window_win.py`:
  - Windows Fluent-style settings GUI and dashboard.
- `blink/ui/shield_window.py` (`_launch_windows`):
  - **Blink Lock** for Windows: Modal Face ID biometric shield for applications (Chrome, Discord, Terminal) and folders with fallback passcode verification.

### 2. Windows System Integrations
- `blink/system/keystroke_injector_win.py`:
  - Virtual keystroke synthesis on Windows using the Win32 `SendInput` API (`ctypes.windll.user32.SendInput`) to inject passwords directly into Windows logon / UAC prompts.
- `blink/system/lock_monitor_win.py`:
  - Windows session state monitor using `WTSRegisterSessionNotification` (detects `WTS_SESSION_LOCK` and `WTS_SESSION_UNLOCK` to wake the camera).
- `blink/system/platform_info.py`:
  - Platform detection logic (`IS_WINDOWS`, `get_platform_info`).

### 3. Build, Packaging & Services
- `scripts/build_windows_exe.py`:
  - Standalone `.exe` packaging script using PyInstaller.
- `packaging/windows/`:
  - Inno Setup compiler scripts (`blink_setup.iss`, etc.) to produce the Windows installer executable.
- `service/install-windows.ps1` & `service/install-windows.bat`:
  - Windows Task Scheduler / Service autostart installer.
- `requirements-windows.txt`:
  - Windows dependency manifest (`pywin32`, `opencv-python`, `Pillow`, `numpy`, `customtkinter`).

### 4. Tests
- `tests/test_windows_support.py`:
  - Unit tests verifying mock Windows imports, SendInput keystroke payloads, and Win32 geometry.

---

## 🐧 Antigravity's Territory: Linux Architecture

- `blink/ui/notch_overlay.py` (Linux GTK3 + PyCairo + X11 XShape overlay)
- `blink/ui/settings_window.py` (Linux GTK3 settings window)
- `blink/system/pam_handler.py` (Linux PAM authentication & `/dev/uinput` kernel injection)
- Debian packaging (`build/deb/`, `.deb` installers, `install.sh`)
- Web / Landing Page repository (`/home/samayy/Codes/Web designs/Blink/`)
- AI Launch Video assets (`/home/samayy/Launch Vid - Blink/`)

---

## ⚡ Development & Testing Commands

Python Virtualenv: `/home/samayy/blink/.venv`

```bash
# Test Windows modules (with simulated/mock environment on Linux):
/home/samayy/blink/.venv/bin/python /home/samayy/blink/tests/test_windows_support.py

# Test camera manager:
/home/samayy/blink/.venv/bin/python -c "from blink.vision.camera import CameraManager; print('Camera OK')"

# Test ArcFace 512-D embedder:
/home/samayy/blink/.venv/bin/python -c "from blink.vision.embedder import ArcFaceEmbedder; print('Embedder OK')"

# Test Shield Window entrypoint:
/home/samayy/blink/.venv/bin/python -m blink.ui.shield_window
```

---

## 🎯 Current Windows Priorities for Claude
1. **Windows Hello / Lock Screen Integration**: Review `keystroke_injector_win.py` and `lock_monitor_win.py` to ensure smooth unlock on Windows 10/11 lockscreens.
2. **Blink Lock for Windows**: Polish `_launch_windows` in `shield_window.py` so that right-clicking files or launching protected apps triggers the native Face ID modal.
3. **Packaging**: Validate `scripts/build_windows_exe.py` and `packaging/windows/` so that the Windows `.exe` installer bundles smoothly with all ONNX vision models.

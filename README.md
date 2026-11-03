# Blink

<p align="center">
  <img src="blink/resources/final.png" alt="Blink Logo" width="128" />
  <br>
  <b>Native Face ID & Biometric Screen Unlock for Linux & Windows</b>
  <br>
  <i>Fast, secure, on-device facial authentication for Linux Mint, Ubuntu, Debian, Fedora, Arch, and Windows 10/11</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Platforms-Linux%20%7C%20Windows-0078D6?style=flat" alt="Platforms" />
  <img src="https://img.shields.io/badge/Privacy-100%25%20On--Device-30D158?style=flat" alt="100% On-Device" />
  <img src="https://img.shields.io/badge/Latency-%3C%2025ms-FF9F0A?style=flat" alt="Latency" />
  <img src="https://img.shields.io/badge/Tests-19%20Passing-30D158?style=flat" alt="Tests" />
  <img src="https://img.shields.io/badge/Release-v2.1.0-blue?style=flat" alt="Release v2.1.0" />
  <img src="https://img.shields.io/badge/License-AGPLv3-0568FD?style=flat" alt="License" />
</p>

<p align="center">
  <a href="https://github.com/realsamay/Blink/releases/download/v2.1.0/blink_2.1.0_amd64.deb">
    <img src="https://img.shields.io/badge/Download%20.deb-Linux%20x64-E95420?style=for-the-badge&logo=ubuntu&logoColor=white" alt="Download .deb for Linux" />
  </a>
  &nbsp;
  <a href="https://github.com/realsamay/Blink/releases/download/v2.1.0/Blink-Setup-x64.exe">
    <img src="https://img.shields.io/badge/Download%20.exe-Windows%2010%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Download .exe for Windows" />
  </a>
</p>

---

## Overview

**Blink** brings Apple-style biometric Face ID to Linux and Windows desktops. It unlocks your system in milliseconds when you wake your PC, open your laptop lid, or touch your keyboard/mouse at the lockscreen — completely hands-free, offline, and private.

No external cloud servers. No telemetry. Your facial biometric vectors and unlock credentials never leave your local hardware.

---

## Highlights & Features

- ⚡ **100% On-Device & Private**: Neural facial embeddings (ArcFace 512-D) and YuNet deep CNN detection run completely offline on CPU with AVX2/FMA hardware acceleration.
- 🎬 **Minimalist Rounded Popout Card (v2.0)**: A sleek, 100×100 px obsidian black squircle with hardware-carved 22px rounded corners that pops out directly under your webcam notch. Displays pure 60 FPS face scan and unlock blossom animations with zero text clutter.
- ⚡ **Instantaneous Keystroke Unlock**:
  - **Linux**: Pre-warmed persistent kernel `/dev/uinput` virtual keyboard typing credentials in **< 25ms**.
  - **Windows**: Win32 `SendInput` batch injection using `KEYEVENTF_UNICODE` in **< 5ms** (100% layout-independent across QWERTY, AZERTY, QWERTZ).
- 👁️ **Multi-Cue Anti-Spoofing Liveness**: Comprehensive heuristics detecting screen glare reflections, device framing bezels, 3D planar homography, depth parallax, and blink dynamics to reject phone photos, tablets, and video spoof attacks.
- 🛡️ **AES-256-GCM Hardware-Bound Vault**: Encrypted credentials and facial embeddings at rest using PBKDF2 (100,000 rounds) key derivation bound to the hardware Machine ID (`/etc/machine-id` on Linux, `MachineGuid` on Windows), with automatic RAM zeroing after injection.
- 🪟 **Focus-Safe Windows Architecture**: Created with `WS_EX_NOACTIVATE | WS_EX_TOPMOST` to guarantee the dynamic overlay card never steals keyboard focus from the Windows 10/11 lockscreen password field.
- 🧪 **Face Lab & Enrollment Wizard**: Guided 5-pose face enrollment wizard (Center, Left, Right, Up, Down) with live feedback, visual distance gauges, and real-time similarity metrics.

---

## The 8-Step Architecture Pipeline

```text
[ Lock Event / User Activity ]
               │
               ▼
[ 1. Event Catching ] ──► Linux: D-Bus (ScreenSaver/login1) + libXss.so.1 idle tracking
                      ──► Windows: wtsapi32 (WTSRegisterSessionNotification) + GetLastInputInfo
               │
               ▼
[ 2. Camera Worker ] ───► Dedicated async capture thread (V4L2 on Linux, DirectShow on Windows)
               │
               ▼
[ 3. YuNet CNN Face Detect ] ──► Locates face bounding box and 5 facial landmarks in ~5ms
               │
               ▼
[ 4. Canonical Alignment ] ───► Affine similarity warp onto 112×112 standard template
               │
               ▼
[ 5. ArcFace 512-D Embedding ] ─► MobileFaceNet generates unit-norm vector; cosine similarity (≥ 0.60)
               │
               ▼
[ 6. Anti-Spoofing Guard ] ───► 5-cue liveness filters (glare, bezels, homography, blink dynamics)
               │
               ▼
[ 7. Hardware Squircle Card ] ─► 100×100 px card pops down at 60 FPS (Cairo XShape on Linux, Win32 on Windows)
               │
               ▼
[ 8. Instant Credential Unlock ] ─► /dev/uinput (Linux) or SendInput (Windows) types password in <25ms
```

---

## Installation & Setup

### 🐧 Linux (Linux Mint, Ubuntu, Debian, Pop!_OS)

#### Option A: 1-Click Debian Package (.deb) — Recommended
Download the latest pre-compiled binary package:
```bash
wget https://github.com/realsamay/Blink/releases/download/v2.1.0/blink_2.1.0_amd64.deb
sudo apt install ./blink_2.1.0_amd64.deb
```
*(Or double-click `blink_2.1.0_amd64.deb` in your file manager to install via your distro's Software Center).*

#### Option B: Build from Source
```bash
git clone https://github.com/realsamay/Blink.git ~/.local/share/blink
cd ~/.local/share/blink
./install.sh
```

#### Configure Password & Enroll Face
```bash
blink settings
```
1. Open the **Password** tab and enter your Linux desktop password (saved encrypted with AES-256-GCM).
2. Open the **Your Face** tab, click **Enroll New Face**, and follow the 5-pose scan guide.
3. Check the **Face Lab** tab for live confidence scores and real-time landmark tracking.

#### Enable Background Service
```bash
# Enable systemd user service (recommended)
systemctl --user enable --now blink.service

# Or configure desktop autostart
blink autostart
```

---

### 🪟 Windows (Windows 10 & Windows 11 64-bit)

#### Option A: 1-Click Setup Wizard (.exe) — Recommended
1. Download [**`Blink-Setup-x64.exe`**](https://github.com/realsamay/Blink/releases/download/v2.1.0/Blink-Setup-x64.exe) from GitHub Releases.
2. Double-click the installer and follow the modern setup wizard.
3. The wizard bundles all neural models and automatically configures background daemon startup on user logon.

#### Option B: Terminal Setup via Git
Open PowerShell or Command Prompt as Administrator:
```powershell
git clone https://github.com/realsamay/Blink.git C:\Users\$env:USERNAME\Blink
cd C:\Users\$env:USERNAME\Blink
.\service\install-windows.bat
```
*(Or right-click `service\install-windows.bat` in File Explorer and click **Run as administrator**).*

The installer automatically:
- Sets up a dedicated Python virtual environment (`.venv`).
- Installs all dependencies (`opencv-python`, `numpy`, `cryptography`, `pillow`, `onnxruntime`).
- Creates desktop and Start Menu shortcuts.
- Configures background unlock daemon autostart on user logon in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.

#### 2. Configure Password & Enroll Face
Launch **Blink** from your Start Menu or Desktop:
1. Enter your Windows login password or PIN in the **Password** tab.
2. Click **➕ Enroll New Face** and look directly at your webcam to capture your profile.
3. Enable **Face ID Unlock** in the **General** tab and click **Save Settings**.

---

## CLI Reference

```text
blink [command]

Commands:
  status            Display hardware, camera, encryption, and enrolled face status
  settings          Open GUI Settings, Face Enrollment Wizard, and Face Lab
  daemon            Run the background unlock daemon
  autostart         Configure Blink to launch automatically on user login
  install-desktop   Install Blink into system application menu and desktop
  test-scan         Preview the Face ID squircle card scan & blossom animation on desktop
```

### Checking System Diagnostics
```bash
blink status
```
```text
==================================================
              BLINK FOR LINUX / WINDOWS
==================================================
Face Unlock Enabled:    True
Similarity Threshold:   0.60
Detection Timeout:      5s
Liveness Strictness:    LIGHT (Enabled: True)
Active Triggers:        on_wake, on_lock, on_space, on_activity
Camera Device Index:    /dev/video1 (or DirectShow Index 0)
Keystroke Injection:    Available (/dev/uinput or SendInput API)
Password Stored:        YES (AES-256-GCM)
Enrolled Identities:    1 (5 poses, Active)
==================================================
```

---

## System Requirements

| Specification | Linux (Available Now) | Windows (Available Now) |
| :--- | :--- | :--- |
| **Operating System** | Linux Mint, Ubuntu, Debian, Fedora, Arch | Windows 10 & 11 (64-bit / ARM64) |
| **Desktop Environment** | Cinnamon, GNOME, KDE Plasma, XFCE, MATE | Native Windows Shell (Winlogon / LogonUI) |
| **Processor** | Dual-core 2.0 GHz+ (Intel / AMD CPU with AVX2) | Dual-core 2.0 GHz+ (Intel / AMD CPU) |
| **Webcam** | Built-in or USB webcam (`/dev/video0`) | Built-in or USB DirectShow webcam |
| **Memory Usage** | ~140 – 180 MB active RAM | ~150 MB active RAM |
| **Unlock Latency** | **< 25 ms** via `/dev/uinput` | **< 5 ms** via Win32 `SendInput` batch |
| **Privacy** | 100% On-Device · Zero Cloud Latency | 100% On-Device · Zero Cloud Latency |

---

## Running the Automated Test Suite

Blink includes an end-to-end unit and integration test suite:

```bash
python -m unittest discover tests
```

Validates:
- **Crypto**: AES-256-GCM encryption, decryption, PBKDF2 salt derivation, and secure memory buffer zeroing.
- **Vision**: YuNet CNN detector initialization, 5-point landmark similarity warping, and 112×112 canonical output.
- **ArcFace**: 512-dimensional normalized unit vector generation and cosine similarity matching ($\ge 0.60$).
- **Liveness**: Multi-cue screen glare detection, bezel framing rejection, homography, and blink dynamics.
- **Input ABI**: Linux `/dev/uinput` mapping and Windows Win32 `INPUT` / `KEYBDINPUT` 64-bit C ABI alignment.
- **Animations**: 60 FPS surface cache validation across idle, unlock blossom, and failure animations.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

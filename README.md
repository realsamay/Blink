# Blink

<p align="center">
  <img src="blink/resources/appicon.png" alt="Blink Logo" width="128" />
  <br>
  <b>Native Face ID & Biometric Screen Unlock for Linux</b>
  <br>
  <i>Fast, secure, on-device facial authentication for Linux Mint, Ubuntu, Debian, Fedora, Arch, and all major desktop environments</i>
</p>

---

## Overview

**Blink** brings seamless, biometric Face ID to Linux desktops. It unlocks your system in the blink of an eye when you wake your PC, open your laptop lid, or touch your keyboard/mouse at the lockscreen — completely hands-free and private.

### Features

- ⚡ **100% On-Device & Private**: Neural facial embeddings (ArcFace 512-D) and detection run completely offline on your CPU with AVX2/FMA hardware acceleration.
- 🎯 **Lockscreen-Only Dynamic Card**: Elegant lockscreen card overlay that renders during authentication and strictly remains off your desktop while unlocked.
- ⚡ **Instant Idle-Wake Detection**: Tracks lockscreen interaction via ultra-fast hardware idle monitoring (`XScreenSaverQueryInfo`) and D-Bus signals so returning to an auto-locked PC immediately triggers Face ID scanning.
- ⌨️ **Kernel-Level Keystroke Injection**: Seamlessly types your unlock credentials directly into the screensaver via `/dev/uinput` virtual keyboard subsystem (compatible with both X11 and Wayland).
- 🛡️ **Military-Grade AES-256-GCM Encryption**: User passwords and facial embedding vectors are encrypted at rest with PBKDF2 (100,000 rounds) key derivation, with automatic memory zeroing after injection.
- 👁️ **Multi-Cue Anti-Spoofing Liveness**: Comprehensive anti-spoofing engine detecting screen glare, bezel framing, planar homography, and blink dynamics to prevent photo or video attacks.
- 🧪 **Face Lab & Enrollment Wizard**: Guided 5-pose face capture wizard with live feedback, visual distance gauges, and real-time similarity metrics.

---

## Quick Start

### 1. Check System Status
Verify camera availability, `/dev/uinput` permissions, and encryption status:
```bash
blink status
```

### 2. Launch Settings & Enroll Your Profile
Open the graphical settings and enrollment interface:
```bash
blink settings
```

1. **Password Tab**: Enter and securely store your desktop unlock password.
2. **Your Face Tab**: Click **Enroll New Face** and follow the guided 5-pose face capture (Center, Left, Right, Up, Down).
3. **Face Lab Tab**: Test real-time camera inference, landmark tracking, and match confidence.

### 3. Enable Background Service
Start the background unlock daemon:
```bash
systemctl --user enable --now blink.service
```

Or configure it via user autostart:
```bash
blink autostart
```

---

## CLI Reference

```text
blink [command]

Commands:
  status        Display hardware, camera, encryption, and enrolled face status
  settings      Open GUI Settings, Face Enrollment Wizard, and Face Lab
  daemon        Run the background unlock daemon
  autostart     Configure Blink to start on login (~/.config/autostart/blink.desktop)
  test-scan     Preview the Face ID card and scan animation on desktop
```

---

## System Requirements

- **Linux Distribution**: Linux Mint, Ubuntu, Debian, Fedora, Arch Linux, or any standard Linux distribution.
- **Desktop Environments**: Cinnamon, GNOME, KDE Plasma, XFCE, MATE, and compatible screensavers.
- **Camera**: Any standard USB or built-in webcam (`/dev/video0`).
- **Dependencies**: Python 3.10+, OpenCV, NumPy, PyGObject (GTK 3), evdev.

---

## Running Tests

Run the end-to-end self-test suite:
```bash
python tests/test_suite.py
```

Validates:
- AES-256-GCM encryption, decryption, and secure zeroing
- Neural face detection and canonical 112×112 similarity alignment
- ArcFace 512-D embedding generation and cosine similarity matching
- Liveness detection and screen glare rejection
- Kernel virtual keyboard (`/dev/uinput`) keycode mapping

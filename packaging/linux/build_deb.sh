#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build/deb"
DIST_DIR="${PROJECT_ROOT}/dist"
VERSION="2.1.0"
PKG_NAME="blink_${VERSION}_amd64"
STAGE_DIR="${BUILD_DIR}/${PKG_NAME}"

echo "=== Building Blink ${VERSION} Debian/Ubuntu (.deb) Package ==="

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}/DEBIAN"
mkdir -p "${STAGE_DIR}/usr/bin"
mkdir -p "${STAGE_DIR}/usr/lib/blink"
mkdir -p "${STAGE_DIR}/usr/share/applications"
mkdir -p "${STAGE_DIR}/usr/lib/systemd/user"
mkdir -p "${DIST_DIR}"

# 1. Copy Blink core application code
echo "[*] Copying core application code..."
cp -r "${PROJECT_ROOT}/blink" "${STAGE_DIR}/usr/lib/blink/"
find "${STAGE_DIR}/usr/lib/blink" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 2. Copy production neural models
echo "[*] Copying neural models (YuNet + ArcFace MobileFaceNet)..."
mkdir -p "${STAGE_DIR}/usr/lib/blink/models"
cp "${PROJECT_ROOT}/models/face_detection_yunet.onnx" "${STAGE_DIR}/usr/lib/blink/models/"
cp "${PROJECT_ROOT}/models/w600k_mbf.onnx" "${STAGE_DIR}/usr/lib/blink/models/"

# 3. Create /usr/bin/blink entrypoint launcher
echo "[*] Creating /usr/bin/blink executable wrapper..."
cat << 'EOF' > "${STAGE_DIR}/usr/bin/blink"
#!/usr/bin/env bash
export PYTHONUNBUFFERED=1
export PYTHONPATH="/usr/lib/blink:${PYTHONPATH}"
exec /usr/bin/python3 -u /usr/lib/blink/blink/main.py "$@"
EOF
chmod 0755 "${STAGE_DIR}/usr/bin/blink"

# 4. Create desktop application launcher
echo "[*] Creating desktop application menu entry..."
cat << 'EOF' > "${STAGE_DIR}/usr/share/applications/blink.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Blink
GenericName=Face ID
Comment=Native Face ID Biometric Authentication for Linux
Exec=/usr/bin/blink settings
Icon=blink
Terminal=false
StartupNotify=true
StartupWMClass=blink
Categories=Utility;Security;Settings;System;GTK;
Keywords=face;id;unlock;biometric;security;blink;camera;
Actions=DaemonRestart;LockscreenTest;

[Desktop Action DaemonRestart]
Name=Restart Face ID Daemon
Exec=systemctl --user restart blink.service

[Desktop Action LockscreenTest]
Name=Test Dynamic Island
Exec=/usr/bin/blink test-scan
EOF
chmod 0644 "${STAGE_DIR}/usr/share/applications/blink.desktop"

# 5. Create systemd user service
echo "[*] Creating systemd user service..."
cat << 'EOF' > "${STAGE_DIR}/usr/lib/systemd/user/blink.service"
[Unit]
Description=Blink Face Unlock Daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/blink daemon
Restart=always
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
chmod 0644 "${STAGE_DIR}/usr/lib/systemd/user/blink.service"

# 6. Install scalable and multi-res icons
echo "[*] Installing icons..."
ICON_SRC="${PROJECT_ROOT}/blink/resources/final.png"
PY_BIN="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -f "${PY_BIN}" ]; then
    PY_BIN="python3"
fi

if [ -f "${ICON_SRC}" ]; then
    "${PY_BIN}" -c "
import cv2, os
src = '${ICON_SRC}'
img = cv2.imread(src, cv2.IMREAD_UNCHANGED)
for sz in [16, 24, 32, 48, 64, 128, 256, 512]:
    d = '${STAGE_DIR}/usr/share/icons/hicolor/' + f'{sz}x{sz}/apps'
    os.makedirs(d, exist_ok=True)
    res = cv2.resize(img, (sz, sz), interpolation=cv2.INTER_AREA)
    cv2.imwrite(f'{d}/blink.png', res)
"
fi

SVG_SRC="${PROJECT_ROOT}/blink/resources/blink-logo.svg"
if [ -f "${SVG_SRC}" ]; then
    mkdir -p "${STAGE_DIR}/usr/share/icons/hicolor/scalable/apps"
    cp "${SVG_SRC}" "${STAGE_DIR}/usr/share/icons/hicolor/scalable/apps/blink.svg"
fi

# 7. Create DEBIAN/control file
echo "[*] Writing DEBIAN/control..."
INSTALLED_SIZE=$(du -sk "${STAGE_DIR}" | cut -f1)

cat << EOF > "${STAGE_DIR}/DEBIAN/control"
Package: blink
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: ${INSTALLED_SIZE}
Maintainer: Samay <realsamay@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, python3-opencv, python3-numpy, python3-cryptography, python3-evdev, libxss1, libx11-6
Recommends: python3-pip
Homepage: https://github.com/realsamay/Blink
Description: Native Face ID & Biometric Screen Unlock for Linux
 Blink brings Apple-style biometric Face ID to Linux desktops.
 It unlocks your system in milliseconds when you wake your PC, open
 your laptop lid, or touch your keyboard/mouse at the lockscreen.
 Completely on-device, hands-free, and private with zero cloud telemetry.
EOF

# 8. Create DEBIAN/postinst script
echo "[*] Writing DEBIAN/postinst..."
cat << 'EOF' > "${STAGE_DIR}/DEBIAN/postinst"
#!/bin/sh
set -e

# Setup persistent uinput udev rule so non-root users can inject keystrokes
RULE_FILE="/etc/udev/rules.d/99-blink-uinput.rules"
if [ ! -f "$RULE_FILE" ]; then
    echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' > "$RULE_FILE"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
fi

# Update desktop and icon databases
if which update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if which gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor || true
fi

echo "=========================================================="
echo " Blink Face ID v2.0.0 installed successfully!"
echo " 1. Run 'blink settings' to enroll your face & password."
echo " 2. Run 'systemctl --user enable --now blink.service'"
echo "=========================================================="
exit 0
EOF
chmod 0755 "${STAGE_DIR}/DEBIAN/postinst"

# 9. Build package using dpkg-deb
echo "[*] Packaging .deb archive..."
dpkg-deb --build --root-owner-group "${STAGE_DIR}" "${DIST_DIR}/${PKG_NAME}.deb"

echo "[✓] Successfully built: ${DIST_DIR}/${PKG_NAME}.deb"
ls -lh "${DIST_DIR}/${PKG_NAME}.deb"

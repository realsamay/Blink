# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPECPATH), ".."))

added_files = [
    (os.path.join(PROJECT_ROOT, "models", "face_detection_yunet.onnx"), "models"),
    (os.path.join(PROJECT_ROOT, "models", "w600k_mbf.onnx"), "models"),
    (os.path.join(PROJECT_ROOT, "models", "face_detection_yunet.onnx"), "blink/models"),
    (os.path.join(PROJECT_ROOT, "models", "w600k_mbf.onnx"), "blink/models"),
    (os.path.join(PROJECT_ROOT, "blink", "resources"), "blink/resources"),
    (os.path.join(PROJECT_ROOT, "blink", "resources"), "resources"),
]

hidden_imports = [
    "cv2",
    "numpy",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.primitives.hashes",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "ctypes",
    "ctypes.wintypes",
    "onnxruntime",
    "urllib.request",
    "blink.core.paths",
    "blink.ui.settings_window_win",
    "blink.ui.notch_overlay_win",
    "blink.system.lock_monitor_win",
    "blink.system.keystroke_injector_win",
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, "blink", "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="blink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(SPECPATH, "version_info.txt"),
    icon=os.path.join(PROJECT_ROOT, "blink", "resources", "appicon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Blink",
)

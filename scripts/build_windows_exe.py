import os
import sys
import zipfile
import shutil
from pathlib import Path

PROJECT_ROOT = Path("/home/samayy/blink")
DIST_DIR = PROJECT_ROOT / "dist"
WINDOWS_DOWNLOADS = Path("/media/samayy/C4C6D337C6D32906/Users/USER/Downloads")

DIST_DIR.mkdir(parents=True, exist_ok=True)

# 1. Locate w64.exe and t64.exe in venv
w64_path = Path("/home/samayy/blink/.venv/lib/python3.12/site-packages/pip/_vendor/distlib/w64.exe")
t64_path = Path("/home/samayy/blink/.venv/lib/python3.12/site-packages/pip/_vendor/distlib/t64.exe")

if not w64_path.exists():
    raise FileNotFoundError(f"Launcher not found: {w64_path}")

print(f"[*] Found Windows PE Launcher: {w64_path} ({w64_path.stat().st_size} bytes)")

# 2. Build Python ZipApp containing blink package, entrypoint, and models
zip_payload_path = DIST_DIR / "blink_payload.zip"
with zipfile.ZipFile(zip_payload_path, "w", zipfile.ZIP_DEFLATED) as zf:
    # Add __main__.py
    main_code = """
import sys
import os
from pathlib import Path

# Set up paths
base_dir = Path(__file__).resolve().parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from blink.main import main
if __name__ == "__main__":
    main()
"""
    zf.writestr("__main__.py", main_code)

    # Add blink package
    blink_dir = PROJECT_ROOT / "blink"
    for root, dirs, files in os.walk(blink_dir):
        if "__pycache__" in root:
            continue
        for file in files:
            fp = Path(root) / file
            rel = fp.relative_to(PROJECT_ROOT)
            zf.write(fp, arcname=str(rel))

    # Add models
    for model_name in ["face_detection_yunet.onnx", "w600k_mbf.onnx"]:
        mp = PROJECT_ROOT / "models" / model_name
        if mp.exists():
            zf.write(mp, arcname=f"models/{model_name}")

print(f"[*] Built zipapp payload: {zip_payload_path} ({zip_payload_path.stat().st_size} bytes)")

# 3. Create standalone Blink.exe
blink_exe = DIST_DIR / "Blink.exe"
with open(blink_exe, "wb") as f_out:
    # Shebang for Windows Python Launcher
    shebang = b"#!pythonw.exe\r\n"
    with open(w64_path, "rb") as f_launcher:
        f_out.write(f_launcher.read())
    f_out.write(shebang)
    with open(zip_payload_path, "rb") as f_zip:
        f_out.write(f_zip.read())

print(f"[✓] Successfully built: {blink_exe} ({blink_exe.stat().st_size} bytes)")

# 4. Create Blink-Setup-x64.exe (Installer launcher)
setup_code = """
import sys
import os
import subprocess
from pathlib import Path

def run_setup():
    app_dir = Path(__file__).resolve().parent
    ps1_script = app_dir / "service" / "install-windows.ps1"
    if not ps1_script.exists():
        ps1_script = Path(os.environ.get("USERPROFILE", "C:\\\\Users\\\\USER")) / "Blink" / "service" / "install-windows.ps1"
    
    print("Launching Blink Face ID Setup for Windows...")
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_script)]
    subprocess.run(cmd)

if __name__ == "__main__":
    run_setup()
"""
setup_zip_path = DIST_DIR / "setup_payload.zip"
with zipfile.ZipFile(setup_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("__main__.py", setup_code)
    # Include service scripts
    for s_file in ["install-windows.bat", "install-windows.ps1"]:
        sp = PROJECT_ROOT / "service" / s_file
        if sp.exists():
            zf.write(sp, arcname=f"service/{s_file}")

setup_exe = DIST_DIR / "Blink-Setup-x64.exe"
with open(setup_exe, "wb") as f_out:
    shebang = b"#!python.exe\r\n"
    with open(t64_path, "rb") as f_launcher:
        f_out.write(f_launcher.read())
    f_out.write(shebang)
    with open(setup_zip_path, "rb") as f_zip:
        f_out.write(f_zip.read())

print(f"[✓] Successfully built: {setup_exe} ({setup_exe.stat().st_size} bytes)")

# 5. Create full portable zip package for Windows
portable_zip = DIST_DIR / "Blink-v2.1.0-Windows.zip"
with zipfile.ZipFile(portable_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(blink_exe, arcname="Blink/Blink.exe")
    zf.write(setup_exe, arcname="Blink/Blink-Setup-x64.exe")
    for root, dirs, files in os.walk(PROJECT_ROOT / "blink"):
        if "__pycache__" in root:
            continue
        for f in files:
            p = Path(root) / f
            zf.write(p, arcname=f"Blink/blink/{p.relative_to(PROJECT_ROOT / 'blink')}")
    for root, dirs, files in os.walk(PROJECT_ROOT / "models"):
        for f in files:
            p = Path(root) / f
            zf.write(p, arcname=f"Blink/models/{f}")
    for root, dirs, files in os.walk(PROJECT_ROOT / "service"):
        for f in files:
            p = Path(root) / f
            zf.write(p, arcname=f"Blink/service/{f}")
    if (PROJECT_ROOT / "requirements-windows.txt").exists():
        zf.write(PROJECT_ROOT / "requirements-windows.txt", arcname="Blink/requirements-windows.txt")

print(f"[✓] Successfully created portable zip: {portable_zip} ({portable_zip.stat().st_size} bytes)")

# 6. Save to Windows Downloads directory (/media/samayy/C4C6D337C6D32906/Users/USER/Downloads)
if WINDOWS_DOWNLOADS.exists():
    shutil.copyfile(blink_exe, WINDOWS_DOWNLOADS / "Blink.exe")
    shutil.copyfile(setup_exe, WINDOWS_DOWNLOADS / "Blink-Setup-x64.exe")
    shutil.copyfile(portable_zip, WINDOWS_DOWNLOADS / "Blink-v2.1.0-Windows.zip")
    print(f"[✓] Copied to Windows Downloads folder: {WINDOWS_DOWNLOADS}")
    print(f"    - {WINDOWS_DOWNLOADS / 'Blink.exe'}")
    print(f"    - {WINDOWS_DOWNLOADS / 'Blink-Setup-x64.exe'}")
    print(f"    - {WINDOWS_DOWNLOADS / 'Blink-v2.1.0-Windows.zip'}")
else:
    print(f"[!] Warning: Windows Downloads folder not accessible at {WINDOWS_DOWNLOADS}")

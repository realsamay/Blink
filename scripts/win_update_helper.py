import os
import sys
import time
import shutil
import subprocess

def replace_and_restart(new_exe, old_exe):
    # Wait for the old process to fully terminate
    time.sleep(2)

    try:
        # Replace the old executable
        shutil.move(new_exe, old_exe)

        # Restart the application
        subprocess.Popen([old_exe])
        print("Update applied. Blink restarted.")
    except Exception as e:
        print(f"Failed to apply update: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(1)

    new_exe = sys.argv[1]
    old_exe = sys.argv[2]

    replace_and_restart(new_exe, old_exe)

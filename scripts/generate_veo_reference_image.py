import cv2
from pathlib import Path

# Extract high quality keyframe from blink_face_recognized_lockscreen_demo.mp4
DOWNLOADS_DIR = Path("/home/samayy/Downloads")
demo_path = DOWNLOADS_DIR / "blink_face_recognized_lockscreen_demo.mp4"

cap = cv2.VideoCapture(str(demo_path))
# Grab frame at 0.0s (clean lockscreen), frame at 1.5s (scanning), frame at 3.0s (face recognized checkmark)
frames = {}
for i in range(250):
    ret, f = cap.read()
    if not ret:
        break
    if i == 5:
        frames["clean"] = f
    elif i == 90:
        frames["scanning"] = f
    elif i == 170:
        frames["recognized"] = f
cap.release()

if "clean" in frames:
    cv2.imwrite(str(DOWNLOADS_DIR / "lockscreen_macbook_reference_clean.png"), frames["clean"])
    print("Saved: lockscreen_macbook_reference_clean.png")

if "recognized" in frames:
    cv2.imwrite(str(DOWNLOADS_DIR / "lockscreen_macbook_reference_recognized.png"), frames["recognized"])
    print("Saved: lockscreen_macbook_reference_recognized.png")

if "scanning" in frames:
    cv2.imwrite(str(DOWNLOADS_DIR / "lockscreen_macbook_reference_scanning.png"), frames["scanning"])
    print("Saved: lockscreen_macbook_reference_scanning.png")

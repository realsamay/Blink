import math
import os
import shutil
from pathlib import Path
import cv2
import numpy as np
import cairo

DOWNLOADS_DIR = Path("/home/samayy/Downloads")
RESOURCES_DIR = Path("/home/samayy/blink/blink/resources")

def load_frames(video_name):
    cap = cv2.VideoCapture(str(RESOURCES_DIR / video_name))
    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
    cap.release()
    return frames

print("Loading raw video assets...")
idle_frames = load_frames("idleanimation.mp4")
unlock_frames = load_frames("unlockanimation.mp4")
print(f"Loaded {len(idle_frames)} idle frames, {len(unlock_frames)} unlock frames.")

# 1. Copy raw unlock animation
raw_target = DOWNLOADS_DIR / "blink_face_recognized_raw_432x432.mp4"
shutil.copyfile(RESOURCES_DIR / "unlockanimation.mp4", raw_target)
print(f"Saved: {raw_target}")

# Function to draw squircle card on a cairo surface
def render_card_surface(video_frame_bgr, card_w=148, card_h=148, radius=28, video_size=116, v_radius=20):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, card_w, card_h)
    cr = cairo.Context(surf)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)

    # 1. Squircle background
    cr.new_sub_path()
    cr.arc(card_w - radius, radius, radius, -math.pi / 2, 0)
    cr.arc(card_w - radius, card_h - radius, radius, 0, math.pi / 2)
    cr.arc(radius, card_h - radius, radius, math.pi / 2, math.pi)
    cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()

    # Dark obsidian glass background
    cr.set_source_rgba(0.015, 0.015, 0.025, 0.98)
    cr.fill_preserve()
    cr.clip()

    # 2. Centered video surface
    vx = (card_w - video_size) / 2.0
    vy = (card_h - video_size) / 2.0

    # Resize video frame
    resized_v = cv2.resize(video_frame_bgr, (video_size, video_size), interpolation=cv2.INTER_AREA)
    bgra_v = cv2.cvtColor(resized_v, cv2.COLOR_BGR2BGRA)
    v_surf = cairo.ImageSurface.create_for_data(bgra_v, cairo.FORMAT_ARGB32, video_size, video_size, video_size * 4)

    cr.save()
    cr.new_sub_path()
    cr.arc(vx + video_size - v_radius, vy + v_radius, v_radius, -math.pi / 2, 0)
    cr.arc(vx + video_size - v_radius, vy + video_size - v_radius, v_radius, 0, math.pi / 2)
    cr.arc(vx + v_radius, vy + video_size - v_radius, v_radius, math.pi / 2, math.pi)
    cr.arc(vx + v_radius, vy + v_radius, v_radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.clip()

    cr.set_source_surface(v_surf, vx, vy)
    cr.paint()
    cr.restore()
    cr.reset_clip()

    # 3. Outer subtle glass rim
    cr.new_sub_path()
    cr.arc(card_w - radius, radius, radius, -math.pi / 2, 0)
    cr.arc(card_w - radius, card_h - radius, radius, 0, math.pi / 2)
    cr.arc(radius, card_h - radius, radius, math.pi / 2, math.pi)
    cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.16)
    cr.set_line_width(1.2)
    cr.stroke()

    # Convert cairo surface to BGRA numpy array
    buf = surf.get_data()
    card_bgra = np.ndarray(shape=(card_h, card_w, 4), dtype=np.uint8, buffer=buf)
    return card_bgra

# Generate frames sequence:
# Phase 1: Spring down from notch (frames 0..18)
# Phase 2: Scanning biometric loop (frames 18..84)
# Phase 3: Face Recognized! Unlock animation (frames 84..157)
# Phase 4: Green checkmark hold (frames 157..195)
# Phase 5: Smooth exit (frames 195..215)
TOTAL_FRAMES = 215
FPS = 60

def get_phase_info(frame_idx):
    # Spring physics for Y
    target_y = 16.0
    start_y = -35.0
    if frame_idx < 20:
        t = frame_idx / 20.0
        # Spring overshoot ease
        cur_y = start_y + (target_y - start_y) * (1 - math.exp(-6 * t) * math.cos(8 * t))
    elif frame_idx > 195:
        t = (frame_idx - 195) / 20.0
        cur_y = target_y - 45.0 * (t * t)
    else:
        cur_y = target_y

    # Video frame selection
    if frame_idx < 84:
        v_idx = frame_idx % len(idle_frames)
        v_frame = idle_frames[v_idx]
        is_recognized = False
    elif frame_idx < 157:
        v_idx = frame_idx - 84
        v_frame = unlock_frames[v_idx]
        is_recognized = True
    else:
        v_frame = unlock_frames[-1]
        is_recognized = True

    return cur_y, v_frame, is_recognized

# 2. Render 1920x1080 Green Screen Video
greenscreen_target = DOWNLOADS_DIR / "blink_face_recognized_lockscreen_greenscreen.mp4"
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out_green = cv2.VideoWriter(str(greenscreen_target), fourcc, FPS, (1920, 1080))

print("Rendering 1080p Chroma-Key Green Screen Video...")
CARD_W = 160
CARD_H = 160
CORNER_RAD = 32
VIDEO_SIZE = 126
V_CORNER_RAD = 22

for f_idx in range(TOTAL_FRAMES):
    cur_y, v_frame, is_rec = get_phase_info(f_idx)
    card_bgra = render_card_surface(v_frame, card_w=CARD_W, card_h=CARD_H, radius=CORNER_RAD, video_size=VIDEO_SIZE, v_radius=V_CORNER_RAD)

    # Pure green screen background: B=0, G=255, R=0
    bg = np.zeros((1080, 1920, 3), dtype=np.uint8)
    bg[:, :] = [0, 255, 0]

    # Composite card onto green background
    cx = (1920 - CARD_W) // 2
    cy = int(cur_y)

    # Blend card with alpha
    c_h, c_w, _ = card_bgra.shape
    y1 = max(0, cy)
    y2 = min(1080, cy + c_h)
    x1 = max(0, cx)
    x2 = min(1920, cx + c_w)

    card_y1 = max(0, -cy)
    card_y2 = card_y1 + (y2 - y1)
    card_x1 = max(0, -cx)
    card_x2 = card_x1 + (x2 - x1)

    if y2 > y1 and x2 > x1:
        patch = card_bgra[card_y1:card_y2, card_x1:card_x2]
        alpha = patch[:, :, 3:4] / 255.0
        card_bgr = patch[:, :, :3]
        bg[y1:y2, x1:x2] = (1.0 - alpha) * bg[y1:y2, x1:x2] + alpha * card_bgr

    out_green.write(bg)

out_green.release()
print(f"Saved: {greenscreen_target}")

# 3. Render 1920x1080 Real Lockscreen Reference Demo (with Wallpaper + Lockscreen Clock)
demo_target = DOWNLOADS_DIR / "blink_face_recognized_lockscreen_demo.mp4"
out_demo = cv2.VideoWriter(str(demo_target), fourcc, FPS, (1920, 1080))

print("Rendering 1080p Lockscreen Demo with Wallpaper...")
# Create rich Apple-style dark gradient wallpaper with blurred ambient glow
wp = np.zeros((1080, 1920, 3), dtype=np.float32)
# Deep obsidian to midnight blue gradient
for y in range(1080):
    t = y / 1080.0
    # Gradient from (15, 10, 8) to (28, 18, 12)
    wp[y, :] = [24.0 * t + 8.0, 16.0 * t + 6.0, 10.0 * t + 5.0]

# Add soft ambient colored spheres (Apple wallpaper style)
cv2.circle(wp, (960, 420), 450, (45, 25, 20), -1)
cv2.circle(wp, (450, 750), 400, (35, 18, 40), -1)
cv2.circle(wp, (1450, 680), 420, (20, 35, 45), -1)
wp = cv2.GaussianBlur(wp, (151, 151), 0)
wp_uint8 = np.clip(wp, 0, 255).astype(np.uint8)

# Render base wallpaper with lock icon and 9:41 clock using Cairo
def create_lockscreen_base(wp_img, is_unlocked):
    h, w, _ = wp_img.shape
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)

    # Draw wallpaper
    bgra_wp = cv2.cvtColor(wp_img, cv2.COLOR_BGR2BGRA)
    wp_surf = cairo.ImageSurface.create_for_data(bgra_wp, cairo.FORMAT_ARGB32, w, h, w * 4)
    cr.set_source_surface(wp_surf, 0, 0)
    cr.paint()

    # Draw subtle top notch bezel indicator
    cr.save()
    cr.set_source_rgba(0, 0, 0, 0.85)
    cr.arc(w / 2, 0, 14, 0, math.pi)
    cr.fill()
    # Camera lens dot
    cr.set_source_rgba(0.08, 0.15, 0.25, 0.8)
    cr.arc(w / 2, 6, 3, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # Draw Padlock icon (Locked vs Unlocked)
    cr.save()
    cr.set_line_width(2.5)
    if is_unlocked:
        cr.set_source_rgba(0.2, 0.95, 0.45, 0.9)  # Apple green
        # Shackle open
        cr.arc(w / 2, 235, 11, -math.pi, 0)
        cr.line_to(w / 2 + 11, 242)
        cr.stroke()
        # Body
        cr.rectangle(w / 2 - 13, 242, 26, 20)
        cr.fill()
    else:
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
        # Shackle closed
        cr.arc(w / 2, 238, 10, -math.pi, 0)
        cr.line_to(w / 2 + 10, 246)
        cr.line_to(w / 2 - 10, 246)
        cr.stroke()
        # Body
        cr.rectangle(w / 2 - 13, 246, 26, 20)
        cr.fill()
    cr.restore()

    # Draw Date & Time (Apple iOS/macOS typography)
    cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.75)
    cr.set_font_size(24)
    text_date = "Monday, July 17"
    x_bear, y_bear, tw, th, _, _ = cr.text_extents(text_date)
    cr.move_to((w - tw) / 2, 305)
    cr.show_text(text_date)

    # Large Clock 9:41
    cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
    cr.set_font_size(108)
    text_time = "9:41"
    x_bear, y_bear, tw, th, _, _ = cr.text_extents(text_time)
    cr.move_to((w - tw) / 2, 415)
    cr.show_text(text_time)

    # Bottom swipe / unlock pill
    cr.save()
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.4)
    cr.new_sub_path()
    bx = (w - 140) / 2
    by = 1045
    brad = 2.5
    cr.arc(bx + 140 - brad, by + brad, brad, -math.pi / 2, 0)
    cr.arc(bx + 140 - brad, by + 5 - brad, brad, 0, math.pi / 2)
    cr.arc(bx + brad, by + 5 - brad, brad, math.pi / 2, math.pi)
    cr.arc(bx + brad, by + brad, brad, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.fill()
    cr.restore()

    buf = surf.get_data()
    return np.ndarray(shape=(h, w, 4), dtype=np.uint8, buffer=buf)[:, :, :3].copy()

base_locked = create_lockscreen_base(wp_uint8, is_unlocked=False)
base_unlocked = create_lockscreen_base(wp_uint8, is_unlocked=True)

for f_idx in range(TOTAL_FRAMES):
    cur_y, v_frame, is_rec = get_phase_info(f_idx)
    card_bgra = render_card_surface(v_frame, card_w=CARD_W, card_h=CARD_H, radius=CORNER_RAD, video_size=VIDEO_SIZE, v_radius=V_CORNER_RAD)

    bg = base_unlocked.copy() if (f_idx >= 150) else base_locked.copy()

    cx = (1920 - CARD_W) // 2
    cy = int(cur_y)

    c_h, c_w, _ = card_bgra.shape
    y1 = max(0, cy)
    y2 = min(1080, cy + c_h)
    x1 = max(0, cx)
    x2 = min(1920, cx + c_w)

    card_y1 = max(0, -cy)
    card_y2 = card_y1 + (y2 - y1)
    card_x1 = max(0, -cx)
    card_x2 = card_x1 + (x2 - x1)

    if y2 > y1 and x2 > x1:
        patch = card_bgra[card_y1:card_y2, card_x1:card_x2]
        alpha = patch[:, :, 3:4] / 255.0
        card_bgr = patch[:, :, :3]
        bg[y1:y2, x1:x2] = (1.0 - alpha) * bg[y1:y2, x1:x2] + alpha * card_bgr

    out_demo.write(bg)

out_demo.release()
print(f"Saved: {demo_target}")

# 4. Render 500x500 Isolated Card Green Screen Video
card_green_target = DOWNLOADS_DIR / "blink_face_recognized_card_isolated_greenscreen.mp4"
out_card_green = cv2.VideoWriter(str(card_green_target), fourcc, FPS, (500, 500))

print("Rendering 500x500 High-Res Isolated Card Green Screen Video...")
CARD_ISO_SIZE = 420
RAD_ISO = 80
V_ISO_SIZE = 330
V_RAD_ISO = 55

for f_idx in range(TOTAL_FRAMES):
    cur_y, v_frame, is_rec = get_phase_info(f_idx)
    card_bgra = render_card_surface(v_frame, card_w=CARD_ISO_SIZE, card_h=CARD_ISO_SIZE, radius=RAD_ISO, video_size=V_ISO_SIZE, v_radius=V_RAD_ISO)

    bg = np.zeros((500, 500, 3), dtype=np.uint8)
    bg[:, :] = [0, 255, 0]  # pure green

    cx = (500 - CARD_ISO_SIZE) // 2
    cy = (500 - CARD_ISO_SIZE) // 2

    patch = card_bgra
    alpha = patch[:, :, 3:4] / 255.0
    card_bgr = patch[:, :, :3]
    bg[cy:cy+CARD_ISO_SIZE, cx:cx+CARD_ISO_SIZE] = (1.0 - alpha) * bg[cy:cy+CARD_ISO_SIZE, cx:cx+CARD_ISO_SIZE] + alpha * card_bgr

    out_card_green.write(bg)

out_card_green.release()
print(f"Saved: {card_green_target}")
print("All clips rendered successfully!")

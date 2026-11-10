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

# Sizing specifications
CARD_W = 168
CARD_H = 168
CORNER_RAD = 34
VIDEO_SIZE = 136
V_CORNER_RAD = 24

def render_card_surface(video_frame_bgr, card_w=CARD_W, card_h=CARD_H, radius=CORNER_RAD, video_size=VIDEO_SIZE, v_radius=V_CORNER_RAD):
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
    cr.set_source_rgba(0.015, 0.015, 0.025, 0.985)
    cr.fill_preserve()
    cr.clip()

    # 2. Centered video surface
    vx = (card_w - video_size) / 2.0
    vy = (card_h - video_size) / 2.0

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

    # 3. Outer Apple glass rim
    cr.new_sub_path()
    cr.arc(card_w - radius, radius, radius, -math.pi / 2, 0)
    cr.arc(card_w - radius, card_h - radius, radius, 0, math.pi / 2)
    cr.arc(radius, card_h - radius, radius, math.pi / 2, math.pi)
    cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.18)
    cr.set_line_width(1.2)
    cr.stroke()

    buf = surf.get_data()
    return np.ndarray(shape=(card_h, card_w, 4), dtype=np.uint8, buffer=buf).copy()

# Timeline Configuration (60 FPS):
# Total: 300 frames = 5.0 seconds
# 0 - 15 (0.0s - 0.25s): Screen completely clean / empty. Card is fully offscreen (y = -CARD_H - 30).
# 15 - 55 (0.25s - 0.9s): Card drops down with Apple spring physics from y = -200 to y = 18.
# 55 - 135 (0.9s - 2.25s): Card rests at y = 18, scanning phase (idle biometric loop).
# 135 - 208 (2.25s - 3.47s): Face Recognized! Unlock animation (73 frames of aperture blooming into glowing green checkmark).
# 208 - 265 (3.47s - 4.42s): Celebration hold: green checkmark steady with subtle pulsing glow.
# 265 - 300 (4.42s - 5.0s): Smooth upward retraction back offscreen into camera bezel (y = 18 back to -200).
TOTAL_FRAMES = 300
FPS = 60

Y_REST = 18.0
Y_OFFSCREEN = -CARD_H - 30.0  # -198.0 px (100% off screen above the top edge)

def get_frame_state(f):
    # 1. Calculate Y position
    if f < 15:
        # Offscreen
        y = Y_OFFSCREEN
        visible = False
    elif 15 <= f < 55:
        # Drop-down spring physics
        p = (f - 15) / 40.0
        zeta = 0.72
        omega = 7.2
        y = Y_REST + (Y_OFFSCREEN - Y_REST) * math.exp(-zeta * omega * p) * math.cos(omega * math.sqrt(1 - zeta**2) * p)
        visible = True
    elif 55 <= f < 265:
        # Resting position
        y = Y_REST
        visible = True
    else:
        # Retraction back offscreen
        p = (f - 265) / 35.0
        # Smooth ease in back to offscreen
        y = Y_REST + (Y_OFFSCREEN - Y_REST) * (p * p * (3 - 2 * p))
        visible = (y > -CARD_H)

    # 2. Select Video Frame
    if f < 135:
        v_idx = f % len(idle_frames)
        v_frame = idle_frames[v_idx]
        is_recognized = False
    elif 135 <= f < 208:
        v_idx = f - 135
        v_frame = unlock_frames[v_idx]
        is_recognized = True
    else:
        v_frame = unlock_frames[-1]
        is_recognized = True

    return y, v_frame, visible, is_recognized

# 1. Render 1920x1080 Green Screen Dropdown Video
greenscreen_target = DOWNLOADS_DIR / "blink_face_recognized_lockscreen_greenscreen.mp4"
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out_green = cv2.VideoWriter(str(greenscreen_target), fourcc, FPS, (1920, 1080))

print("Rendering 1080p Chroma-Key Green Screen Dropdown Video...")
for f in range(TOTAL_FRAMES):
    y, v_frame, visible, is_rec = get_frame_state(f)
    
    # Pure chroma key green
    bg = np.zeros((1080, 1920, 3), dtype=np.uint8)
    bg[:, :] = [0, 255, 0]

    if visible:
        card_bgra = render_card_surface(v_frame)
        cx = (1920 - CARD_W) // 2
        cy = int(y)

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

# 2. Render 1920x1080 Lockscreen Demo with Dropdown and Wallpaper
demo_target = DOWNLOADS_DIR / "blink_face_recognized_lockscreen_demo.mp4"
out_demo = cv2.VideoWriter(str(demo_target), fourcc, FPS, (1920, 1080))

print("Rendering 1080p Lockscreen Demo with Dropdown...")
# Create rich Apple macOS dark wallpaper
wp = np.zeros((1080, 1920, 3), dtype=np.float32)
for y_idx in range(1080):
    t = y_idx / 1080.0
    wp[y_idx, :] = [24.0 * t + 8.0, 16.0 * t + 6.0, 10.0 * t + 5.0]

cv2.circle(wp, (960, 420), 450, (45, 25, 20), -1)
cv2.circle(wp, (450, 750), 400, (35, 18, 40), -1)
cv2.circle(wp, (1450, 680), 420, (20, 35, 45), -1)
wp = cv2.GaussianBlur(wp, (151, 151), 0)
wp_uint8 = np.clip(wp, 0, 255).astype(np.uint8)

def create_lockscreen_base(wp_img, is_unlocked):
    h, w, _ = wp_img.shape
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)

    bgra_wp = cv2.cvtColor(wp_img, cv2.COLOR_BGR2BGRA)
    wp_surf = cairo.ImageSurface.create_for_data(bgra_wp, cairo.FORMAT_ARGB32, w, h, w * 4)
    cr.set_source_surface(wp_surf, 0, 0)
    cr.paint()

    # Subtle top bezel camera notch
    cr.save()
    cr.set_source_rgba(0, 0, 0, 0.9)
    cr.arc(w / 2, 0, 16, 0, math.pi)
    cr.fill()
    cr.set_source_rgba(0.08, 0.15, 0.25, 0.8)
    cr.arc(w / 2, 7, 3.5, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # Padlock icon
    cr.save()
    cr.set_line_width(2.5)
    if is_unlocked:
        cr.set_source_rgba(0.2, 0.95, 0.45, 0.95)
        cr.arc(w / 2, 235, 11, -math.pi, 0)
        cr.line_to(w / 2 + 11, 242)
        cr.stroke()
        cr.rectangle(w / 2 - 13, 242, 26, 20)
        cr.fill()
    else:
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
        cr.arc(w / 2, 238, 10, -math.pi, 0)
        cr.line_to(w / 2 + 10, 246)
        cr.line_to(w / 2 - 10, 246)
        cr.stroke()
        cr.rectangle(w / 2 - 13, 246, 26, 20)
        cr.fill()
    cr.restore()

    # Date
    cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.75)
    cr.set_font_size(24)
    text_date = "Monday, July 17"
    _, _, tw, _, _, _ = cr.text_extents(text_date)
    cr.move_to((w - tw) / 2, 305)
    cr.show_text(text_date)

    # 9:41 Time
    cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
    cr.set_font_size(108)
    text_time = "9:41"
    _, _, tw, _, _, _ = cr.text_extents(text_time)
    cr.move_to((w - tw) / 2, 415)
    cr.show_text(text_time)

    # Bottom swipe bar
    cr.save()
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.4)
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

for f in range(TOTAL_FRAMES):
    y, v_frame, visible, is_rec = get_frame_state(f)
    bg = base_unlocked.copy() if (f >= 195) else base_locked.copy()

    if visible:
        card_bgra = render_card_surface(v_frame)
        cx = (1920 - CARD_W) // 2
        cy = int(y)

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

# 3. Also render a standalone 600x600 Dropdown Green Screen Clip (Focus on the Card Drop)
focus_target = DOWNLOADS_DIR / "blink_face_recognized_dropdown_focus_greenscreen.mp4"
out_focus = cv2.VideoWriter(str(focus_target), fourcc, FPS, (600, 600))
print("Rendering 600x600 Dropdown Focus Green Screen Video...")

FOCUS_CARD_W = 220
FOCUS_CARD_H = 220
FOCUS_CORNER_RAD = 44
FOCUS_VIDEO_SIZE = 180
FOCUS_V_CORNER_RAD = 32

FOCUS_Y_REST = 30.0
FOCUS_Y_OFFSCREEN = -FOCUS_CARD_H - 40.0

for f in range(TOTAL_FRAMES):
    if f < 15:
        y = FOCUS_Y_OFFSCREEN
        visible = False
    elif 15 <= f < 55:
        p = (f - 15) / 40.0
        zeta = 0.72
        omega = 7.2
        y = FOCUS_Y_REST + (FOCUS_Y_OFFSCREEN - FOCUS_Y_REST) * math.exp(-zeta * omega * p) * math.cos(omega * math.sqrt(1 - zeta**2) * p)
        visible = True
    elif 55 <= f < 265:
        y = FOCUS_Y_REST
        visible = True
    else:
        p = (f - 265) / 35.0
        y = FOCUS_Y_REST + (FOCUS_Y_OFFSCREEN - FOCUS_Y_REST) * (p * p * (3 - 2 * p))
        visible = (y > -FOCUS_CARD_H)

    if f < 135:
        v_idx = f % len(idle_frames)
        v_frame = idle_frames[v_idx]
    elif 135 <= f < 208:
        v_idx = f - 135
        v_frame = unlock_frames[v_idx]
    else:
        v_frame = unlock_frames[-1]

    bg = np.zeros((600, 600, 3), dtype=np.uint8)
    bg[:, :] = [0, 255, 0]

    if visible:
        card_bgra = render_card_surface(v_frame, card_w=FOCUS_CARD_W, card_h=FOCUS_CARD_H, radius=FOCUS_CORNER_RAD, video_size=FOCUS_VIDEO_SIZE, v_radius=FOCUS_V_CORNER_RAD)
        cx = (600 - FOCUS_CARD_W) // 2
        cy = int(y)

        c_h, c_w, _ = card_bgra.shape
        y1 = max(0, cy)
        y2 = min(600, cy + c_h)
        x1 = max(0, cx)
        x2 = min(600, cx + c_w)

        card_y1 = max(0, -cy)
        card_y2 = card_y1 + (y2 - y1)
        card_x1 = max(0, -cx)
        card_x2 = card_x1 + (x2 - x1)

        if y2 > y1 and x2 > x1:
            patch = card_bgra[card_y1:card_y2, card_x1:card_x2]
            alpha = patch[:, :, 3:4] / 255.0
            card_bgr = patch[:, :, :3]
            bg[y1:y2, x1:x2] = (1.0 - alpha) * bg[y1:y2, x1:x2] + alpha * card_bgr

    out_focus.write(bg)

out_focus.release()
print(f"Saved: {focus_target}")
print("ALL NEW FULL DROPDOWN CLIPS RENDERED SUCCESSFULLY!")

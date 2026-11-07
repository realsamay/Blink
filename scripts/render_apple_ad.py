#!/usr/bin/env python3
"""
Apple-style Cinematic Product Announcement Video Generator for Blink.
Renders a 1080p 30fps commercial introducing Blink Face ID for Linux & Windows.
"""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter


# Video Specs
WIDTH = 1920
HEIGHT = 1080
FPS = 30
TOTAL_DURATION = 42.0  # seconds
TOTAL_FRAMES = int(TOTAL_DURATION * FPS)

OUT_FILE = "/home/samayy/blink/blink_ad_introducing_blink.mp4"
FRAMES_DIR = "/home/samayy/.gemini/antigravity-cli/brain/3903cd0d-66ad-4d46-963e-a5f7a2b1cc24/ad_frames"
os.makedirs(FRAMES_DIR, exist_ok=True)

# Assets
RESOURCES = Path("/home/samayy/blink/blink/resources")
LOGO_PNG = RESOURCES / "final.png"
LOGO_MP4 = RESOURCES / "logoanimation.mp4"
UNLOCK_MP4 = RESOURCES / "unlockanimation.mp4"
IDLE_MP4 = RESOURCES / "idleanimation.mp4"

ARTIFACTS = Path("/home/samayy/.gemini/antigravity-cli/brain/3903cd0d-66ad-4d46-963e-a5f7a2b1cc24")
SCREEN_FACE_ID = ARTIFACTS / "v2_tab_face_id.png"
SCREEN_DIAG = ARTIFACTS / "v2_tab_diagnostics_live.png"
SCREEN_CAMERA = ARTIFACTS / "v2_tab_camera.png"
SCREEN_SUITE = ARTIFACTS / "v2_suite_montage.png"

# Typography
FONT_BOLD_PATH = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_REG_PATH = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# Easing curves
def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)

def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - math.pow(1.0 - t, 3)

def ease_in_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t

def spring_pop(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 + 0.25 * math.sin(t * math.pi) * (1.0 - t)


# Preload video clips
def load_video_frames(path: Path) -> List[Image.Image]:
    frames = []
    if not path.is_file():
        return frames
    cap = cv2.VideoCapture(str(path))
    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frames.append(Image.fromarray(rgb))
    cap.release()
    return frames

print("[Renderer] Loading source video assets...")
logo_frames = load_video_frames(LOGO_MP4)
unlock_frames = load_video_frames(UNLOCK_MP4)
print(f"[Renderer] Loaded {len(logo_frames)} logo frames and {len(unlock_frames)} unlock frames.")

# Preload static images
img_logo = Image.open(LOGO_PNG).convert("RGBA") if LOGO_PNG.is_file() else None
img_face_id = Image.open(SCREEN_FACE_ID).convert("RGBA") if SCREEN_FACE_ID.is_file() else None
img_diag = Image.open(SCREEN_DIAG).convert("RGBA") if SCREEN_DIAG.is_file() else None


# Drawing helpers
def draw_radial_background(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, col_center: Tuple[int, int, int], col_edge: Tuple[int, int, int]):
    # Fast multi-step radial gradient simulation
    steps = 18
    for i in range(steps, 0, -1):
        frac = i / steps
        cur_r = int(r * frac)
        cr = int(col_center[0] * (1.0 - frac) + col_edge[0] * frac)
        cg = int(col_center[1] * (1.0 - frac) + col_edge[1] * frac)
        cb = int(col_center[2] * (1.0 - frac) + col_edge[2] * frac)
        draw.ellipse([cx - cur_r, cy - cur_r, cx + cur_r, cy + cur_r], fill=(cr, cg, cb))


def render_scene_1(t: float) -> Image.Image:
    """Scene 1: The Hook (0.0s - 4.5s)"""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (8, 9, 13, 255))
    draw = ImageDraw.Draw(img)

    # Ambient subtle blue glow
    glow_pulse = 0.5 + 0.5 * math.sin(t * 1.5)
    draw_radial_background(draw, WIDTH // 2, HEIGHT // 2 + 100, 600, (18, 28, 48), (8, 9, 13))

    f_sub = get_font(34, bold=False)
    f_main = get_font(68, bold=True)
    f_mute = get_font(38, bold=False)

    # Line 1: What if unlocking your laptop...
    alpha_1 = ease_out_cubic(min(1.0, max(0.0, (t - 0.4) / 0.8)))
    if alpha_1 > 0:
        c1 = int(180 * alpha_1)
        draw.text((WIDTH // 2, HEIGHT // 2 - 90), "What if unlocking your laptop...", font=f_sub, fill=(c1, c1, c1, 255), anchor="mm")

    # Line 2: Was as simple as looking at it?
    alpha_2 = ease_out_cubic(min(1.0, max(0.0, (t - 1.6) / 0.8)))
    if alpha_2 > 0:
        c2 = int(255 * alpha_2)
        draw.text((WIDTH // 2, HEIGHT // 2), "Was as simple as looking at it?", font=f_main, fill=(c2, c2, c2, 255), anchor="mm")

    # Line 3: Zero keys. Zero friction.
    alpha_3 = ease_out_cubic(min(1.0, max(0.0, (t - 2.8) / 0.8)))
    if alpha_3 > 0:
        c3 = int(140 * alpha_3)
        draw.text((WIDTH // 2, HEIGHT // 2 + 80), "Zero keys. Zero friction. Instant unlock.", font=f_mute, fill=(c3, c3, c3, 255), anchor="mm")

    return img


def render_scene_2(t: float) -> Image.Image:
    """Scene 2: The Reveal - Introducing Blink (4.5s - 10.5s)"""
    local_t = t - 4.5
    img = Image.new("RGBA", (WIDTH, HEIGHT), (6, 7, 10, 255))
    draw = ImageDraw.Draw(img)

    # Expanding radial iris
    iris_r = int(500 + 300 * ease_out_cubic(min(1.0, local_t / 3.0)))
    draw_radial_background(draw, WIDTH // 2, HEIGHT // 2 - 40, iris_r, (20, 40, 80), (6, 7, 10))

    # Animate logo frames
    if logo_frames:
        frame_idx = int(local_t * 60) % len(logo_frames)
        logo_f = logo_frames[frame_idx]
        scale = 0.95 + 0.05 * math.sin(local_t * 1.5)
        lw, lh = int(logo_f.width * scale), int(logo_f.height * scale)
        logo_scaled = logo_f.resize((lw, lh), Image.Resampling.LANCZOS)
        # Paste in center
        img.paste(logo_scaled, (WIDTH // 2 - lw // 2, HEIGHT // 2 - lh // 2 - 70))

    # Headline
    f_super = get_font(32, bold=False)
    f_title = get_font(84, bold=True)
    f_sub = get_font(36, bold=False)

    a_title = ease_out_cubic(min(1.0, max(0.0, (local_t - 1.2) / 0.8)))
    if a_title > 0:
        c_blue = (10, 132, 255)
        c_white = int(255 * a_title)
        draw.text((WIDTH // 2, HEIGHT // 2 + 180), "INTRODUCING", font=f_super, fill=c_blue, anchor="mm")
        draw.text((WIDTH // 2, HEIGHT // 2 + 250), "Blink.", font=f_title, fill=(c_white, c_white, c_white, 255), anchor="mm")
        draw.text((WIDTH // 2, HEIGHT // 2 + 325), "Native Face ID for Linux & Windows", font=f_sub, fill=(160, 160, 175, 255), anchor="mm")

    return img


def render_scene_3(t: float) -> Image.Image:
    """Scene 3: The Magic Moment - Instant Lockscreen Unlock (10.5s - 18.0s)"""
    local_t = t - 10.5
    img = Image.new("RGBA", (WIDTH, HEIGHT), (10, 11, 15, 255))
    draw = ImageDraw.Draw(img)

    # Laptop Frame Mockup
    lap_w = 1200
    lap_h = 740
    lap_x = (WIDTH - lap_w) // 2
    lap_y = (HEIGHT - lap_h) // 2 + 40

    # Laptop aluminum body
    draw.rounded_rectangle([lap_x - 14, lap_y - 14, lap_x + lap_w + 14, lap_y + lap_h + 14], radius=24, fill=(45, 45, 52), outline=(75, 75, 85), width=2)
    # Laptop screen border
    draw.rounded_rectangle([lap_x, lap_y, lap_x + lap_w, lap_y + lap_h], radius=16, fill=(16, 18, 24))

    # Lockscreen Wallpaper: Elegant Apple macOS gradient
    is_unlocked = local_t > 3.8
    if not is_unlocked:
        # Locked state
        draw_radial_background(draw, lap_x + lap_w // 2, lap_y + lap_h // 2, lap_w // 2, (38, 24, 60), (16, 18, 24))
        # Clock: 9:41
        f_clock = get_font(90, bold=True)
        draw.text((lap_x + lap_w // 2, lap_y + 240), "9:41", font=f_clock, fill=(240, 240, 250, 255), anchor="mm")
        f_date = get_font(26, bold=False)
        draw.text((lap_x + lap_w // 2, lap_y + 310), "Saturday, November 7", font=f_date, fill=(180, 180, 195, 255), anchor="mm")
    else:
        # Unlocked state (revealing desktop)
        fade_prog = min(1.0, (local_t - 3.8) / 0.6)
        cr = int(16 + fade_prog * (25 - 16))
        cg = int(18 + fade_prog * (45 - 18))
        cb = int(24 + fade_prog * (85 - 24))
        draw_radial_background(draw, lap_x + lap_w // 2, lap_y + lap_h // 2, lap_w // 2, (cr, cg, cb), (16, 18, 24))

        f_wel = get_font(52, bold=True)
        draw.text((lap_x + lap_w // 2, lap_y + 300), "Welcome back, Samay", font=f_wel, fill=(255, 255, 255, 255), anchor="mm")
        f_stat = get_font(26, bold=False)
        draw.text((lap_x + lap_w // 2, lap_y + 360), "Unlocked with Face ID", font=f_stat, fill=(48, 209, 88, 255), anchor="mm")

    # The Dynamic Island / Notch at top of screen!
    notch_w = 160
    notch_h = 32
    # Expands when scanning begins
    if 0.8 < local_t < 4.2:
        expand_p = ease_out_cubic(min(1.0, (local_t - 0.8) / 0.4))
        if local_t > 3.6:
            # collapse
            expand_p = 1.0 - ease_in_cubic(min(1.0, (local_t - 3.6) / 0.5))

        cur_nw = int(notch_w + expand_p * 140)
        cur_nh = int(notch_h + expand_p * 110)
        nx = lap_x + (lap_w - cur_nw) // 2
        ny = lap_y

        draw.rounded_rectangle([nx, ny, nx + cur_nw, ny + cur_nh], radius=22, fill=(0, 0, 0, 255), outline=(60, 60, 70), width=1)

        # Draw unlock animation frames inside notch
        if unlock_frames and 1.2 < local_t < 3.8:
            uf_idx = int((local_t - 1.2) * 45) % len(unlock_frames)
            uf = unlock_frames[uf_idx].resize((88, 88), Image.Resampling.LANCZOS)
            img.paste(uf, (lap_x + lap_w // 2 - 44, ny + cur_nh // 2 - 44))
    else:
        # Idle small camera notch
        nx = lap_x + (lap_w - notch_w) // 2
        draw.rounded_rectangle([nx, lap_y, nx + notch_w, lap_y + notch_h], radius=16, fill=(0, 0, 0, 255))
        # Camera lens dot
        draw.ellipse([lap_x + lap_w // 2 - 4, lap_y + 12, lap_x + lap_w // 2 + 4, lap_y + 20], fill=(15, 25, 45))

    # Top overlay text
    f_tag = get_font(42, bold=True)
    f_detail = get_font(28, bold=False)
    draw.text((WIDTH // 2, lap_y - 70), "Look. Unlocked.", font=f_tag, fill=(255, 255, 255, 255), anchor="mm")
    draw.text((WIDTH // 2, lap_y - 30), "In 0.2 seconds. No typing passwords.", font=f_detail, fill=(160, 160, 175, 255), anchor="mm")

    return img


def render_scene_4(t: float) -> Image.Image:
    """Scene 4: On-Device Neural Vision & Real-time Telemetry (18.0s - 26.0s)"""
    local_t = t - 18.0
    img = Image.new("RGBA", (WIDTH, HEIGHT), (8, 9, 13, 255))
    draw = ImageDraw.Draw(img)

    # Ambient grid & cyber blue gradient
    draw_radial_background(draw, 400, HEIGHT // 2, 800, (14, 28, 55), (8, 9, 13))

    # Floating Diagnostics Screen with Ken Burns effect
    if img_diag:
        zoom = 1.0 + 0.05 * (local_t / 8.0)
        dw, dh = int(960 * zoom), int(640 * zoom)
        diag_resized = img_diag.resize((dw, dh), Image.Resampling.LANCZOS)
        # Pan gently
        pan_x = int(120 - local_t * 10)
        pan_y = int((HEIGHT - dh) // 2)

        # Card shadow & border
        draw.rounded_rectangle([pan_x - 6, pan_y - 6, pan_x + dw + 6, pan_y + dh + 6], radius=18, fill=(30, 30, 40, 180), outline=(0, 132, 255, 120), width=2)
        img.paste(diag_resized, (pan_x, pan_y))

    # Right side: Feature Callout Badges
    rx = 1200
    ry_start = 320

    f_head = get_font(52, bold=True)
    f_sub = get_font(28, bold=False)
    draw.text((rx, ry_start - 100), "Zero Latency.", font=f_head, fill=(255, 255, 255, 255), anchor="ls")
    draw.text((rx, ry_start - 60), "Real-time on-device neural vision.", font=f_sub, fill=(140, 140, 160, 255), anchor="ls")

    badges = [
        ("ArcFace 512-D Biometrics", "Cosine similarity biometric matching (>99.8% precision)"),
        ("Live 106-Point Mesh", "High-frequency 3D face tracking and pupil alignment"),
        ("Active Anti-Spoofing", "Specular reflection and specular depth liveness guard"),
    ]

    for idx, (b_title, b_desc) in enumerate(badges):
        badge_y = ry_start + idx * 115
        prog = ease_out_cubic(min(1.0, max(0.0, (local_t - 0.6 - idx * 0.7) / 0.6)))
        if prog > 0:
            bx = int(rx + (1.0 - prog) * 100)
            alpha = int(255 * prog)

            # Glass badge box
            draw.rounded_rectangle([bx, badge_y, bx + 620, badge_y + 85], radius=14, fill=(20, 22, 30, 220), outline=(80, 80, 110, alpha), width=1)
            # Apple green pill
            draw.ellipse([bx + 20, badge_y + 36, bx + 32, badge_y + 48], fill=(48, 209, 88, alpha))
            f_bt = get_font(28, bold=True)
            f_bd = get_font(20, bold=False)
            draw.text((bx + 48, badge_y + 30), b_title, font=f_bt, fill=(255, 255, 255, alpha), anchor="ls")
            draw.text((bx + 48, badge_y + 58), b_desc, font=f_bd, fill=(150, 150, 170, alpha), anchor="ls")

    return img


def render_scene_5(t: float) -> Image.Image:
    """Scene 5: Privacy by Design - 100% On-Device (26.0s - 33.0s)"""
    local_t = t - 26.0
    img = Image.new("RGBA", (WIDTH, HEIGHT), (6, 7, 10, 255))
    draw = ImageDraw.Draw(img)

    # Ambient deep emerald/sapphire glow
    draw_radial_background(draw, WIDTH // 2, HEIGHT // 2 - 80, 600, (15, 45, 30), (6, 7, 10))

    # Metallic Vault Lock Graphic
    cx, cy = WIDTH // 2, HEIGHT // 2 - 80
    pulse = 0.5 + 0.5 * math.sin(local_t * 2.0)

    # Glowing rings
    draw.ellipse([cx - 140, cy - 140, cx + 140, cy + 140], outline=(48, 209, 88, int(80 + 100 * pulse)), width=2)
    draw.ellipse([cx - 110, cy - 110, cx + 110, cy + 110], fill=(16, 26, 20), outline=(48, 209, 88, 200), width=3)

    # Padlock graphic
    # Shackle
    draw.arc([cx - 40, cy - 65, cx + 40, cy + 5], start=180, end=360, fill=(220, 220, 230), width=12)
    # Lock body
    draw.rounded_rectangle([cx - 50, cy - 15, cx + 50, cy + 55], radius=10, fill=(48, 209, 88), outline=(255, 255, 255), width=2)
    # Keyhole
    draw.ellipse([cx - 8, cy + 6, cx + 8, cy + 22], fill=(10, 15, 10))
    draw.polygon([(cx - 5, cy + 18), (cx + 5, cy + 18), (cx + 8, cy + 34), (cx - 8, cy + 34)], fill=(10, 15, 10))

    # Bold Typography
    f_h1 = get_font(64, bold=True)
    f_sub = get_font(34, bold=False)
    f_det = get_font(26, bold=False)

    a1 = ease_out_cubic(min(1.0, local_t / 0.8))
    if a1 > 0:
        c1 = int(255 * a1)
        draw.text((WIDTH // 2, cy + 160), "100% On-Device.", font=f_h1, fill=(c1, c1, c1, 255), anchor="mm")
        draw.text((WIDTH // 2, cy + 230), "Zero Cloud. Zero Data Leaves Your Machine.", font=f_sub, fill=(48, 209, 88, 255), anchor="mm")
        draw.text((WIDTH // 2, cy + 285), "Biometric embeddings encrypted with hardware-linked AES-256-GCM.", font=f_det, fill=(150, 150, 165, 255), anchor="mm")

    return img


def render_scene_6(t: float) -> Image.Image:
    """Scene 6: The Glass Experience (33.0s - 38.5s)"""
    local_t = t - 33.0
    img = Image.new("RGBA", (WIDTH, HEIGHT), (8, 9, 13, 255))
    draw = ImageDraw.Draw(img)

    # Ambient lighting
    draw_radial_background(draw, WIDTH // 2, HEIGHT // 2, 700, (25, 20, 45), (8, 9, 13))

    # Showcase the Face ID Glass Interface Window
    if img_face_id:
        scale = 0.82 + 0.04 * math.sin(local_t * 0.8)
        fw = int(img_face_id.width * scale)
        fh = int(img_face_id.height * scale)
        face_scaled = img_face_id.resize((fw, fh), Image.Resampling.LANCZOS)

        # Gentle floating offset
        float_y = int(math.sin(local_t * 1.2) * 12)
        fx = (WIDTH - fw) // 2
        fy = (HEIGHT - fh) // 2 + 30 + float_y

        # Premium glass drop shadow & glow
        draw.rounded_rectangle([fx - 8, fy - 8, fx + fw + 8, fy + fh + 8], radius=22, fill=(0, 0, 0, 160), outline=(255, 255, 255, 70), width=1)
        img.paste(face_scaled, (fx, fy))

    f_top = get_font(54, bold=True)
    f_sub = get_font(28, bold=False)

    draw.text((WIDTH // 2, 75), "Designed with obsessive care.", font=f_top, fill=(255, 255, 255, 255), anchor="mm")
    draw.text((WIDTH // 2, 125), "Apple Glassmorphism. Whole-sensor viewfinder. Fluid navigation.", font=f_sub, fill=(160, 160, 180, 255), anchor="mm")

    return img


def render_scene_7(t: float) -> Image.Image:
    """Scene 7: Outro & Call to Action (38.5s - 42.0s)"""
    local_t = t - 38.5
    img = Image.new("RGBA", (WIDTH, HEIGHT), (6, 7, 10, 255))
    draw = ImageDraw.Draw(img)

    # Radiant center bloom
    bloom_r = int(350 + 200 * ease_out_cubic(min(1.0, local_t / 1.5)))
    draw_radial_background(draw, WIDTH // 2, HEIGHT // 2 - 60, bloom_r, (25, 45, 90), (6, 7, 10))

    # Logo
    if img_logo:
        scale = 0.55
        lw, lh = int(img_logo.width * scale), int(img_logo.height * scale)
        logo_s = img_logo.resize((lw, lh), Image.Resampling.LANCZOS)
        img.paste(logo_s, (WIDTH // 2 - lw // 2, HEIGHT // 2 - lh // 2 - 80), mask=logo_s)

    f_logo = get_font(76, bold=True)
    f_sub = get_font(34, bold=False)
    f_url = get_font(38, bold=True)
    f_foot = get_font(24, bold=False)

    draw.text((WIDTH // 2, HEIGHT // 2 + 90), "Blink.", font=f_logo, fill=(255, 255, 255, 255), anchor="mm")
    draw.text((WIDTH // 2, HEIGHT // 2 + 150), "Face ID, reimagined for your computer.", font=f_sub, fill=(180, 180, 195, 255), anchor="mm")

    # URL Button Pill
    pill_w = 520
    pill_h = 60
    px = (WIDTH - pill_w) // 2
    py = HEIGHT // 2 + 205
    draw.rounded_rectangle([px, py, px + pill_w, py + pill_h], radius=30, fill=(10, 132, 255), outline=(255, 255, 255, 120), width=1)
    draw.text((WIDTH // 2, py + pill_h // 2), "github.com/realsamay/blink", font=f_url, fill=(255, 255, 255, 255), anchor="mm")

    draw.text((WIDTH // 2, HEIGHT - 60), "Free • Open Source • 100% Private", font=f_foot, fill=(120, 120, 135, 255), anchor="mm")

    return img


def main():
    print(f"[Renderer] Initializing 1080p MP4 VideoWriter: {OUT_FILE}...")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(OUT_FILE, fourcc, FPS, (WIDTH, HEIGHT))

    if not writer.isOpened():
        print("[Renderer] Error: Could not open VideoWriter.")
        sys.exit(1)

    t0 = time.time()
    print(f"[Renderer] Rendering {TOTAL_FRAMES} frames ({TOTAL_DURATION}s @ {FPS}fps)...")

    # Sample keyframe timestamps to save as stills for inspection
    keyframe_stills = {
        60: "ad_scene1_hook.png",          # 2.0s
        210: "ad_scene2_reveal.png",       # 7.0s
        420: "ad_scene3_unlock.png",       # 14.0s
        660: "ad_scene4_telemetry.png",    # 22.0s
        880: "ad_scene5_privacy.png",      # 29.3s
        1080: "ad_scene6_glass_ui.png",    # 36.0s
        1220: "ad_scene7_outro.png",       # 40.6s
    }

    for frame_idx in range(TOTAL_FRAMES):
        t = float(frame_idx) / float(FPS)

        if t < 4.5:
            frame_img = render_scene_1(t)
        elif t < 10.5:
            frame_img = render_scene_2(t)
        elif t < 18.0:
            frame_img = render_scene_3(t)
        elif t < 26.0:
            frame_img = render_scene_4(t)
        elif t < 33.0:
            frame_img = render_scene_5(t)
        elif t < 38.5:
            frame_img = render_scene_6(t)
        else:
            frame_img = render_scene_7(t)

        # Convert PIL RGBA to OpenCV BGR
        frame_rgb = frame_img.convert("RGB")
        frame_bgr = cv2.cvtColor(np.array(frame_rgb), cv2.COLOR_RGB2BGR)
        writer.write(frame_bgr)

        # Save keyframe still
        if frame_idx in keyframe_stills:
            still_path = os.path.join(FRAMES_DIR, keyframe_stills[frame_idx])
            frame_img.save(still_path)
            # Also save directly in brain directory for artifact viewing
            art_path = os.path.join(str(ARTIFACTS), keyframe_stills[frame_idx])
            frame_img.save(art_path)
            print(f"[Renderer] Saved keyframe preview: {keyframe_stills[frame_idx]}")

        if frame_idx % 150 == 0:
            pct = int((frame_idx / TOTAL_FRAMES) * 100)
            elapsed = time.time() - t0
            print(f"[Renderer] Progress: {pct}% ({frame_idx}/{TOTAL_FRAMES} frames in {elapsed:.1f}s)...")

    writer.release()
    total_time = time.time() - t0
    file_size_mb = os.path.getsize(OUT_FILE) / (1024 * 1024)
    print(f"[Renderer] Complete! Exported {OUT_FILE} ({file_size_mb:.2f} MB in {total_time:.1f}s)")


if __name__ == "__main__":
    main()

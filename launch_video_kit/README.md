# Blink Launch Ad Video Kit 🚀

This kit contains all the high-resolution assets, animation clips, UI screenshots, and AI video generator prompts needed to create a world-class, Apple-style launch commercial for **Blink**.

---

## 📂 Folder Structure

```
launch_video_kit/
├── assets/                  # Logos, icons, and vector graphics
│   ├── blink-logo.svg       # Official vector logo (Scalable)
│   ├── final.png            # High-res glowing emblem (Transparent)
│   ├── logo.png             # Alternate logo badge
│   ├── appicon.png          # High-res square application icon
│   └── appicon.ico          # Windows executable icon
│
├── clips/                   # 60fps native animation source clips
│   ├── logoanimation.mp4    # Official glowing neon emblem intro animation (60fps)
│   ├── unlockanimation.mp4  # Apple Face ID scanning ring & checkmark animation (60fps)
│   ├── idleanimation.mp4    # Ambient floating biometric ring loop (60fps)
│   └── unsuccessfulunlockanimation.mp4 # Failure shake & retry animation
│
├── screenshots/             # 4K / HD real UI screenshots & keyframe stills
│   ├── v2_suite_montage.png # 3D multi-tab perspective montage
│   ├── v2_tab_face_id.png   # Face ID settings & enrolled profile card
│   ├── v2_tab_face_id_multi_match.png # Real-time multi-person camera highlight
│   ├── v2_tab_camera.png    # Uncropped whole-sensor optical viewfinder
│   ├── v2_tab_diagnostics_live.png # Live on-camera neural HUD with 106pt mesh
│   ├── v2_tab_about.png     # Obsidian glass About page
│   └── ad_scene1..7.png     # Rendered commercial keyframe storyboard stills
│
└── prompts/                 # Complete prompts for AI video & voice tools
    ├── MASTER_AI_VIDEO_PROMPT.md    # Prompts for Runway Gen-3, Luma, Kling, Sora, Pika
    └── VOICEOVER_AND_AUDIO_PROMPT.md # Timed script for ElevenLabs, SFX, and music
```

---

## 🛠️ How to Use This Kit with AI Video Generators

### Option A: End-to-End AI Video Generation (Runway Gen-3 / Luma / Kling)
1. Open [Runway Gen-3 Alpha](https://runwayml.com/), [Luma Dream Machine](https://lumalabs.ai/dream-machine), or [Kling AI](https://klingai.com/).
2. Open `prompts/MASTER_AI_VIDEO_PROMPT.md`.
3. Copy either:
   - **Section 1**: The single all-in-one Master Prompt for generating a complete commercial montage.
   - **Section 2**: The scene-by-scene prompts (Shot 1 through Shot 7) to generate individual 5–10 second clips.
4. Upload the corresponding reference images from `assets/` or `screenshots/` as Image-to-Video prompts:
   - For Scene 2 (Logo Reveal): Upload `assets/final.png`.
   - For Scene 3 (Unlock): Upload `screenshots/ad_scene3_unlock.png`.
   - For Scene 4 (Neural Telemetry): Upload `screenshots/v2_tab_diagnostics_live.png`.
   - For Scene 6 (Glass UI): Upload `screenshots/v2_suite_montage.png`.

---

### Option B: Generating Voiceover & Music
1. **Voiceover**:
   - Open [ElevenLabs](https://elevenlabs.io/).
   - Select voice **"Adam"** or **"Antony"** (calm, authoritative British/Atlantic keynote tone).
   - Copy the timed script from `prompts/VOICEOVER_AND_AUDIO_PROMPT.md` and generate the 42-second audio track.
2. **Music & SFX**:
   - Open [Suno](https://suno.com/) or [Udio](https://udio.com/) and paste the prompt in `prompts/VOICEOVER_AND_AUDIO_PROMPT.md` under **Section 2**.
   - Sync the sound effects (spring pop, unlock chime) using the SFX cue sheet in **Section 3**.

---

### Option C: Assembling in Video Editors (CapCut, Premiere, DaVinci Resolve)
1. Drag the 4 source animation clips from `clips/` directly onto your timeline:
   - `logoanimation.mp4` for the title reveal.
   - `unlockanimation.mp4` over a laptop mockup for the unlock moment.
2. Place the high-res UI screenshots from `screenshots/` with a smooth 105% Ken Burns slow zoom.
3. Layer the voiceover track and ambient music.
4. Export at 4K / 1080p 60fps!

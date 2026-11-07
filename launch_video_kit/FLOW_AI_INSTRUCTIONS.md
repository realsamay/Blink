# Flow AI Production Blueprint: "Introducing Blink"

Use this guide to generate an authentic Apple Keynote-style product announcement video in **Flow AI**.

---

## ⚙️ 1. Project Setup in Flow AI

| Setting | Value |
| :--- | :--- |
| **Aspect Ratio** | `16:9` (Widescreen landscape, 1920×1080 or 4K) |
| **Visual Style** | `Apple Keynote / Tech Commercial / Dark Mode Glassmorphism` |
| **Voiceover Persona** | `British Tech Executive` or `Deep Calm Visionary` (Jony Ive / Craig Federighi style) |
| **Music Mood** | `Minimalist Ambient Electronic, Warm Analog Synths, Subtle 808 Sub-Drop` |
| **Pacing** | `Measured & Deliberate (with dramatic 0.8s pauses)` |
| **Target Duration** | `40 – 45 seconds` |

---

## 🎬 2. The Master Prompt for Flow AI
Paste this into Flow AI's initial prompt / project description box:

```text
Create a cinematic Apple Keynote launch commercial for "Blink" — a native Face ID biometric facial recognition system for laptops and desktop computers. 

Style & Tone:
- Minimalist dark mode aesthetic: obsidian black background, subtle deep sapphire and emerald glows, Apple glassmorphism.
- Kinetic typography: clean white sans-serif text fading smoothly with soft momentum.
- Camera: slow cinematic dollys, macro zooms, and smooth orbital pans.
- Audio: confident, calm British/Atlantic tech visionary voiceover with atmospheric analog synth pads and Apple-style tactile UI sound effects (screen wake chirps, spring pop, and two-tone harmonic unlock chimes).

Story Outline:
1. Hook: In pitch-black space, question fades in: "What if unlocking your computer was as simple as looking at it?"
2. Logo Reveal: Glowing neon-blue Blink emblem blooms with radiant light: "Introducing Blink."
3. The Magic Moment: Sleek laptop screen wakes up. An Apple Dynamic Island notch springs out from the top display bezel, spins a Face ID biometric aperture ring, blooms radiant green, and instantly unlocks the computer in 0.2 seconds.
4. Neural Vision: Live camera diagnostics view with 106-point 3D facial landmark mesh and real-time biometric confidence tracking.
5. Privacy: Vault shield graphic pulsing with emerald protection rings: "100% On-Device. Zero Cloud. Hardware AES-256 Encrypted."
6. Glass Interface: 3D perspective of the redesigned dark glass settings application.
7. Outro: Final glowing Blink crest with link: github.com/realsamay/blink.
```

---

## 🎞️ 3. Scene-by-Scene Flow AI Production Table

For each scene in Flow AI's storyboard editor, attach the specified asset from `launch_video_kit/`:

### Scene 1: The Hook (`0:00 - 0:05`)
- **Voiceover**: *"What if unlocking your computer... was as simple as looking at it? Zero keys. Zero friction."*
- **Attach Asset**: `launch_video_kit/screenshots/ad_scene1_hook.png`
- **Visual Prompt**: `Pitch black minimalist Apple studio. Subtle deep blue volumetric vignette. Crisp white sans-serif text floating in center with smooth upward drift. Ultra high contrast, 4K photorealistic.`
- **On-Screen Text**: `What if unlocking your computer was as simple as looking at it?`
- **Sound Effect**: Deep cinematic sub-bass whoosh.

---

### Scene 2: The Logo Reveal (`0:05 - 0:11`)
- **Voiceover**: *"Introducing Blink. Native Face ID for Linux and Windows."*
- **Attach Asset**: `launch_video_kit/assets/final.png` (or `launch_video_kit/clips/logoanimation.mp4`)
- **Visual Prompt**: `A 3D metallic and neon-cyan abstract eye emblem floats in dark space, glowing with iridescent reflections and subtle light rays. Bold clean headline slides in below. Cinematic Apple Keynote product reveal.`
- **On-Screen Text**: `INTRODUCING BLINK`
- **Sound Effect**: Ethereal glass chime swell + high-frequency shimmer.

---

### Scene 3: The Magic Moment — The Instant Unlock (`0:11 - 0:18`)
- **Voiceover**: *"Just look at your screen. And in two tenths of a second... you're in."*
- **Attach Asset**: `launch_video_kit/screenshots/ad_scene3_unlock.png` (or `launch_video_kit/clips/unlockanimation.mp4`)
- **Visual Prompt**: `Close-up macro shot of a sleek space-gray aluminum laptop on an obsidian desk. Display wakes up with 9:41 lockscreen clock. An Apple Dynamic Island notch springs out from the top display bezel with fluid physics, spins a biometric Face ID scanning ring, flashes a glowing radiant Apple-green checkmark, and unlocks into the desktop. Super smooth, tactile.`
- **On-Screen Text**: `Look. Unlocked. In 0.2s.`
- **Sound Effect**: Display wake chirp + pneumatic notch spring pop + iconic harmonic Apple two-tone unlock chime.

---

### Scene 4: On-Device Neural Vision (`0:18 - 0:26`)
- **Voiceover**: *"Powered by on-device neural vision. Continuous 3D pose tracking, specular liveness verification, and instant biometric confirmation."*
- **Attach Asset**: `launch_video_kit/screenshots/v2_tab_diagnostics_live.png`
- **Visual Prompt**: `Cinematic lateral camera glide across a floating frosted glass computer vision HUD. Holographic cyan 106-point facial mesh tracks face movements in real-time with glowing green bounding brackets and confidence meters. Apple glassmorphism, depth of field.`
- **On-Screen Text**: `512-D ArcFace Biometrics • Active Anti-Spoofing Guard`
- **Sound Effect**: Soft futuristic digital telemetry flutter and radar pulse.

---

### Scene 5: Privacy by Design (`0:26 - 0:33`)
- **Voiceover**: *"And because privacy is built in at the core: your face never leaves your machine. Ever."*
- **Attach Asset**: `launch_video_kit/screenshots/ad_scene5_privacy.png`
- **Visual Prompt**: `A sculpted metallic titanium vault padlock emblem floating above dark reflective water, radiating concentric emerald-green energy ripples. Reassuring, bank-grade security aesthetic.`
- **On-Screen Text**: `100% On-Device • Zero Cloud • AES-256-GCM`
- **Sound Effect**: Solid mechanical deadbolt latch click.

---

### Scene 6: The Glass Experience (`0:33 - 0:38`)
- **Voiceover**: *"Crafted with hardware-blurred glass. Designed for the desktop you love."*
- **Attach Asset**: `launch_video_kit/screenshots/v2_suite_montage.png` (or `v2_tab_face_id.png`)
- **Visual Prompt**: `3D angled floating perspective of dark obsidian glass application windows arranged in a subtle arc. Refined iOS 27 style glass toggles, whole-sensor camera preview, and glowing multi-person biometric tags.`
- **On-Screen Text**: `Apple Glassmorphism • Whole-Sensor Viewfinder`
- **Sound Effect**: Warm analog synth crescendo.

---

### Scene 7: Outro & Call to Action (`0:38 - 0:42`)
- **Voiceover**: *"Blink. Face ID, reimagined. Free and open source."*
- **Attach Asset**: `launch_video_kit/screenshots/ad_scene7_outro.png`
- **Visual Prompt**: `Camera dollies in toward a centered glowing Blink emblem. An Apple-blue pill button appears beneath with the repository link. Screen slowly dissolves to black with ambient cyan fade.`
- **On-Screen Text**: `github.com/realsamay/blink • Free & Open Source`
- **Sound Effect**: Warm cinematic sub-drop and tape delay decay.

---

## 💡 Pro-Tips for Flow AI Success
1. **Always Upload the Reference Images**: Flow AI will try to generate generic stock laptops unless you upload the screenshots from `launch_video_kit/screenshots/`.
2. **Turn Off Generic Stock Footage**: If Flow AI has a toggle for "Stock Footage vs AI Generation", choose **AI Generated** or **Upload Media** so it sticks to the obsidian keynote look.
3. **Lock Voice Pacing**: Ensure the voiceover has breath marks (commas and ellipses `...`) so the speaker pauses when the Dynamic Island expands and unlocks!

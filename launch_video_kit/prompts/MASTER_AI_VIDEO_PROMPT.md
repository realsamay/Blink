# Master AI Video Generation Prompts: "Introducing Blink"

Use these prompts with state-of-the-art AI video tools (**Runway Gen-3 Alpha**, **Luma Dream Machine**, **Kling AI**, **Pika 2.0**, **OpenAI Sora**, or **Haiper**).

---

## 🌟 1. Single All-in-One Master Prompt (For Full Ad Generation)
> **Paste this into Runway Gen-3 / Luma Dream Machine / Kling / Sora:**
>
> ```text
> Cinematic Apple Keynote product commercial, 4K resolution, 60fps. Introducing "Blink" — native Face ID biometric facial recognition for computers. 
> 
> Visual sequence: 
> 1. Opening on a pitch-black studio with dark obsidian reflections and minimalist typography: "What if unlocking your laptop was as simple as looking at it?"
> 2. Slow cinematic camera dolly in towards a floating metallic neon-blue emblem of Blink glowing with subtle volumetric rays, bold typography blooms: "Introducing Blink."
> 3. Cut to a sleek space-gray aluminum laptop on a minimalist walnut desk in a modern architectural studio at dawn. The laptop screen wakes up showing a locked screen with a digital clock. A sleek glossy black Dynamic Island pill notch springs out from the top bezel with fluid liquid-metal physics, rotates an Apple Face ID aperture scanning ring with radiant cyan laser lines. In 0.2 seconds, a radiant emerald green checkmark blooms. The lock icon snaps open, and the screen smoothly reveals the desktop wallpaper. Kinetic text: "Look. Unlocked."
> 4. Camera orbits around floating frosted glass UI windows designed in Apple macOS Sequoia dark glassmorphism. Holographic 3D cyan facial landmark mesh (106 points) tracks a user's face with real-time biometric confidence meters and anti-spoofing specular liveness analysis.
> 5. Macro close-up of a titanium vault shield pulsing with emerald energy: "100% On-Device. Zero Cloud. AES-256 Encrypted."
> 6. Grand pull-out revealing the complete glassmorphic application suite floating in dark cinematic space, settling on a glowing centered Blink logo with link: "github.com/realsamay/blink".
> 
> Aesthetic: Shot on ARRI Alexa 65, anamorphic lens flare, Apple Cupertino keynote lighting, soft volumetric shadows, ultra-photorealistic, high-end commercial grading, 8K render.
> ```

---

## 🎬 2. Shot-by-Shot Prompts (For Scene-by-Scene Generation)

If generating clips scene-by-scene (e.g. 5 to 10-second generations on Runway Gen-3 or Kling):

### Shot 1: The Hook (`0:00 - 0:05`)
- **Prompt**:
  ```text
  Extreme close-up in a pitch-black minimalist Apple studio. A subtle deep sapphire glow pulses in the background. Crisp, ultra-clean white Helvetica typography emerges from the dark with smooth upward kinetic momentum: "What if unlocking your computer was as simple as looking at it?" Slow cinematic push-in. High contrast, studio dark mode, 4k photorealistic.
  ```
- **Motion Settings**: Camera Dolly In (Speed: 2), Pan: 0, Zoom: +1.5.

### Shot 2: The Logo Reveal (`0:05 - 0:11`)
- **Image Prompt / Asset Reference**: Use `assets/final.png` or `assets/logo.png`.
- **Prompt**:
  ```text
  A 3D metallic and neon-glass emblem of an abstract eye and biometric circle floats in a dark void. Volumetric cyan and deep blue light beams illuminate the edges of the logo with realistic reflections and subtle caustics. The emblem pulses gently as bold text slides in beneath: "INTRODUCING BLINK. Native Face ID for your computer." High-end Apple hardware product commercial, 8k, raytracing.
  ```
- **Motion Settings**: Camera Slow Orbit / Dolly Back (Speed: 1.5).

### Shot 3: The Laptop Unlock & Dynamic Island (`0:11 - 0:18`)
- **Image Prompt / Asset Reference**: Use `screenshots/ad_scene3_unlock.png`.
- **Prompt**:
  ```text
  Cinematic macro shot of a sleek space-gray aluminum laptop sitting on a minimalist slate desk. The display wakes up, displaying 9:41 on a locked screen. Smoothly and organically, an obsidian black Dynamic Island notch pops out from the top display bezel with fluid spring elasticity. Inside the notch, a green and white biometric Face ID aperture ring spins smoothly. A radiant Apple-green checkmark flashes inside the notch with a soft sound wave pulse, the lock icon snaps open, and the laptop immediately transitions into an unlocked desktop. Text overlays: "Look. Unlocked. In 0.2 seconds."
  ```
- **Motion Settings**: Static shot transitioning into smooth macro push-in on the notch bezel.

### Shot 4: On-Device Neural Vision Telemetry (`0:18 - 0:26`)
- **Image Prompt / Asset Reference**: Use `screenshots/v2_tab_diagnostics_live.png`.
- **Prompt**:
  ```text
  Smooth cinematic camera pan across a floating frosted obsidian glass application window showing a real-time computer vision diagnostic viewfinder. Holographic 3D cyan dots and vector lines map 106 facial landmark points over a live camera feed. Subtle glowing green HUD brackets frame the face with live match confidence meters (99.8%) and liveness telemetry. Sleek glass badges slide in on the right: "512-D ArcFace Biometrics", "Active Anti-Spoofing Guard". Futuristic yet minimal Apple design, soft neon glow, depth of field.
  ```
- **Motion Settings**: Smooth lateral camera truck right (Speed: 2), slight tilt up.

### Shot 5: Privacy by Design — 100% On-Device (`0:26 - 0:33`)
- **Image Prompt / Asset Reference**: Use `screenshots/ad_scene5_privacy.png`.
- **Prompt**:
  ```text
  Close-up of a sculpted titanium and emerald-green security shield floating above dark reflective water. A radiant green circular pulse ripples outward through the glass rings. Bold kinetic typography: "Your face is yours alone. 100% On-Device. Zero Cloud." Text drifts gently with soft optical defocus in the background. Ultra-secure, high-tech, reassuring, photorealistic commercial lighting.
  ```
- **Motion Settings**: Slow camera zoom in, subtle counter-clockwise rotation.

### Shot 6: The Glass Experience (`0:33 - 0:38`)
- **Image Prompt / Asset Reference**: Use `screenshots/v2_tab_face_id_multi_match.png` or `screenshots/v2_suite_montage.png`.
- **Prompt**:
  ```text
  Cinematic wide 3D perspective of multiple floating glass application windows arranged in an elegant arc against a deep space background. The windows feature hardware-blurred obsidian dark glass, refined iOS 27 toggles, and live camera viewfinders with glowing multi-face green recognition tags. Soft ambient reflections on the glass surface. Text: "Designed with obsessive care. Apple Glassmorphism."
  ```
- **Motion Settings**: Smooth crane pan up and pull back, revealing the full desktop ecosystem.

### Shot 7: Grand Finale & Outro (`0:38 - 0:42`)
- **Image Prompt / Asset Reference**: Use `screenshots/ad_scene7_outro.png` or `assets/final.png`.
- **Prompt**:
  ```text
  The floating windows dissolve smoothly into a focused center composition. The luminous Blink emblem stands in center with a glowing Apple-blue pill button beneath reading "github.com/realsamay/blink". A final soft bloom pulses across the screen. Text: "Blink. Face ID, reimagined for your computer. Free. Open Source. Private." Screen softly fades to pitch black.
  ```
- **Motion Settings**: Smooth slow push in to center emblem.

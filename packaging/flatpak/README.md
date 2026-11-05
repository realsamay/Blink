# Flathub / Flatpak Packaging for Blink

This directory contains the complete packaging suite for distributing **Blink** on [Flathub](https://flathub.org).

## Files
- `io.github.realsamay.Blink.yml`: The Flathub Flatpak build manifest.
- `io.github.realsamay.Blink.metainfo.xml`: AppStream metadata, search keywords, and description.
- `io.github.realsamay.Blink.desktop`: XDG Desktop entry.

## Submitting to Flathub
1. Fork the official Flathub submission repository: [https://github.com/flathub/flathub](https://github.com/flathub/flathub).
2. Create a new branch named `io.github.realsamay.Blink`.
3. Add `io.github.realsamay.Blink.yml` to the root of the branch.
4. Submit a Pull Request to `flathub/flathub`.
5. The Flathub bot will run test builds and create your dedicated Flathub repository upon merge!

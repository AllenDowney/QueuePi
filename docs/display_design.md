# How should the customer display reach the HDMI monitor on Raspberry Pi OS Lite?

*A design question. Drafted 2026-11-03; to be settled by the spike in Task 3.*

## Background

QueuePi's customer display is a Python program that polls the local server and draws a full-screen 1920×1080 board with Pygame. On the Pi it has to run with no desktop environment and no Chromium, start at boot from systemd, and come back by itself if it crashes or the monitor is unplugged and replugged. On a development desktop it runs in a window.

The brief asks us not to assume the old framebuffer techniques still work, and to consider whether a small Wayland compositor would be simpler than SDL on bare DRM/KMS.

## What changed in the Pi graphics stack

*To verify on current hardware and the current Pi OS release; this section is from prior knowledge, not yet tested.*

- Pi OS uses the KMS driver (`vc4-kms-v3d`) by default, since Bullseye. The old firmware framebuffer (`dispmanx`) and the legacy `fkms` driver are gone. `/dev/fb0` still exists, as DRM's fbdev emulation for the console, but it's not the intended path for applications.
- Pygame 2 is built on SDL2, and **SDL2 has no `fbcon` driver** (that was SDL 1.2). The old recipe `SDL_VIDEODRIVER=fbcon` doesn't work.
- SDL2 has a **`kmsdrm`** video driver that draws directly through DRM/KMS with GBM and EGL. It needs DRM master on the card (no other compositor running) and access to `/dev/dri/card*` (user in the `video` group, or `render` for the render node).
- On a Pi 4 there are two DRM devices: `v3d` (render only) and `vc4` (display). Which is `card0` and which `card1` has varied between releases. SDL may need `SDL_VIDEO_KMSDRM_DEVICE_INDEX` or similar to pick the one with the HDMI connector.
- Desktop Pi OS now uses a Wayland compositor (labwc, previously Wayfire). Lite has none.

## Options

### A. Pygame on SDL `kmsdrm`, no compositor

One process, launched by systemd, `SDL_VIDEODRIVER=kmsdrm`.

- **For:** the fewest moving parts. Nothing to install beyond Pygame. Lowest memory.
- **Against:**
  - Depends on how the SDL2 in use was built. Debian's `libsdl2` (and so `python3-pygame` from apt) includes `kmsdrm`. **PyPI Pygame wheels bundle their own SDL2, which may not**; pygame-ce wheels likewise. Needs checking. If not, we use the apt package, which pins the Pygame version to Debian's.
  - Hotplug: if the monitor is off or unplugged at boot, KMS may report no connected display and SDL fails to start. systemd restarts it until the monitor appears, which is probably fine, but needs testing. Mode selection when the monitor reports something odd may need `video=` on the kernel command line.
  - systemd unit needs the right groups, `TTYPath`/VT handling, and sometimes a seat (logind) to get DRM master. Known to be fiddly.
  - Text rendering cost: `pygame.font` on the Pi's CPU at 1080p is fine for redraws every second, but full-screen redraws at 60 fps aren't needed and shouldn't be attempted. Redraw only on change or once a second.

### B. Pygame under `cage` (kiosk Wayland compositor)

systemd starts `cage -- python -m queuepi.display`. Cage runs one full-screen application and nothing else. SDL uses its Wayland driver.

- **For:**
  - Cage (wlroots) handles DRM master, mode setting, seat, and hotplug, which are the fiddly parts of A. It's packaged in Debian (`apt install cage`).
  - The same setup would run any other kiosk client later (including a browser, if we ever changed our minds, though we don't plan to).
  - SDL's Wayland support is in the wheels and in Debian's build.
- **Against:**
  - One more package and one more process (a few tens of MB; fine on 1 GB).
  - wlroots versions and Pi GPU quirks; needs testing on the real hardware.
  - Cage needs a logind seat or `seatd`; a `cage@.service` pattern with `PAMName=login` and `TTYPath=/dev/tty1` is the usual recipe.

### C. Render to the framebuffer directly (no SDL video)

Draw with Pygame to an offscreen `Surface` and copy the pixels to `/dev/fb0` ourselves.

- **For:** no DRM master, no GBM/EGL. Works as long as fbdev emulation exists.
- **Against:** exactly the "old framebuffer technique" the brief warns about. fbdev emulation is a compatibility layer and its mode is whatever the console chose. Tearing, no vsync. Keep only as an emergency fallback.

### D. Browser kiosk

Excluded by the brief (no Chromium). Listed only for comparison: it would let the display reuse the employee pages' HTML and CSS, but costs far more RAM and has more moving parts.

## Recommendation (tentative)

Write the display so it doesn't care: a plain Pygame program that takes `--windowed` or full screen and lets SDL choose its driver from the environment. Then on the Pi:

1. Try **A** first (`python3-pygame` from apt, `SDL_VIDEODRIVER=kmsdrm`). If it starts reliably from systemd, survives a monitor unplug/replug and a cold boot with the monitor off, use it.
2. If any of those is fragile, use **B**. The code doesn't change; only the systemd unit does.

Both units go in `deploy/systemd/`, with the one we pick as the default.

## Spike checklist (Task 3)

- [ ] Fresh Pi OS Lite 64-bit on a Pi 4 (1 GB if available); record the release and kernel
- [ ] `ls -l /dev/dri/`, `cat /sys/class/drm/*/status`: which card has HDMI
- [ ] Minimal Pygame script (full-screen, big text, redraw once a second) with apt `python3-pygame`, `SDL_VIDEODRIVER=kmsdrm`, run from the console
- [ ] Same with a venv and PyPI `pygame` / `pygame-ce` wheels: does `kmsdrm` exist? (`pygame.display.get_driver()`)
- [ ] Same script under `cage`
- [ ] Each from a systemd unit at boot, as a non-root user
- [ ] Boot with the monitor off; unplug and replug while running; pull the power
- [ ] Memory (`free`, `ps`) and CPU at idle and on change; temperature in the fanless case after an hour
- [ ] Font rendering at the target sizes: readable from 3–5 m?
- [ ] Record results here and the decision in `docs/plan.md`

## Sources to check

- SDL2 `kmsdrm` driver documentation and hints (`SDL_hints.h`, `README-kmsbsd`/`README-linux`)
- Raspberry Pi documentation on KMS and the display stack
- Cage README and example systemd units
- Pygame / pygame-ce issues about `kmsdrm` on Raspberry Pi

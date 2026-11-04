"""Draw the home-screen icons (src/queuepi/static/icon-*.png) with Pygame.

The icon echoes the display's example receipt: a highlighted "47".
Run again only if the design changes; the PNGs are committed.
"""

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

STATIC = Path(__file__).resolve().parent.parent / "src" / "queuepi" / "static"


def icon(size: int) -> pygame.Surface:
    s = pygame.Surface((size, size))
    s.fill((17, 20, 24))
    box = pygame.Rect(0, 0, int(size * 0.74), int(size * 0.5))
    box.center = (size // 2, size // 2)
    pygame.draw.rect(s, (255, 200, 60), box, border_radius=size // 12)
    font = pygame.font.Font(None, int(size * 0.5))
    text = font.render("47", True, (25, 25, 25))
    s.blit(text, text.get_rect(center=(size // 2, size // 2 + size // 40)))
    return s


if __name__ == "__main__":
    pygame.init()
    for size in (192, 512):
        pygame.image.save(icon(size), STATIC / f"icon-{size}.png")
        print(f"wrote icon-{size}.png")

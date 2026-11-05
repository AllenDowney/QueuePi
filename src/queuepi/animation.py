"""The "order ready" animation on the customer display.

When the kitchen marks an order ready, its number lifts out of the Waiting
panel and grows into the middle of the screen, confetti bursts, it holds for
a moment, then it shrinks into its place in the Ready panel. Meanwhile the
header says "Ready!". Orders marked ready together move together.

This module is only timing, geometry and confetti physics, with no Pygame,
so it can be tested without a window; display.py draws what it computes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Timeline, in seconds from the start.
GROW = 0.7  # from the Waiting panel to the middle, growing
HOLD = 1.0  # in the middle; confetti bursts at the start
FLY = 0.7  # into the Ready panel, shrinking
CONFETTI_LIFE = 1.6
TOTAL = max(GROW + HOLD + FLY, GROW + CONFETTI_LIFE)

DIM_ALPHA = 170  # how much the lists fade behind the big numbers (0-255)
MAX_CENTER_SIZE = 620  # font size of a lone number in the middle

Point = tuple[float, float]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out(t: float) -> float:
    """Fast start, gentle stop."""
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def clamp01(t: float) -> float:
    return min(1.0, max(0.0, t))


def center_layout(count: int, area: tuple[int, int, int, int]) -> tuple[list[Point], int]:
    """Where `count` numbers sit in the middle of `area` (left, top, width,
    height): side by side, as big as fits. Returns the centers and font size."""
    left, top, width, height = area
    slot = (width - 120) / count
    # Two digits are about 1.1 em wide; leave a gap between numbers.
    size = int(min(MAX_CENTER_SIZE, height * 0.8, slot * 0.8 / 1.1))
    cy = top + height / 2
    centers = [(left + 60 + slot * (i + 0.5), cy) for i in range(count)]
    return centers, size


# --- which orders just became ready ---------------------------------------------


class ReadyWatcher:
    """Spots orders that became ready since the last board.

    The first board after starting or reconnecting only sets the baseline,
    so orders that were already ready don't all celebrate at once. An order
    sent back to waiting (undo) and marked ready again celebrates again.
    """

    def __init__(self) -> None:
        self.ready_ids: set[int] | None = None

    def update(self, board: dict | None, connected: bool) -> list[str]:
        if board is None or not connected:
            self.ready_ids = None
            return []
        ids = {o["id"] for o in board["ready"]}
        if self.ready_ids is None:
            self.ready_ids = ids
            return []
        new = [o["number"] for o in board["ready"] if o["id"] not in self.ready_ids]
        self.ready_ids = ids
        return new


# --- confetti -------------------------------------------------------------------


GRAVITY = 1400.0  # pixels per second squared
DRAG = 1.2  # per second


@dataclass(frozen=True)
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    angle: float
    spin: float
    color: int  # index into the display's confetti colors
    w: float
    h: float

    def at(self, t: float) -> tuple[float, float, float]:
        """Position and angle `t` seconds after the burst. With linear drag,
        velocity decays as exp(-DRAG t); gravity pulls down throughout."""
        decay = (1 - math.exp(-DRAG * t)) / DRAG
        x = self.x + self.vx * decay
        y = self.y + self.vy * decay + GRAVITY * (t / DRAG - decay / DRAG)
        return x, y, self.angle + self.spin * t


def make_confetti(centers: list[Point], size: int, rng: random.Random,
                  colors: int, per_number: int = 80) -> list[Particle]:
    """A burst from around each number, mostly upward and outward."""
    per_number = min(per_number, 240 // max(1, len(centers)))
    out = []
    for cx, cy in centers:
        for _ in range(per_number):
            a = rng.uniform(-math.pi * 0.95, -math.pi * 0.05)  # upward half
            speed = rng.uniform(500, 1500)
            out.append(Particle(
                x=cx + rng.uniform(-0.3, 0.3) * size,
                y=cy + rng.uniform(-0.2, 0.2) * size,
                vx=math.cos(a) * speed,
                vy=math.sin(a) * speed,
                angle=rng.uniform(0, 2 * math.pi),
                spin=rng.uniform(-12, 12),
                color=rng.randrange(colors),
                w=rng.uniform(10, 22),
                h=rng.uniform(6, 12),
            ))
    return out


# --- one celebration --------------------------------------------------------------


@dataclass
class Flight:
    number: str
    start: Point
    start_size: int
    center: Point
    center_size: int

    def at(self, t: float, end: Point, end_size: int) -> tuple[float, float, int]:
        """Center and font size `t` seconds in. `end` is looked up each frame,
        since the Ready panel can change while the animation runs."""
        if t < GROW:
            k = ease_out(clamp01(t / GROW))
            a, b, sa, sb = self.start, self.center, self.start_size, self.center_size
        elif t < GROW + HOLD:
            # A slow swell while it holds, gently drawing the eye.
            k = (t - GROW) / HOLD
            swell = 1 + 0.04 * math.sin(math.pi * k)
            return self.center[0], self.center[1], int(self.center_size * swell)
        else:
            k = ease_in_out(clamp01((t - GROW - HOLD) / FLY))
            a, b, sa, sb = self.center, end, self.center_size, end_size
        return lerp(a[0], b[0], k), lerp(a[1], b[1], k), int(lerp(sa, sb, k))


@dataclass
class Celebration:
    start_time: float
    flights: list[Flight]
    confetti: list[Particle] = field(default_factory=list)

    @property
    def numbers(self) -> list[str]:
        return [f.number for f in self.flights]

    def elapsed(self, now: float) -> float:
        return now - self.start_time

    def done(self, now: float) -> bool:
        return self.elapsed(now) >= TOTAL

    def landed(self, now: float) -> bool:
        """The numbers have reached the Ready panel."""
        return self.elapsed(now) >= GROW + HOLD + FLY

    def dim(self, now: float) -> int:
        """How much to fade the lists behind the numbers."""
        t = self.elapsed(now)
        if t < GROW:
            return int(DIM_ALPHA * ease_out(t / GROW))
        if t < GROW + HOLD:
            return DIM_ALPHA
        return int(DIM_ALPHA * (1 - clamp01((t - GROW - HOLD) / FLY)))

    def confetti_time(self, now: float) -> float | None:
        """Seconds since the burst, or None before it or after it's over."""
        t = self.elapsed(now) - GROW
        return t if 0 <= t < CONFETTI_LIFE else None


def plan(numbers: list[str], starts: dict[str, tuple[Point, int]], fallback: tuple[Point, int],
         area: tuple[int, int, int, int], now: float, rng: random.Random,
         colors: int) -> Celebration:
    """Set up a celebration for `numbers`. `starts` maps numbers to where
    they were drawn in the Waiting panel (center, font size); numbers not
    shown there (another page, or never seen) start from `fallback`."""
    centers, size = center_layout(len(numbers), area)
    flights = [
        Flight(n, *starts.get(n, fallback), center=c, center_size=size)
        for n, c in zip(numbers, centers)
    ]
    return Celebration(now, flights, make_confetti(centers, size, rng, colors))

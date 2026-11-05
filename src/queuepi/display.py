"""The customer display: a full-screen board drawn with Pygame.

It's an ordinary client of the server. A background thread polls
`GET /api/board` once a second; the main loop draws on a 1920x1080 canvas and
scales it to the window or screen. It never touches the database.

If the server stops answering, the display says so: after a few seconds a
warning banner, and after longer the order lists are hidden rather than shown
out of date.

    queuepi-display                       # full screen on display 0
    queuepi-display --windowed            # a 1280x720 window, resizable
    queuepi-display --display 1           # full screen on a second monitor
    queuepi-display --screenshot out.png  # draw one frame to a file and exit
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import animation

if TYPE_CHECKING:
    import pygame

WIDTH, HEIGHT = 1920, 1080

POLL_SECONDS = 1.0
STALE_SECONDS = 5.0  # show a warning banner
LOST_SECONDS = 30.0  # hide the lists
PAGE_SECONDS = 6.0  # time per page when a list overflows

EXAMPLE_RECEIPT = ("Receipt #103", "47")

# Grid sizes to try, largest numbers first: (columns, rows). If even the
# last one can't fit every number, the list is split into pages.
READY_GRIDS = [(2, 2), (2, 3), (3, 4)]
WAITING_GRIDS = [(4, 4), (5, 5), (6, 5)]

# Colors
# A palette from the shop's own colors: logo green and gold, warm greys.
# The staff pages (staff.css) use the same colors.
BG = (22, 20, 18)
HEADER_BG = (38, 35, 32)
TEXT = (244, 242, 241)
MUTED = (168, 160, 148)
WAITING_BG = (46, 38, 20)
WAITING_NUM = (242, 197, 74)  # logo gold, a little lighter
READY_BG = (0, 58, 42)
READY_NUM = (96, 214, 162)  # logo green, lightened to read on dark
PAPER = (244, 242, 241)
INK = (25, 25, 25)
HIGHLIGHT = (238, 177, 17)  # logo gold
PLUM_LIGHT = (190, 165, 205)  # the staff pages' plum, lightened
CONFETTI_COLORS = [HIGHLIGHT, READY_NUM, PLUM_LIGHT, PAPER]
WARN_BG = (176, 0, 32)


# --- what to show (no Pygame here, so it's easy to test) ---------------------


def sort_waiting(numbers: list[str]) -> list[str]:
    """Waiting numbers in numeric order, so customers can find theirs and the
    board doesn't suggest orders are made first come, first served."""
    return sorted(numbers, key=int)


@dataclass(frozen=True)
class Grid:
    cols: int
    rows: int
    pages: int
    per_page: int  # numbers on each page (the last page may have fewer)


def choose_grid(n: int, options: list[tuple[int, int]]) -> Grid:
    """How to lay out n numbers. Use the first (largest-number) grid that
    holds them all. If none does, split them evenly into as few pages as the
    last grid allows, then use the largest grid that holds one page."""
    pages = max(1, math.ceil(n / (options[-1][0] * options[-1][1])))
    per_page = math.ceil(n / pages)
    for cols, rows in options:
        if per_page <= cols * rows:
            return Grid(cols, rows, pages, per_page if pages > 1 else cols * rows)
    raise AssertionError("unreachable")


def page_of(items: list[str], grid: Grid, now: float) -> tuple[list[str], int]:
    """The items on the page showing at time `now`, and that page's index."""
    page = int(now // PAGE_SECONDS) % grid.pages
    start = page * grid.per_page
    return items[start : start + grid.per_page], page


def connection_state(last_ok: float | None, now: float) -> str:
    """'connecting' (never reached the server), 'ok', 'stale', or 'lost'."""
    if last_ok is None:
        return "connecting"
    age = now - last_ok
    if age > LOST_SECONDS:
        return "lost"
    if age > STALE_SECONDS:
        return "stale"
    return "ok"


@dataclass(frozen=True)
class Panel:
    numbers: tuple[str, ...]
    grid: Grid
    page: int


@dataclass(frozen=True)
class View:
    """Everything one frame shows. Two equal Views draw the same picture, so
    the main loop redraws only when the View changes."""

    state: str
    ready: Panel
    waiting: Panel
    join_url: str | None
    size: tuple[int, int] = (WIDTH, HEIGHT)


def make_view(board: dict | None, last_ok: float | None, now: float) -> View:
    state = connection_state(last_ok, now)
    if board is None or state in ("connecting", "lost"):
        ready_numbers: list[str] = []
        waiting_numbers: list[str] = []
    else:
        ready_numbers = [o["number"] for o in board["ready"]]
        waiting_numbers = sort_waiting([o["number"] for o in board["waiting"]])

    def panel(numbers: list[str], options: list[tuple[int, int]]) -> Panel:
        grid = choose_grid(len(numbers), options)
        shown, page = page_of(numbers, grid, now)
        return Panel(tuple(shown), grid, page)

    return View(
        state=state,
        ready=panel(ready_numbers, READY_GRIDS),
        waiting=panel(waiting_numbers, WAITING_GRIDS),
        join_url=board.get("join_url") if board else None,
    )


# --- layout --------------------------------------------------------------------

HEADER_HEIGHT = 250
# (left, top, width, height) on the 1920x1080 canvas.
WAITING_RECT = (40, 280, 950, 750)
READY_RECT = (1020, 280, 860, 750)
BELOW_HEADER = (0, HEADER_HEIGHT, WIDTH, HEIGHT - HEADER_HEIGHT)
WAITING_MAX_FONT = 200
READY_MAX_FONT = 300


def panel_area(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """The part of a panel where numbers go, under its title."""
    left, top, width, height = rect
    return left + 20, top + 130, width - 40, height - 150


def panel_cells(rect, panel: Panel, max_font: int) -> list[tuple[str, tuple[float, float], int]]:
    """Where each number on the panel's current page is drawn:
    (number, center, font size)."""
    left, top, width, height = panel_area(rect)
    g = panel.grid
    cell_w, cell_h = width / g.cols, height / g.rows
    # Two digits are about 1.1 em wide in this font.
    size = int(min(cell_h * 0.8, cell_w * 0.85 / 1.1, max_font))
    cells = []
    for i, number in enumerate(panel.numbers):
        r, col = divmod(i, g.cols)
        cells.append((number, (left + cell_w * (col + 0.5), top + cell_h * (r + 0.5)), size))
    return cells


# --- polling -----------------------------------------------------------------


class Poller(threading.Thread):
    """Fetches the board in the background so a slow server never freezes
    the screen."""

    def __init__(self, server: str, interval: float = POLL_SECONDS):
        super().__init__(daemon=True)
        self.url = server.rstrip("/") + "/api/board"
        self.interval = interval
        self.lock = threading.Lock()
        self.board: dict | None = None
        self.last_ok: float | None = None
        self.stopping = threading.Event()
        self.logo_url = server.rstrip("/") + "/api/logo"
        self.logo: tuple[str, bytes] | None = None  # (version, PNG bytes)

    def fetch_once(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=2.0) as r:
                board = json.load(r)
        except (OSError, ValueError):
            return  # keep the last board; staleness shows on screen
        with self.lock:
            self.board = board
            self.last_ok = time.monotonic()
        self.fetch_logo(board.get("logo"))

    def fetch_logo(self, version: str | None) -> None:
        """Fetch the logo when the server has a new one; forget it if gone."""
        if version is None:
            self.logo = None
            return
        if self.logo and self.logo[0] == version:
            return
        try:
            with urllib.request.urlopen(self.logo_url, timeout=5.0) as r:
                self.logo = (version, r.read())
        except OSError:
            pass  # try again next poll

    def run(self) -> None:
        while not self.stopping.is_set():
            self.fetch_once()
            self.stopping.wait(self.interval)

    def snapshot(self) -> tuple[dict | None, float | None]:
        with self.lock:
            return self.board, self.last_ok


# --- drawing -----------------------------------------------------------------


class Renderer:
    def __init__(self) -> None:
        import pygame

        self.pg = pygame
        self.canvas = pygame.Surface((WIDTH, HEIGHT))
        self.fonts: dict[int, pygame.font.Font] = {}
        self.qr_cache: tuple[str, int, pygame.Surface] | None = None
        self.logo: pygame.Surface | None = None
        self.big_cache: dict[str, pygame.Surface] = {}

    def set_logo(self, png: bytes | None) -> None:
        """Use these PNG bytes as the shop logo (None: no logo)."""
        self.logo = None
        if png:
            import io

            try:
                img = self.pg.image.load(io.BytesIO(png), "logo.png")
                # Faster to draw once converted, which needs a window.
                self.logo = img.convert_alpha() if self.pg.display.get_surface() else img
            except self.pg.error:
                pass  # a broken file: carry on without it

    def font(self, size: int) -> pygame.font.Font:
        if size not in self.fonts:
            # Pygame's bundled font (FreeSans Bold), the same on every machine.
            self.fonts[size] = self.pg.font.Font(None, size)
        return self.fonts[size]

    def text(self, s: str, size: int, color, **pos) -> pygame.Rect:
        surf = self.font(size).render(s, True, color)
        rect = surf.get_rect(**pos)
        self.canvas.blit(surf, rect)
        return rect

    def fitted_size(self, s: str, max_width: int, size: int) -> int:
        while size > 12 and self.font(size).size(s)[0] > max_width:
            size -= 2
        return size

    def qr(self, url: str, size: int) -> pygame.Surface:
        if self.qr_cache and self.qr_cache[:2] == (url, size):
            return self.qr_cache[2]
        import segno

        rows = list(segno.make(url, error="m").matrix_iter(border=2))
        n = len(rows)
        small = self.pg.Surface((n, n))
        small.fill((255, 255, 255))
        for y, row in enumerate(rows):
            for x, dark in enumerate(row):
                if dark:
                    small.set_at((x, y), (0, 0, 0))
        scale = size // n
        surf = self.pg.transform.scale(small, (n * scale, n * scale))  # crisp, no smoothing
        self.qr_cache = (url, size, surf)
        return surf

    def draw(self, view: View, celebration: animation.Celebration | None = None,
             now: float = 0.0, hide: frozenset[str] = frozenset()) -> pygame.Surface:
        """Draw one frame. During a celebration, the celebrating numbers fly
        over the lists; `hide` keeps numbers out of the Ready panel until
        they land there (including ones waiting for their turn)."""
        pg = self.pg
        c = self.canvas
        c.fill(BG)
        pg.draw.rect(c, HEADER_BG, (0, 0, WIDTH, HEADER_HEIGHT))
        if celebration:
            self.text("Ready!", 190, READY_NUM, center=(WIDTH // 2, HEADER_HEIGHT // 2 + 8))
        else:
            self.draw_header(view)

        self.draw_panel(pg.Rect(WAITING_RECT), WAITING_BG, "Waiting", WAITING_NUM, view.waiting, view,
                        max_font=WAITING_MAX_FONT)
        self.draw_panel(pg.Rect(READY_RECT), READY_BG, "Ready for pickup", READY_NUM, view.ready, view,
                        max_font=READY_MAX_FONT, hide=hide)
        if celebration:
            self.draw_celebration(view, celebration, now)

        # Connection warnings.
        if view.state == "stale":
            band = pg.Rect(0, HEIGHT - 70, WIDTH, 70)
            pg.draw.rect(c, WARN_BG, band)
            self.text(
                "Lost connection to the order system. This list may be out of date.",
                44, TEXT, center=band.center,
            )
        return c

    def draw_header(self, view: View) -> None:
        """How to find your number, an example receipt, the join code."""
        pg = self.pg
        left = 60
        if self.logo:
            card = pg.Rect(30, 25, 330, 200)
            self.draw_logo(card)
            left = card.right + 40
        lines = ["Your order number is the", "last two digits of your receipt."]
        size = min(self.fitted_size(line, 1120 - left, 76) for line in lines)
        top = 125 - int(size * 1.05)
        self.text(lines[0], size, TEXT, topleft=(left, top))
        self.text(lines[1], size, TEXT, topleft=(left, top + int(size * 1.1)))
        self.draw_receipt(pg.Rect(1170, 45, 470, 160))
        if view.join_url:
            self.draw_join(view.join_url, pg.Rect(1690, 18, 200, 232))

    def draw_celebration(self, view: View, cel: animation.Celebration, now: float) -> None:
        pg = self.pg
        t = cel.elapsed(now)
        dim = cel.dim(now)
        if dim:
            veil = pg.Surface(BELOW_HEADER[2:], pg.SRCALPHA)
            veil.fill((*BG, dim))
            self.canvas.blit(veil, BELOW_HEADER[:2])

        # Where each number will land in the Ready panel (it may be on
        # another page; then it heads for the middle of the panel).
        targets = {n: (c, size) for n, c, size in panel_cells(READY_RECT, view.ready, READY_MAX_FONT)}
        rx, ry, rw, rh = READY_RECT
        fallback = ((rx + rw / 2, ry + rh / 2), 100)
        if not cel.landed(now):
            for f in cel.flights:
                end, end_size = targets.get(f.number, fallback)
                x, y, size = f.at(t, end, end_size)
                img = self.big_number(f.number)
                scale = size / animation.MAX_CENTER_SIZE
                w, h = img.get_size()
                scaled = pg.transform.smoothscale(img, (max(1, int(w * scale)), max(1, int(h * scale))))
                self.canvas.blit(scaled, scaled.get_rect(center=(int(x), int(y))))

        ct = cel.confetti_time(now)
        if ct is not None:
            # Pieces shrink away over the last half second.
            fade = min(1.0, (animation.CONFETTI_LIFE - ct) / 0.5)
            for p in cel.confetti:
                x, y, a = p.at(ct)
                if y > HEIGHT + 40:
                    continue
                hw, hh = p.w * fade / 2, p.h * fade / 2
                ca, sa = math.cos(a), math.sin(a)
                corners = [(x + dx * ca - dy * sa, y + dx * sa + dy * ca)
                           for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
                pg.draw.polygon(self.canvas, CONFETTI_COLORS[p.color], corners)

    def big_number(self, number: str) -> pygame.Surface:
        """A number rendered once at full celebration size, scaled per frame."""
        if number not in self.big_cache:
            self.big_cache[number] = self.font(animation.MAX_CENTER_SIZE).render(number, True, READY_NUM)
        return self.big_cache[number]

    def draw_receipt(self, rect: pygame.Rect) -> None:
        pg = self.pg
        pg.draw.rect(self.canvas, PAPER, rect, border_radius=8)
        self.text("For example:", 30, MUTED, bottomleft=(rect.left, rect.top - 6))
        prefix, last2 = EXAMPLE_RECEIPT
        f = self.font(84)
        w1, h = f.size(prefix)
        w2, _ = f.size(last2)
        x = rect.centerx - (w1 + w2 + 16) // 2
        y = rect.centery - h // 2 + 6
        self.text(prefix, 84, INK, topleft=(x, y))
        box = pg.Rect(x + w1 + 4, y - 10, w2 + 16, h + 12)
        pg.draw.rect(self.canvas, HIGHLIGHT, box, border_radius=8)
        self.text(last2, 84, INK, topleft=(box.left + 8, y))

    def draw_logo(self, card: pygame.Rect) -> None:
        """The shop's logo on a light card (logos are made for white paper)."""
        pg = self.pg
        pg.draw.rect(self.canvas, PAPER, card, border_radius=12)
        w, h = self.logo.get_size()
        scale = min((card.width - 30) / w, (card.height - 30) / h)
        img = pg.transform.smoothscale(self.logo, (int(w * scale), int(h * scale)))
        self.canvas.blit(img, img.get_rect(center=card.center))

    def draw_join(self, url: str, rect: pygame.Rect) -> None:
        qr = self.qr(url, 190)
        self.canvas.blit(qr, qr.get_rect(midtop=(rect.centerx, rect.top)))
        self.text("Staff", 26, MUTED, midtop=(rect.centerx, rect.top + qr.get_height() + 4))

    def draw_panel(self, rect, bg, title, color, panel: Panel, view: View, max_font: int,
                   hide: frozenset[str] = frozenset()) -> None:
        pg = self.pg
        pg.draw.rect(self.canvas, bg, rect, border_radius=24)
        self.text(title, 80, color, topleft=(rect.left + 40, rect.top + 30))
        if panel.grid.pages > 1:
            self.text(
                f"{panel.page + 1} / {panel.grid.pages}", 48, MUTED,
                topright=(rect.right - 40, rect.top + 42),
            )

        area = pg.Rect(panel_area(tuple(rect)))
        if view.state in ("connecting", "lost"):
            msg = "Connecting…" if view.state == "connecting" else "Reconnecting…"
            self.text(msg, 64, MUTED, center=area.center)
            return

        for number, center, size in panel_cells(tuple(rect), panel, max_font):
            if number not in hide:
                self.text(number, size, color, center=center)


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="QueuePi customer display")
    ap.add_argument("--server", default="http://127.0.0.1:8000", help="QueuePi server URL")
    ap.add_argument("--windowed", action="store_true", help="run in a window instead of full screen")
    ap.add_argument("--display", type=int, default=0, help="which monitor to use for full screen")
    ap.add_argument("--size", default="1280x720", help="window size with --windowed")
    ap.add_argument("--screenshot", metavar="PNG", help="draw one frame to a file and exit")
    ap.add_argument("--no-animation", action="store_true",
                    help="don't celebrate orders becoming ready")
    args = ap.parse_args(argv)

    import os

    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if args.screenshot:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import warnings

    # conda-forge's pygame warns that it wasn't built for AVX2; harmless.
    warnings.filterwarnings("ignore", message=".*avx2", category=RuntimeWarning)
    import pygame

    pygame.init()
    poller = Poller(args.server)

    if args.screenshot:
        poller.fetch_once()
        board, last_ok = poller.snapshot()
        renderer = Renderer()
        renderer.set_logo(poller.logo[1] if poller.logo else None)
        pygame.image.save(renderer.draw(make_view(board, last_ok, time.monotonic())), args.screenshot)
        return

    if args.windowed:
        w, h = (int(x) for x in args.size.lower().split("x"))
        screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
    else:
        monitors = pygame.display.get_num_displays()
        display = args.display
        if not 0 <= display < monitors:
            display = monitors - 1
            print(f"queuepi-display: no monitor {args.display} ({monitors} connected); "
                  f"using monitor {display}", file=sys.stderr)
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN, display=display)
        pygame.mouse.set_visible(False)
    pygame.display.set_caption("QueuePi")

    renderer = Renderer()
    poller.start()
    clock = pygame.time.Clock()
    last_view: View | None = None  # the last view drawn; None forces a redraw
    prev_view: View | None = None  # the view one loop ago, for where numbers were
    last_draw = 0.0
    logo_version: str | None = None
    watcher = animation.ReadyWatcher()
    rng = random.Random()
    celebration: animation.Celebration | None = None
    queued: list[tuple[list[str], dict]] = []  # batches waiting their turn

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                return
            if event.type in (pygame.VIDEORESIZE, pygame.WINDOWEXPOSED):
                last_view = None

        now = time.monotonic()
        logo = poller.logo
        if (logo[0] if logo else None) != logo_version:
            logo_version = logo[0] if logo else None
            renderer.set_logo(logo[1] if logo else None)
            last_view = None
        board, last_ok = poller.snapshot()
        view = make_view(board, last_ok, now)
        view = View(view.state, view.ready, view.waiting, view.join_url, screen.get_size())

        # Orders that just became ready get a celebration, one batch at a time.
        newly_ready = watcher.update(board, view.state in ("ok", "stale"))
        if newly_ready and not args.no_animation:
            source = prev_view.waiting if prev_view else view.waiting
            starts = {n: (c, size) for n, c, size in panel_cells(WAITING_RECT, source, WAITING_MAX_FONT)}
            queued.append((newly_ready, starts))
        if celebration and celebration.done(now):
            celebration = None
            last_view = None
        if celebration is None and queued:
            numbers, starts = queued.pop(0)
            wx, wy, ww, wh = WAITING_RECT
            celebration = animation.plan(
                numbers, starts, ((wx + ww / 2, wy + wh / 2), WAITING_MAX_FONT // 2),
                BELOW_HEADER, now, rng, len(CONFETTI_COLORS),
            )
        hide = frozenset(n for batch, _ in queued for n in batch)
        if celebration and not celebration.landed(now):
            hide |= frozenset(celebration.numbers)
        prev_view = view

        # Redraw when something changed, and every few seconds regardless;
        # every frame while celebrating.
        if celebration or view != last_view or now - last_draw > 5:
            canvas = renderer.draw(view, celebration, now, hide)
            sw, sh = screen.get_size()
            if (sw, sh) == (WIDTH, HEIGHT):
                screen.blit(canvas, (0, 0))
            else:
                scale = min(sw / WIDTH, sh / HEIGHT)
                scaled = pygame.transform.smoothscale(canvas, (int(WIDTH * scale), int(HEIGHT * scale)))
                screen.fill((0, 0, 0))
                screen.blit(scaled, scaled.get_rect(center=(sw // 2, sh // 2)))
            pygame.display.flip()
            last_view, last_draw = view, now
        clock.tick(30 if celebration else 10)


if __name__ == "__main__":
    main()

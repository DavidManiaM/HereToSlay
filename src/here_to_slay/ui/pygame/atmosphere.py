"""The living table: dark felt, depth-lit rim, sparse motes.

Everything is deterministic and cached so a resize never rebuilds grain
pixel-by-pixel, and a dropped frame cannot hide a rules bug.

Two things the Phase 11 performance pass changed, both measured rather than
guessed (``docs/ui_guide.md`` records the numbers):

* **The backdrop art is rebuilt only when the geometry stops moving.** Every
  size-keyed cache below misses on every frame of a *dragged window resize*,
  because the table oval is a fraction of the window and therefore a new size
  each frame — and :func:`_table` costs about 58 ms to paint, being a
  2x-supersampled ellipse stack with a tiled grain layer. Dragging a window edge
  ran at **139 ms a frame**. :class:`_Layer` stretches the art it already has
  while the geometry is in motion and builds the real thing once, on the first
  frame that asks for the same size twice: 43 ms, and the felt is repainted once
  instead of once per frame.
* **The room and the vignette are one opaque surface.** They were two
  full-screen *alpha* blits per frame over a board that then covered them; an
  opaque composite is a straight copy. With the removal of a table-sized
  ``.copy()`` per frame for the active seat's arc, that took a steady frame from
  15.2 ms to 5.8 ms — a 66 fps ceiling to a 171 fps one.
"""

from __future__ import annotations

import contextlib
import math
from functools import lru_cache
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.theme import C

_MOTE_COUNT = 8
_SS = 2  # supersample factor for table / placemat layers
#: Alpha quantisation for motes, so a breathing one is a cache hit.
_MOTE_STEPS = 2


def _hash01(n: int) -> float:
    x = (n * 0x9E3779B1) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x85EBCA6B) & 0xFFFFFFFF
    x ^= x >> 13
    return ((x * 0xC2B2AE35) & 0xFFFFFFFF) / 0xFFFFFFFF


@lru_cache(maxsize=6)
def _room(width: int, height: int) -> pygame.Surface:
    """Dark vertical gradient, warm ceiling glow, bokeh, horizon seam."""
    width, height = max(8, width), max(8, height)
    surf = T.surface((width, height))
    surf.blit(T.vgradient(width, height, (18, 28, 32), C.VOID), (0, 0))
    # Warm ceiling pool
    T.blit_glow(surf, (width // 2, int(height * 0.08)), int(width * 0.55),
                (80, 48, 28, 38), power=2.2)
    # Bokeh lights
    for i in range(7):
        bx = int(_hash01(i * 23 + 5) * width)
        by = int(_hash01(i * 41 + 9) * height * 0.45)
        br = int(40 + _hash01(i * 67) * 90)
        col = (
            int(40 + _hash01(i * 89) * 40),
            int(60 + _hash01(i * 103) * 50),
            int(70 + _hash01(i * 127) * 40),
            int(12 + _hash01(i * 151) * 18),
        )
        T.blit_glow(surf, (bx, by), br, col, power=2.4)
    # Faint horizon seam
    seam_y = int(height * 0.38)
    seam = T.surface((width, 3))
    for x in range(width):
        a = int(18 * abs(math.sin(x / max(1, width) * math.pi)))
        if a:
            seam.set_at((x, 1), (90, 70, 50, a))
    surf.blit(seam, (0, seam_y))
    return surf


@lru_cache(maxsize=6)
def _felt_noise_tile(size: int = 128) -> pygame.Surface:
    """Deterministic grain tile for BLEND_RGBA_MULT (128px for crisp 1080p)."""
    tile = T.surface((size, size))
    for y in range(size):
        for x in range(size):
            n = _hash01(x * 928371 + y * 689287)
            v = int(200 + n * 55)
            tile.set_at((x, y), (v, v, v, 255))
    return tile


def _paint_table(surf: pygame.Surface, width: int, height: int) -> None:
    """Paint the oval table stack onto ``surf`` at the given pixel size."""
    rect = pygame.Rect(0, 0, width, height)

    shadow = T.surface((width, height))
    pygame.draw.ellipse(shadow, (0, 0, 0, 55), rect.inflate(8, 6).move(0, 10))
    surf.blit(shadow, (0, 0))

    outer = rect.inflate(-6, -4)
    pygame.draw.ellipse(surf, C.TABLE_RIM, outer, 0)
    inner = outer.inflate(-14, -10)
    pygame.draw.ellipse(surf, C.FELT_DEEP, inner, 0)

    bevel = T.surface((width, height))
    bevel_rect = outer.inflate(-4, -4)
    pygame.draw.arc(bevel, C.TABLE_RIM_LIT, bevel_rect, math.pi * 0.85, math.pi * 1.65, 3)
    surf.blit(bevel, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

    cx, cy = width // 2, int(height * 0.42)
    T.blit_glow(surf, (cx, cy), int(min(width, height) * 0.38),
                (*T.mix(C.FELT_LIGHT, (255, 255, 255), 0.12), 48), power=1.9)

    grain = _felt_noise_tile()
    grain_layer = T.surface((width, height))
    for gy in range(0, height, grain.get_height()):
        for gx in range(0, width, grain.get_width()):
            grain_layer.blit(grain, (gx, gy))
    surf.blit(grain_layer, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    inlay = inner.inflate(-18, -14)
    pygame.draw.ellipse(surf, (*T.shade(C.GOLD, 0.6), 80), inlay, 1)


@lru_cache(maxsize=12)
def _table(width: int, height: int) -> pygame.Surface:
    """Depth stack supersampled 2x then smoothscaled for crisp oval edges."""
    width, height = max(16, width), max(12, height)
    hi_w, hi_h = width * _SS, height * _SS
    hi = T.surface((hi_w, hi_h))
    _paint_table(hi, hi_w, hi_h)
    return pygame.transform.smoothscale(hi, (width, height))


def _ellipse_arc_rect(width: int, height: int) -> pygame.Rect:
    """Bounding ellipse matching the felt oval, nudged outside the rim."""
    outer = pygame.Rect(0, 0, width, height).inflate(-6, -4)
    inner = outer.inflate(-14, -10)
    return inner.inflate(10, 8)


@lru_cache(maxsize=24)
def _placemats(width: int, height: int, angles: tuple[float, ...]) -> pygame.Surface:
    """Elliptical seat arcs on the table oval, keyed by seat angle (radians).

    Drawn at 2x then downscaled so strokes stay smooth at full HD.
    """
    width, height = max(16, width), max(12, height)
    hi_w, hi_h = width * _SS, height * _SS
    hi = T.surface((hi_w, hi_h))
    arc_rect = _ellipse_arc_rect(hi_w, hi_h)
    stroke = max(4, 8 * _SS)
    half_sweep = math.radians(22)

    for i, angle in enumerate(angles):
        colour = T.seat_colour(i)
        start = angle - half_sweep
        end = angle + half_sweep
        pygame.draw.arc(hi, (*colour, 32), arc_rect, start, end, stroke)

    return pygame.transform.smoothscale(hi, (width, height))


@lru_cache(maxsize=8)
def _felt(width: int, height: int, angles: tuple[float, ...]) -> pygame.Surface:
    """The table and its seat arcs, as one surface.

    Composited rather than blitted separately because they always change
    together: one cache, one blit, and — the reason it matters — one thing for
    :class:`_Layer` to stretch while a camera blend is in flight.
    """
    surf = _table(width, height).copy()
    if angles:
        surf.blit(_placemats(width, height, angles), (0, 0))
    return surf


@lru_cache(maxsize=48)
def _active_arc(
    width: int, height: int, angle: float, seat_i: int,
) -> pygame.Surface:
    """Single brighter arc for the active seat (breath-tinted at draw time)."""
    width, height = max(16, width), max(12, height)
    hi_w, hi_h = width * _SS, height * _SS
    hi = T.surface((hi_w, hi_h))
    arc_rect = _ellipse_arc_rect(hi_w, hi_h)
    stroke = max(5, 10 * _SS)
    half_sweep = math.radians(24)
    colour = T.seat_colour(seat_i)
    pygame.draw.arc(
        hi, (*colour, 90), arc_rect,
        angle - half_sweep, angle + half_sweep, stroke,
    )
    return pygame.transform.smoothscale(hi, (width, height))


@lru_cache(maxsize=8)
def _ground(width: int, height: int) -> pygame.Surface:
    """Room and vignette, composited once, **opaque**.

    Opaque matters: the board is drawn on top of every pixel of this, so its
    alpha channel was never used for anything, and blitting a surface with one
    costs roughly twice as much as blitting one without.
    """
    width, height = max(8, width), max(8, height)
    surf = pygame.Surface((width, height))
    surf.blit(_room(width, height), (0, 0))
    surf.blit(T.vignette((width, height), 110), (0, 0))
    with contextlib.suppress(pygame.error):
        # Matching the display format makes the per-frame blit a copy. Raises
        # when there is no display (headless tests), where it does not matter.
        surf = surf.convert()
    return surf


@lru_cache(maxsize=256)
def _mote_sprite(
    radius: int, colour: tuple[int, int, int], alpha: int = 255
) -> pygame.Surface:
    """Soft radial disc — no hard circle edges at HD.

    ``alpha`` is part of the key so a breathing mote is a lookup rather than a
    surface copy per mote per frame. Callers quantise it (:data:`_MOTE_STEPS`),
    which is why 256 entries is plenty for a handful of motes.
    """
    r = max(1, radius)
    # Build a soft glow a bit larger than the mote radius.
    glow_r = max(4, r * 3)
    sprite = T.radial_glow(glow_r, (*colour, 180), power=2.4)
    if alpha < 255:
        sprite = sprite.copy()
        sprite.set_alpha(alpha)
    return sprite


class _Layer:
    """Backdrop art that must not be rebuilt while its geometry is moving.

    The rule is one line: **build only when asked for the same geometry twice in
    a row.** A window being dragged never asks twice, so it never triggers a
    build; the frame after the drag stops does, exactly once. In between,
    whatever art is already in hand is stretched to fit — a couple of
    milliseconds instead of sixty, and nobody studies the felt's grain while
    they are dragging a window edge.
    """

    __slots__ = ("_art", "_build", "_key", "_wanted")

    def __init__(self, build: Any) -> None:
        self._build = build
        self._art: pygame.Surface | None = None
        self._key: tuple[Any, ...] | None = None
        self._wanted: tuple[Any, ...] | None = None

    def get(self, key: tuple[Any, ...], size: tuple[int, int]) -> pygame.Surface:
        if key == self._key and self._art is not None:
            return self._art
        if self._art is None or key == self._wanted:
            self._art = self._build(*key)
            self._key = key
            self._wanted = key
            return self._art
        self._wanted = key
        if self._art.get_size() == size:
            return self._art
        # `scale` rather than `smoothscale`: half the cost, and the result is on
        # screen for a fraction of a second while everything else is moving too.
        return pygame.transform.scale(self._art, size)


class Atmosphere:
    """Per-frame table dressing. Cheap to update, cheap to draw."""

    def __init__(self) -> None:
        self.time = 0.0
        self._size = (1600, 900)
        self._motes: list[tuple[float, float, float, float, int, tuple[int, int, int], float]] = []
        # One per piece of geometry-dependent art. See `_Layer`: these are what
        # stop a camera blend from repainting the felt sixty times a second.
        self._felt = _Layer(_felt)
        self._arc = _Layer(_active_arc)
        self._rebuild(self._size)

    def _rebuild(self, size: tuple[int, int]) -> None:
        self._size = size
        w, h = size
        self._motes = []
        palette = (C.GOLD_PALE, C.CYAN, (180, 200, 190), (140, 160, 150))
        cone_cx, cone_cy = w // 2, int(h * 0.42)
        cone_r = min(w, h) * 0.38
        for i in range(_MOTE_COUNT):
            for _ in range(8):
                x = _hash01(i * 17 + 3) * w
                y = _hash01(i * 31 + 11) * h
                if math.hypot(x - cone_cx, y - cone_cy) <= cone_r:
                    break
            else:
                x = cone_cx + (_hash01(i * 53) - 0.5) * cone_r * 1.6
                y = cone_cy + (_hash01(i * 71) - 0.5) * cone_r * 0.9
            self._motes.append((
                x, y,
                4.0 + _hash01(i * 53) * 8.0,
                -3.0 - _hash01(i * 71) * 6.0,
                1 + int(_hash01(i * 97) * 2),
                palette[i % len(palette)],
                _hash01(i * 113) * math.tau,
            ))

    def update(self, dt: float, size: tuple[int, int]) -> None:
        if size != self._size and size[0] > 0 and size[1] > 0:
            self._rebuild(size)
        self.time += dt
        w, h = self._size
        next_motes = []
        for x, y, vx, vy, radius, colour, phase in self._motes:
            x = (x + vx * dt + math.sin(self.time + phase) * 3.0 * dt) % (w + 20) - 10
            y = (y + vy * dt) % (h + 20) - 10
            next_motes.append((x, y, vx, vy, radius, colour, phase))
        self._motes = next_motes

    def draw(
        self,
        screen: pygame.Surface,
        layout: Any,
        *,
        active_seat: str | None = None,
        active_index: int | None = None,
        camera_key: str = "local",
    ) -> None:
        del camera_key, active_seat  # active_index is the resolved seat slot
        w, h = screen.get_size()
        screen.blit(_ground(w, h), (0, 0))

        table = getattr(layout, "table_rect", None)
        if table is not None and table.width > 40:
            size = (table.width, table.height)
            seats = getattr(layout, "seats", ())
            angles = tuple(float(s.angle) for s in seats)
            screen.blit(self._felt.get((*size, angles), size), table.topleft)

            if active_index is not None and 0 <= active_index < len(angles):
                glow = self._arc.get(
                    (*size, angles[active_index], active_index), size
                )
                # No copy: this surface is drawn nowhere else, so setting its
                # alpha in place is safe — and copying a table-sized surface was
                # costing more than every other layer put together.
                glow.set_alpha(int(90 + 70 * (0.5 + 0.5 * math.sin(self.time * 1.4))))
                screen.blit(glow, table.topleft, special_flags=pygame.BLEND_RGBA_ADD)

        self._draw_motes(screen)

    def _draw_motes(self, screen: pygame.Surface) -> None:
        for x, y, _vx, _vy, radius, colour, phase in self._motes:
            fade = 0.25 + 0.4 * (0.5 + 0.5 * math.sin(self.time * 0.8 + phase))
            # Quantised so a breathing mote hits the sprite cache instead of
            # copying a surface every frame. Forty steps of alpha over a range
            # of forty is one step per level: nothing is lost.
            a = int(40 * fade) // _MOTE_STEPS * _MOTE_STEPS
            if a < 4:
                continue
            sprite = _mote_sprite(radius, colour, a)
            hr = sprite.get_width() // 2
            screen.blit(sprite, (int(x - hr), int(y - hr)))


def blit_card_sheen(dest: pygame.Surface, rect: pygame.Rect, amount: float) -> None:
    """A diagonal highlight that sweeps across a hovered card."""
    if amount < 0.08 or rect.width < 12 or rect.height < 12:
        return
    travel = T.ease_out_cubic(min(1.0, amount))
    band_w = max(10, int(rect.width * 0.28))
    x = int(rect.left - band_w + travel * (rect.width + band_w * 2))
    sheen = T.surface((band_w, rect.height))
    sheen.blit(
        T.hgradient(band_w, rect.height, (255, 255, 255, 0), (255, 255, 255, 55)),
        (0, 0),
    )
    sheen.blit(
        T.hgradient(band_w, rect.height, (255, 255, 255, 55), (255, 255, 255, 0)),
        (0, 0),
        special_flags=pygame.BLEND_RGBA_MAX,
    )
    clip = dest.get_clip()
    dest.set_clip(rect)
    dest.blit(sheen, (x, rect.top), special_flags=pygame.BLEND_RGBA_ADD)
    dest.set_clip(clip)


__all__ = ["Atmosphere", "blit_card_sheen"]

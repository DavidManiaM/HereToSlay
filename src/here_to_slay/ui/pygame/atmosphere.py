"""The living table: dark felt, depth-lit rim, sparse motes.

Everything is deterministic and cached so a resize never rebuilds grain
pixel-by-pixel, and a dropped frame cannot hide a rules bug.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.theme import C

_MOTE_COUNT = 8


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
def _felt_noise_tile(size: int = 64) -> pygame.Surface:
    """Deterministic grain tile for BLEND_RGBA_MULT."""
    tile = T.surface((size, size))
    for y in range(size):
        for x in range(size):
            n = _hash01(x * 928371 + y * 689287)
            v = int(200 + n * 55)
            tile.set_at((x, y), (v, v, v, 255))
    return tile


@lru_cache(maxsize=12)
def _table(width: int, height: int) -> pygame.Surface:
    """Depth stack: contact shadow, rim bevel, felt, key-light, grain, inlay."""
    width, height = max(16, width), max(12, height)
    surf = T.surface((width, height))
    rect = pygame.Rect(0, 0, width, height)

    # Contact shadow under the whole oval
    shadow = T.surface((width, height))
    pygame.draw.ellipse(shadow, (0, 0, 0, 55), rect.inflate(8, 6).move(0, 10))
    surf.blit(shadow, (0, 0))

    # Outer rim band (wood/leather)
    outer = rect.inflate(-6, -4)
    pygame.draw.ellipse(surf, C.TABLE_RIM, outer, 0)
    inner = outer.inflate(-14, -10)
    pygame.draw.ellipse(surf, C.FELT_DEEP, inner, 0)

    # Upper-left bevel highlight on the rim
    bevel = T.surface((width, height))
    bevel_rect = outer.inflate(-4, -4)
    pygame.draw.arc(bevel, C.TABLE_RIM_LIT, bevel_rect, math.pi * 0.85, math.pi * 1.65, 3)
    surf.blit(bevel, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

    # Key-light pool centred at 0.42h
    cx, cy = width // 2, int(height * 0.42)
    T.blit_glow(surf, (cx, cy), int(min(width, height) * 0.38),
                (*T.mix(C.FELT_LIGHT, (255, 255, 255), 0.12), 48), power=1.9)

    # Felt grain
    grain = _felt_noise_tile()
    grain_layer = T.surface((width, height))
    for gy in range(0, height, grain.get_height()):
        for gx in range(0, width, grain.get_width()):
            grain_layer.blit(grain, (gx, gy))
    surf.blit(grain_layer, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    # Thin gold inlay
    inlay = inner.inflate(-18, -14)
    pygame.draw.ellipse(surf, (*T.shade(C.GOLD, 0.6), 80), inlay, 1)

    return surf


@lru_cache(maxsize=24)
def _placemats(
    width: int, height: int, seat_sig: tuple[tuple[int, int, int, int], ...],
) -> pygame.Surface:
    """Per-seat tinted arcs around the table edge."""
    surf = T.surface((width, height))
    table_cx, table_cy = width // 2, height // 2
    for i, (sx, sy, sw, sh) in enumerate(seat_sig):
        colour = T.seat_colour(i)
        cx = sx + sw // 2
        cy = sy + sh // 2
        angle = math.atan2(cy - table_cy, cx - table_cx)
        arc_r = int(min(width, height) * 0.44)
        arc_rect = pygame.Rect(table_cx - arc_r, table_cy - arc_r, arc_r * 2, arc_r * 2)
        start = math.degrees(angle) - 28
        end = math.degrees(angle) + 28
        pygame.draw.arc(surf, (*colour, 30), arc_rect, math.radians(start), math.radians(end), 6)
    return surf


_mote_sprites: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}


def _mote_sprite(radius: int, colour: tuple[int, int, int]) -> pygame.Surface:
    key = (radius, colour)
    hit = _mote_sprites.get(key)
    if hit is not None:
        return hit
    r = max(1, radius)
    surf = T.surface((r * 4, r * 4))
    pygame.draw.circle(surf, (*colour, 255), (r * 2, r * 2), r)
    _mote_sprites[key] = surf
    return surf


class Atmosphere:
    """Per-frame table dressing. Cheap to update, cheap to draw."""

    def __init__(self) -> None:
        self.time = 0.0
        self._size = (1600, 900)
        self._motes: list[tuple[float, float, float, float, int, tuple[int, int, int], float]] = []
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
        camera_key: str = "local",
    ) -> None:
        del camera_key  # reserved for camera-specific lighting later
        w, h = screen.get_size()
        screen.blit(_room(w, h), (0, 0))

        table = getattr(layout, "table_rect", None)
        if table is not None and table.width > 40:
            screen.blit(_table(table.width, table.height), table.topleft)

            seats = getattr(layout, "seats", ())
            if seats:
                sig = tuple(
                    (s.rect.x, s.rect.y, s.rect.w, s.rect.h) for s in seats
                )
                mats = _placemats(table.width, table.height, sig)
                # Brighten active seat arc
                if active_seat is not None:
                    breath = 0.5 + 0.5 * math.sin(self.time * 1.4)
                    active_layer = mats.copy()
                    active_layer.set_alpha(int(40 + 35 * breath))
                    screen.blit(active_layer, table.topleft, special_flags=pygame.BLEND_RGBA_ADD)
                screen.blit(mats, table.topleft)

        self._draw_motes(screen)
        screen.blit(T.vignette((w, h), 110), (0, 0))

    def _draw_motes(self, screen: pygame.Surface) -> None:
        for x, y, _vx, _vy, radius, colour, phase in self._motes:
            fade = 0.25 + 0.4 * (0.5 + 0.5 * math.sin(self.time * 0.8 + phase))
            a = int(32 * fade)
            if a < 4:
                continue
            sprite = _mote_sprite(radius, colour)
            sprite = sprite.copy()
            sprite.set_alpha(a)
            r = radius * 2
            screen.blit(sprite, (int(x - r * 2), int(y - r * 2)))


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

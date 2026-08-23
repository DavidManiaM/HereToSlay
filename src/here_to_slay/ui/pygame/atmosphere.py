"""The living table: felt grain, floating motes, a class constellation.

Cosmetic only. The board still reads correctly with this module deleted —
``GameScene`` just paints a flat gradient instead — so a dropped frame or a
headless test that never constructs one cannot hide a rules bug.

Everything is deterministic. A replayed game's table must look the same twice
(the same reason ``core/`` may not import ``random``), so motes are seeded from
the window size, not from wall-clock entropy.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.theme import C

_CLASS_COLOURS = tuple(T.CLASS_COLOURS.values())
_MOTE_COUNT = 48


@lru_cache(maxsize=8)
def _felt_tile(size: int = 256) -> pygame.Surface:
    """A noisy indigo square. Tiled so a resize never rebuilds grain."""
    small = 64
    surf = T.surface((small, small))
    for y in range(small):
        for x in range(small):
            n = _hash01(x * 131 + y * 917 + 41)
            m = _hash01(x * 53 + y * 197 + 7)
            tone = int(14 + n * 18 + m * 6)
            a = int(28 + n * 36)
            surf.set_at((x, y), (tone + 4, tone, tone + 18, a))
    return pygame.transform.smoothscale(surf, (size, size))


@lru_cache(maxsize=4)
def _hex_veil(width: int, height: int) -> pygame.Surface:
    """A faint honeycomb over the monster row — the table's 'arena'."""
    width, height = max(8, width), max(8, height)
    surf = T.surface((width, height))
    radius = 22
    dx = int(radius * 1.75)
    dy = int(radius * 1.52)
    colour = (255, 205, 92, 16)
    for row, y in enumerate(range(-radius, height + radius, dy)):
        ox = (dx // 2) if row % 2 else 0
        for x in range(-radius + ox, width + radius, dx):
            pygame.draw.circle(surf, colour, (x, y), radius, 1)
    return surf


def _hash01(n: int) -> float:
    x = (n * 0x9E3779B1) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x85EBCA6B) & 0xFFFFFFFF
    x ^= x >> 13
    return ((x * 0xC2B2AE35) & 0xFFFFFFFF) / 0xFFFFFFFF


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
        palette = (C.GOLD, C.GOLD_PALE, C.EMBER, C.ARCANE, C.FROST, C.ROSE)
        for i in range(_MOTE_COUNT):
            self._motes.append((
                _hash01(i * 17 + 3) * w,
                _hash01(i * 31 + 11) * h,
                8.0 + _hash01(i * 53) * 22.0,
                -6.0 - _hash01(i * 71) * 16.0,
                1 + int(_hash01(i * 97) * 3),
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
            x = (x + vx * dt + math.sin(self.time + phase) * 6.0 * dt) % (w + 20) - 10
            y = (y + vy * dt) % (h + 20) - 10
            next_motes.append((x, y, vx, vy, radius, colour, phase))
        self._motes = next_motes

    def draw(self, screen: pygame.Surface, layout: Any) -> None:
        w, h = screen.get_size()
        screen.blit(T.vgradient(w, h, C.FELT, C.FELT_DEEP), (0, 0))
        self._tile_felt(screen)
        self._draw_hearth(screen, layout)
        self._draw_constellation(screen, layout)
        self._draw_motes(screen)
        self._draw_torches(screen, layout)
        screen.blit(T.vignette((w, h), 168), (0, 0))

    def _tile_felt(self, screen: pygame.Surface) -> None:
        tile = _felt_tile()
        tw, th = tile.get_size()
        w, h = screen.get_size()
        for y in range(0, h, th):
            for x in range(0, w, tw):
                screen.blit(tile, (x, y))

    def _draw_hearth(self, screen: pygame.Surface, layout: Any) -> None:
        """A warm pool of light under the Monster row — the table's focus."""
        row = layout.monster_row_rect
        beat = T.pulse(self.time, period=3.4, low=0.55, high=1.0)
        T.blit_glow(
            screen, row.center, int(row.width * 0.52),
            T.alpha((90, 48, 110), int(52 * beat)),
        )
        T.blit_glow(
            screen, row.center, int(row.width * 0.28),
            T.alpha(C.BLOOD, int(28 * beat)),
        )
        veil = _hex_veil(max(8, row.width), max(8, row.height))
        screen.blit(veil, row.topleft, special_flags=pygame.BLEND_RGBA_ADD)

        # Gold rail around the arena, breathing with the pulse.
        rim = row.inflate(8, 8)
        T.round_rect(screen, rim, T.alpha(C.GOLD, int(36 + 28 * beat)),
                     radius=T.M.RADIUS_L + 4, width=1)

    def _draw_constellation(self, screen: pygame.Surface, layout: Any) -> None:
        """Six class-coloured stars orbit the Monster row slowly."""
        row = layout.monster_row_rect
        rx = max(80, int(row.width * 0.46))
        ry = max(48, int(row.height * 0.42))
        cx, cy = row.center
        n = len(_CLASS_COLOURS)
        for i, colour in enumerate(_CLASS_COLOURS):
            angle = self.time * 0.18 + i * (math.tau / n)
            x = int(cx + math.cos(angle) * rx)
            y = int(cy + math.sin(angle) * ry)
            glow = T.pulse(self.time + i * 0.4, period=2.6, low=0.35, high=1.0)
            T.blit_glow(screen, (x, y), 16, T.alpha(colour, int(90 * glow)))
            pygame.draw.circle(screen, colour, (x, y), 3)
            pygame.draw.circle(screen, T.alpha(C.INK_BRIGHT, 80), (x, y), 3, 1)
            # Hairline to the next star, so the orbit reads as one figure.
            nxt = (i + 1) % n
            a2 = self.time * 0.18 + nxt * (math.tau / n)
            x2 = int(cx + math.cos(a2) * rx)
            y2 = int(cy + math.sin(a2) * ry)
            T.hairline(screen, (x, y), (x2, y2), T.alpha(colour, 28))

    def _draw_motes(self, screen: pygame.Surface) -> None:
        for x, y, _vx, _vy, radius, colour, phase in self._motes:
            fade = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self.time * 1.4 + phase))
            a = int(70 * fade)
            if a < 8:
                continue
            r = max(1, radius)
            scratch = T.surface((r * 4, r * 4))
            pygame.draw.circle(scratch, T.alpha(colour, a), (r * 2, r * 2), r)
            screen.blit(scratch, (int(x - r * 2), int(y - r * 2)))

    def _draw_torches(self, screen: pygame.Surface, layout: Any) -> None:
        """Flickering embers in the four corners of the board, like table lamps."""
        board = layout.board_rect
        points = (
            (board.left + 28, board.top + 22, C.EMBER),
            (board.right - 28, board.top + 22, C.GOLD),
            (board.left + 28, board.bottom - 18, C.ARCANE),
            (board.right - 28, board.bottom - 18, C.ROSE),
        )
        for i, (x, y, colour) in enumerate(points):
            flicker = T.pulse(self.time * 1.7 + i * 0.9, period=0.55, low=0.45, high=1.0)
            T.blit_glow(screen, (x, y), 34, T.alpha(colour, int(55 * flicker)))
            pygame.draw.circle(screen, T.alpha(colour, int(160 * flicker)), (x, y), 3)


def blit_card_sheen(dest: pygame.Surface, rect: pygame.Rect, amount: float) -> None:
    """A diagonal highlight that sweeps across a hovered card."""
    if amount < 0.08 or rect.width < 12 or rect.height < 12:
        return
    # The band travels left-to-right as hover eases in, then holds.
    travel = T.ease_out_cubic(min(1.0, amount))
    band_w = max(10, int(rect.width * 0.28))
    x = int(rect.left - band_w + travel * (rect.width + band_w * 2))
    sheen = T.surface((band_w, rect.height))
    sheen.blit(
        T.hgradient(band_w, rect.height, (255, 255, 255, 0), (255, 248, 220, 70)),
        (0, 0),
    )
    sheen.blit(
        T.hgradient(band_w, rect.height, (255, 248, 220, 70), (255, 255, 255, 0)),
        (0, 0),
        special_flags=pygame.BLEND_RGBA_MAX,
    )
    clip = dest.get_clip()
    dest.set_clip(rect)
    dest.blit(sheen, (x, rect.top), special_flags=pygame.BLEND_RGBA_ADD)
    dest.set_clip(clip)


__all__ = ["Atmosphere", "blit_card_sheen"]

"""Cosmetic motion. Nothing here may ever block the engine.

That is the load-bearing rule of this module. The engine runs on its own thread
and does not know animations exist; the GUI notices that a card changed zone
*after the fact* (see ``tracker.py``) and plays a flight to celebrate it. If a
frame drops or an animation is cancelled, the board still shows the truth,
because the truth is drawn from ``engine.view()`` and animations only draw
*extra* pixels on top.

Two layers:

* :class:`Animation` — a timed thing that draws itself. Subclasses cover card
  flights, dice tumbles, floating numbers, rings, bursts, banners and confetti.
* :class:`AnimationManager` — a z-ordered queue plus the screen-space effects
  that are not per-object: screen shake and full-screen flashes.

Animations are additive by design: several can target the same card at once
(a flight, a ring and a floating "+2") and the result still reads.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import pygame

from here_to_slay.ui import lexicon as L
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.card_renderer import render_card, render_card_back
from here_to_slay.ui.pygame.icons import draw_icon
from here_to_slay.ui.pygame.theme import C, M

#: Draw order. Card flights pass over the board but under modal overlays.
Z_BEHIND = -10
Z_BOARD = 0
Z_CARD = 10
Z_ABOVE = 20
Z_TOP = 30


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Animation:
    """A timed visual. ``update`` advances it, ``draw`` paints one frame."""

    z: int = Z_ABOVE

    def __init__(self, duration: float, *, delay: float = 0.0) -> None:
        self.duration = max(0.01, duration)
        self.delay = max(0.0, delay)
        self.elapsed = 0.0
        self.cancelled = False
        #: fired once when the animation completes — used to chain flights
        self.on_done: Callable[[], None] | None = None
        self._fired = False

    @property
    def started(self) -> bool:
        return self.elapsed >= self.delay

    @property
    def progress(self) -> float:
        if not self.started:
            return 0.0
        return min(1.0, (self.elapsed - self.delay) / self.duration)

    @property
    def finished(self) -> bool:
        return self.cancelled or self.elapsed >= self.delay + self.duration

    def update(self, dt: float) -> bool:
        self.elapsed += dt
        if self.finished and not self._fired:
            self._fired = True
            if self.on_done is not None and not self.cancelled:
                self.on_done()
        return self.finished

    def draw(self, screen: pygame.Surface) -> None:  # pragma: no cover - overridden
        pass

    def cancel(self) -> None:
        self.cancelled = True


# ---------------------------------------------------------------------------
# Card motion
# ---------------------------------------------------------------------------


class CardMoveAnimation(Animation):
    """A card flying between two places, arcing and spinning as it goes.

    The arc is what sells it: a straight linear slide reads as a UI transition,
    a lifted parabola reads as a hand moving a card across a table. Height
    scales with distance so a short shuffle does not launch into orbit.
    """

    z = Z_CARD

    def __init__(
        self,
        card_def: Any,
        start_pos: tuple[int, int],
        end_pos: tuple[int, int],
        size: tuple[int, int] = (100, 140),
        duration: float = 0.32,
        *,
        face_down: bool = False,
        flip: bool = False,
        spin: float = 0.0,
        arc: float | None = None,
        delay: float = 0.0,
        end_size: tuple[int, int] | None = None,
        trail: bool = False,
    ) -> None:
        super().__init__(duration, delay=delay)
        self.card_def = card_def
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.size = size
        self.end_size = end_size or size
        self.face_down = face_down
        self.flip = flip
        self.spin = spin
        self.trail = trail
        distance = math.hypot(end_pos[0] - start_pos[0], end_pos[1] - start_pos[1])
        self.arc = distance * 0.14 if arc is None else arc
        self._front = (
            render_card_back(*size) if card_def is None else render_card(card_def, *size)
        )
        self._back = render_card_back(*size)

    def _frame(self) -> tuple[pygame.Surface, pygame.Rect]:
        p = self.progress
        t = T.ease_out_cubic(p)
        land = T.ease_out_back(min(1.0, max(0.0, (p - 0.75) / 0.25))) if p > 0.75 else 0.0
        x = T.lerp(self.start_pos[0], self.end_pos[0], t)
        y = T.lerp(self.start_pos[1], self.end_pos[1], t) - math.sin(p * math.pi) * self.arc
        scale = T.lerp(0.86, 1.0, t) * (1.0 + 0.06 * land)
        w = max(1, int(T.lerp(self.size[0], self.end_size[0], t) * scale))
        h = max(1, int(T.lerp(self.size[1], self.end_size[1], t) * scale))

        showing_back = self.face_down
        squeeze = 1.0
        if self.flip:
            squeeze = abs(math.cos(p * math.pi))
            showing_back = self.face_down if p < 0.5 else not self.face_down
            squeeze = max(0.06, squeeze)

        surf = self._back if showing_back else self._front
        if (w, h) != self.size:
            surf = pygame.transform.smoothscale(surf, (max(1, w), max(1, h)))
        if squeeze < 0.999:
            surf = pygame.transform.smoothscale(surf, (max(1, int(w * squeeze)), max(1, h)))
        if self.spin:
            surf = pygame.transform.rotozoom(surf, self.spin * (1.0 - t), 1.0)

        rect = surf.get_rect(center=(int(x + w / 2), int(y + h / 2)))
        return surf, rect

    def draw(self, screen: pygame.Surface) -> None:
        if not self.started:
            return
        surf, rect = self._frame()
        p = self.progress
        shadow_spread = int(6 + p * 14)
        shadow_off = int(3 + p * 8)
        T.drop_shadow(screen, rect, radius=10, spread=shadow_spread,
                      offset=(0, shadow_off), strength=int(60 + p * 80))
        screen.blit(surf, rect.topleft)


class DealAnimation(Animation):
    """A staggered fan of cards leaving the deck — the opening deal."""

    z = Z_CARD

    def __init__(
        self,
        origin: tuple[int, int],
        targets: Sequence[tuple[int, int]],
        size: tuple[int, int],
        *,
        stagger: float = 0.05,
        duration: float = 0.3,
    ) -> None:
        super().__init__(duration + stagger * max(0, len(targets) - 1))
        self.legs = [
            CardMoveAnimation(None, origin, target, size, duration,
                              face_down=True, delay=i * stagger, trail=False)
            for i, target in enumerate(targets)
        ]

    def update(self, dt: float) -> bool:
        for leg in self.legs:
            leg.update(dt)
        return super().update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        for leg in self.legs:
            if leg.started and not leg.finished:
                leg.draw(screen)


# ---------------------------------------------------------------------------
# Dice
# ---------------------------------------------------------------------------


class DiceRollAnimation(Animation):
    """Scramble ``np.random("N")`` digits, then settle on the final total.

    Matches the RO tech lexicon dice readout — no pip cubes required.
    """

    z = Z_ABOVE

    def __init__(
        self,
        final_values: tuple[int, ...],
        rect: pygame.Rect,
        duration: float = 0.65,
        *,
        faces: int = 6,
        total: int | None = None,
        accent: tuple[int, int, int] = C.GOLD,
    ) -> None:
        super().__init__(duration)
        self.final_values = tuple(final_values)
        self.rect = pygame.Rect(rect)
        self.faces = max(2, faces)
        self.total = total if total is not None else sum(final_values)
        self.accent = accent
        self._noise = _Wobble(len(final_values) or 1)

    def _scramble_total(self) -> int:
        if self.progress >= 0.72:
            return int(self.total)
        # Digits churn inside the quotes until settle.
        churn = int(self.elapsed * 28) + int(self._noise.at(0, self.elapsed) * 9)
        lo = max(2, len(self.final_values))
        hi = max(lo, self.faces * max(1, len(self.final_values)))
        return lo + (churn * 2654435761 >> 13) % max(1, hi - lo + 1)

    def draw(self, screen: pygame.Surface) -> None:
        rect = self.rect
        p = self.progress
        settle = 0.72
        bounce = 0.0
        if p >= settle:
            bounce = T.ease_out_back(min(1.0, (p - settle) / max(0.001, 1.0 - settle)))
        draw_rect = pygame.Rect(rect)
        draw_rect.y -= int(8 * bounce * (1.0 - p * 0.3))
        T.drop_shadow(screen, draw_rect, radius=8, spread=6,
                      offset=(0, int(4 + 6 * (1.0 - bounce))), strength=70)

        value = self._scramble_total()
        label = f'roll("{value}")'
        mono = T.mono(max(12, min(22, rect.width // 12)), bold=True)

        T.inset(screen, draw_rect, radius=M.RADIUS)

        jitter = 0.0
        if p < settle:
            jitter = self._noise.at(1, self.elapsed) * 4.0 * (1.0 - p / settle)

        colour = self.accent if p >= settle else T.mix(C.INK_DIM, self.accent, 0.45)
        T.text(
            screen, label,
            (draw_rect.centerx + int(jitter), draw_rect.centery),
            mono, colour, anchor="center", shadow=None,
            max_width=draw_rect.width - 16,
        )
        if p >= settle:
            k = bounce
            ring = draw_rect.inflate(int(-8 + 4 * k), int(-8 + 4 * k))
            T.round_rect(screen, ring, T.alpha(self.accent, int(160 * k)), radius=8, width=2)


class _Wobble:
    """Deterministic pseudo-noise, so a replayed roll tumbles identically."""

    __slots__ = ("_salt",)

    def __init__(self, salt: int) -> None:
        self._salt = salt * 7919 + 13

    def at(self, index: int, t: float) -> float:
        a = math.sin((t * 11.3 + index * 2.7 + self._salt) * 1.7)
        b = math.sin((t * 19.7 + index * 1.3 + self._salt) * 0.9)
        return (a + b) * 0.5


_die_cache: dict[tuple[int, int, int, tuple[int, int, int]], pygame.Surface] = {}

#: Pip coordinates in a 3x3 grid, per face value.
_PIPS: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((0.5, 0.5),),
    2: ((0.28, 0.28), (0.72, 0.72)),
    3: ((0.26, 0.26), (0.5, 0.5), (0.74, 0.74)),
    4: ((0.28, 0.28), (0.72, 0.28), (0.28, 0.72), (0.72, 0.72)),
    5: ((0.27, 0.27), (0.73, 0.27), (0.5, 0.5), (0.27, 0.73), (0.73, 0.73)),
    6: ((0.28, 0.24), (0.72, 0.24), (0.28, 0.5), (0.72, 0.5), (0.28, 0.76), (0.72, 0.76)),
}


def die_face(size: int, value: int, faces: int, accent: tuple[int, int, int]) -> pygame.Surface:
    key = (size, value, faces, accent)
    hit = _die_cache.get(key)
    if hit is not None:
        return hit
    surf = T.surface((size, size))
    radius = max(3, size // 6)
    T.round_rect(surf, pygame.Rect(0, 0, size, size), (250, 248, 244), radius=radius)
    surf.blit(T.vgradient(size, size, (255, 255, 255, 40), (0, 0, 0, 46)), (0, 0))
    _apply_round_clip(surf, radius)
    T.round_rect(surf, pygame.Rect(0, 0, size, size), T.shade(accent, 0.8), radius=radius, width=2)

    pips = _PIPS.get(value) if faces == 6 else None
    if pips:
        r = max(2, size // 10)
        for fx, fy in pips:
            pygame.draw.circle(surf, (38, 32, 46), (int(fx * size), int(fy * size)), r)
            pygame.draw.circle(surf, (90, 80, 100), (int(fx * size), int(fy * size)), r, 1)
    else:
        T.text(surf, str(value), (size // 2, size // 2),
               T.display(max(10, int(size * 0.56))), (38, 32, 46), anchor="center", shadow=None)
    _die_cache[key] = surf
    return surf


def _apply_round_clip(surf: pygame.Surface, radius: int) -> None:
    w, h = surf.get_size()
    mask = T.surface((w, h))
    pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, w, h), border_radius=radius)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)


# ---------------------------------------------------------------------------
# Floating feedback
# ---------------------------------------------------------------------------


class ModifierPopAnimation(Animation):
    """A number that rises, grows and fades — "+2", "-1", "3 AP"."""

    z = Z_TOP

    def __init__(
        self,
        text: str,
        pos: tuple[int, int],
        colour: tuple[int, int, int] = C.GOLD,
        duration: float = 0.75,
        *,
        size: int = 26,
        rise: int = 40,
        delay: float = 0.0,
    ) -> None:
        super().__init__(duration, delay=delay)
        self.text = text
        self.pos = pos
        self.colour = colour
        self.size = size
        self.rise = rise

    def draw(self, screen: pygame.Surface) -> None:
        if not self.started:
            return
        p = self.progress
        scale = T.ease_out_back(min(1.0, p * 3.2)) if p < 0.32 else 1.0
        fade = 1.0 if p < 0.55 else 1.0 - (p - 0.55) / 0.45
        y = self.pos[1] - int(self.rise * T.ease_out_cubic(p))
        fnt = T.display(max(9, int(self.size * scale)))
        surf = fnt.render(self.text, True, self.colour)
        glow = fnt.render(self.text, True, C.INK_BRIGHT)
        rect = surf.get_rect(center=(self.pos[0], y))
        a = int(255 * max(0.0, fade))
        glow.set_alpha(int(a * 0.2))
        screen.blit(glow, (rect.left + 1, rect.top + 2))
        surf.set_alpha(a)
        screen.blit(surf, rect.topleft)


class FlashAnimation(Animation):
    """A border pulse around a rect — "look here"."""

    z = Z_ABOVE

    def __init__(
        self,
        rect: pygame.Rect,
        colour: tuple[int, int, int] = C.GOLD,
        duration: float = 0.5,
        *,
        radius: int = 12,
        thickness: int = 3,
    ) -> None:
        super().__init__(duration)
        self.rect = pygame.Rect(rect)
        self.colour = colour
        self.radius = radius
        self.thickness = thickness

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        a = int(200 * (1.0 - abs(p - 0.35) / 0.65) ** 1.4)
        if a <= 0:
            return
        grow = int(6 * T.ease_out_cubic(p))
        rect = self.rect.inflate(grow * 2, grow * 2)
        T.round_rect(screen, rect, T.alpha(self.colour, a),
                     radius=self.radius + grow, width=self.thickness)


class RingBurstAnimation(Animation):
    """An expanding ring — a slain Monster, a landed Challenge."""

    z = Z_ABOVE

    def __init__(
        self,
        centre: tuple[int, int],
        colour: tuple[int, int, int] = C.GOLD,
        duration: float = 0.6,
        *,
        radius: int = 90,
        rings: int = 3,
        thickness: int = 4,
    ) -> None:
        super().__init__(duration)
        self.centre = centre
        self.colour = colour
        self.radius = radius
        self.rings = max(1, rings)
        self.thickness = thickness

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        for i in range(self.rings):
            offset = i / (self.rings * 1.6)
            k = p - offset
            if k <= 0 or k >= 1:
                continue
            r = int(self.radius * T.ease_out_quint(k))
            a = int(190 * (1 - k) ** 1.6)
            if r < 2 or a <= 0:
                continue
            box = pygame.Rect(0, 0, r * 2 + 4, r * 2 + 4)
            scratch = T.surface(box.size)
            pygame.draw.circle(scratch, T.alpha(self.colour, a), (r + 2, r + 2), r,
                               max(1, self.thickness - i))
            screen.blit(scratch, (self.centre[0] - r - 2, self.centre[1] - r - 2))


class ParticleBurstAnimation(Animation):
    """Motes thrown from a point: sparks, shards, embers, confetti.

    Deliberately deterministic (a fixed wobble, not ``random``) so a replayed
    game looks the same twice — the same reason ``core/`` may not import
    ``random``.
    """

    z = Z_ABOVE

    def __init__(
        self,
        centre: tuple[int, int],
        colours: Sequence[tuple[int, int, int]] = (C.GOLD, C.EMBER, C.GOLD_PALE),
        duration: float = 0.7,
        *,
        count: int = 14,
        speed: float = 220.0,
        gravity: float = 520.0,
        size: int = 4,
        spread: float = math.tau,
        angle: float = -math.pi / 2,
        shape: str = "square",
        seed: int = 0,
    ) -> None:
        super().__init__(duration)
        self.centre = centre
        self.colours = tuple(colours) or (C.GOLD,)
        self.gravity = gravity
        self.size = size
        self.shape = shape
        self.motes: list[tuple[float, float, float, tuple[int, int, int], float]] = []
        for i in range(max(1, count)):
            h = _hash01(seed * 977 + i * 131)
            g = _hash01(seed * 613 + i * 379 + 7)
            a = angle + (h - 0.5) * spread
            v = speed * (0.42 + 0.58 * g)
            self.motes.append((
                math.cos(a) * v, math.sin(a) * v,
                0.55 + 0.45 * _hash01(i * 7919 + seed),
                self.colours[i % len(self.colours)],
                _hash01(i * 3301 + seed) * math.tau,
            ))

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        t = p * self.duration
        fade = (1.0 - p) ** 1.35
        if fade <= 0.01:
            return
        for vx, vy, scale, colour, phase in self.motes:
            x = self.centre[0] + vx * t
            y = self.centre[1] + vy * t + 0.5 * self.gravity * t * t
            s = max(1, int(self.size * scale * (0.4 + 0.6 * fade)))
            a = int(170 * fade)
            if self.shape == "circle":
                scratch = T.surface((s * 2, s * 2))
                pygame.draw.circle(scratch, T.alpha(colour, a), (s, s), s)
                screen.blit(scratch, (int(x - s), int(y - s)))
            else:
                chip = T.surface((s * 2, s * 2))
                pygame.draw.rect(chip, T.alpha(colour, a), pygame.Rect(0, 0, s * 2, s))
                spun = pygame.transform.rotozoom(chip, math.degrees(phase + t * 7.0), 1.0)
                screen.blit(spun, (int(x - spun.get_width() / 2), int(y - spun.get_height() / 2)))


class ConfettiAnimation(Animation):
    """Victory rain across the whole window."""

    z = Z_TOP

    def __init__(
        self,
        size: tuple[int, int],
        duration: float = 4.5,
        *,
        count: int = 150,
        seed: int = 0,
    ) -> None:
        super().__init__(duration)
        self.size = size
        palette = (C.GOLD, C.ROSE, C.FROST, C.POISON, C.ARCANE, C.GOLD_PALE)
        self.flakes = [
            (
                _hash01(seed + i * 17) * size[0],
                -_hash01(seed + i * 31) * size[1] - 20,
                120.0 + _hash01(seed + i * 53) * 240.0,
                _hash01(seed + i * 71) * math.tau,
                palette[i % len(palette)],
                4 + int(_hash01(seed + i * 97) * 6),
            )
            for i in range(max(1, count))
        ]

    def draw(self, screen: pygame.Surface) -> None:
        t = self.progress * self.duration
        fade = 1.0 if self.progress < 0.75 else 1.0 - (self.progress - 0.75) / 0.25
        for x, y, fall, phase, colour, s in self.flakes:
            py = y + fall * t
            if py > self.size[1] + 20:
                py = py % (self.size[1] + 40) - 20
            px = x + math.sin(phase + t * 2.4) * 26
            chip = T.surface((s * 2, s * 2))
            pygame.draw.rect(chip, T.alpha(colour, int(230 * fade)),
                             pygame.Rect(0, 0, s * 2, max(2, s)))
            spun = pygame.transform.rotozoom(chip, math.degrees(phase + t * 4.0), 1.0)
            screen.blit(spun, (int(px), int(py)))


class BannerAnimation(Animation):
    """Thin accent lozenge for turn changes and slays — not a full-screen veil."""

    z = Z_TOP

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        colour: tuple[int, int, int] = C.GOLD,
        duration: float = 0.85,
        icon: str | None = None,
        y_fraction: float = 0.18,
    ) -> None:
        super().__init__(duration)
        self.title = title
        self.subtitle = subtitle
        self.colour = colour
        self.icon = icon
        self.y_fraction = y_fraction

    def draw(self, screen: pygame.Surface) -> None:
        w, h = screen.get_size()
        p = self.progress
        if p < 0.22:
            k = T.ease_out_quint(p / 0.22)
            slide, fade = (1 - k) * w * 0.2, k
        elif p > 0.78:
            k = (p - 0.78) / 0.22
            slide, fade = k * w * 0.15, 1 - k
        else:
            slide, fade = 0.0, 1.0
        if fade <= 0.01:
            return

        title_font = T.ui(max(14, int(h * 0.024)), bold=True)
        sub_font = T.ui(max(10, int(h * 0.016)))
        tw = title_font.size(self.title)[0]
        sw = sub_font.size(self.subtitle)[0] if self.subtitle else 0
        icon_w = 28 if self.icon else 0
        panel_w = min(int(w * 0.4), max(tw, sw) + icon_w + 48)
        panel_h = T.s(36) + (T.s(16) if self.subtitle else 0)
        rect = pygame.Rect(0, 0, panel_w, panel_h)
        rect.center = (int(w // 2 + slide), int(h * self.y_fraction))

        layer = T.surface((panel_w + 40, panel_h + 40))
        local = pygame.Rect(20, 20, panel_w, panel_h)
        T.round_rect(layer, local, T.alpha(self.colour, int(200 * fade)), radius=local.height // 2)
        T.round_rect(layer, local, T.alpha(self.colour, 220), radius=local.height // 2, width=2)

        text_left = local.left + 16 + icon_w
        if self.icon:
            draw_icon(layer, self.icon, (local.left + 22, local.centery), 18, self.colour)
        T.text(layer, self.title, (text_left, local.centery - (6 if self.subtitle else 0)),
               title_font, C.INK_BRIGHT, anchor="midleft", shadow=None)
        if self.subtitle:
            T.text(layer, self.subtitle, (text_left, local.centery + 10), sub_font,
                   T.alpha(C.INK_BRIGHT, 180), anchor="midleft", shadow=None)

        layer.set_alpha(int(200 * fade))
        screen.blit(layer, (rect.left - 20, rect.top - 20))


class TableLightSweep(Animation):
    """Slow specular arc across the felt on turn change."""

    z = Z_BEHIND

    def __init__(self, table_rect: pygame.Rect, duration: float = 1.4) -> None:
        super().__init__(duration)
        self.table_rect = pygame.Rect(table_rect)

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        if p <= 0.05 or p >= 0.95:
            return
        rect = self.table_rect
        layer = T.surface(rect.size)
        cx, cy = rect.width // 2, rect.height // 2
        r = int(min(rect.width, rect.height) * 0.46)
        arc_rect = pygame.Rect(cx - r, cy - r, r * 2, r * 2)
        start = math.pi * 0.15 + p * math.pi
        end = start + 0.4
        pygame.draw.arc(layer, (*C.GOLD_PALE, 38), arc_rect, start, end, 5)
        screen.blit(layer, rect.topleft, special_flags=pygame.BLEND_RGBA_ADD)


class APSpendAnimation(Animation):
    """AP pips drain with a tick when a prompt is consumed."""

    z = Z_ABOVE

    def __init__(
        self,
        centre: tuple[int, int],
        *,
        remaining: int = 0,
        duration: float = 0.55,
    ) -> None:
        super().__init__(duration)
        self.centre = centre
        self.remaining = remaining

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        fade = 1.0 - p
        if fade <= 0.02:
            return
        y = self.centre[1] - int(18 * p)
        label = f"{self.remaining} {L.AP_ONE if self.remaining == 1 else L.AP}"
        T.text(screen, label, (self.centre[0], y), T.ui(14, bold=True),
               T.alpha(C.WARN, int(220 * fade)), anchor="center", shadow=None)


class TrailAnimation(Animation):
    """A comet from A to B — a steal, a pull, a targeted effect."""

    z = Z_ABOVE

    def __init__(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        colour: tuple[int, int, int] = C.ARCANE,
        duration: float = 0.42,
        *,
        width: int = 4,
    ) -> None:
        super().__init__(duration)
        self.start = start
        self.end = end
        self.colour = colour
        self.width = width

    def draw(self, screen: pygame.Surface) -> None:
        p = T.ease_out_cubic(self.progress)
        segments = 14
        tail = max(0.0, p - 0.3)
        for i in range(segments):
            k = tail + (p - tail) * (i / segments)
            x = T.lerp(self.start[0], self.end[0], k)
            y = T.lerp(self.start[1], self.end[1], k) - math.sin(k * math.pi) * 34
            a = int(160 * (i / segments) ** 1.6 * (1 - self.progress * 0.35))
            r = max(1, int(self.width * (0.35 + 0.65 * i / segments)))
            scratch = T.surface((r * 2 + 2, r * 2 + 2))
            pygame.draw.circle(scratch, T.alpha(self.colour, a), (r + 1, r + 1), r)
            screen.blit(scratch, (int(x - r), int(y - r)))
        head = (int(T.lerp(self.start[0], self.end[0], p)),
                int(T.lerp(self.start[1], self.end[1], p) - math.sin(p * math.pi) * 34))
        pygame.draw.circle(screen, T.alpha(self.colour, 180), head, max(2, self.width))


class SpotlightAnimation(Animation):
    """Dim everything except one rect. Draws attention without a modal."""

    z = Z_BOARD

    def __init__(
        self, rect: pygame.Rect, duration: float = 0.9, *, strength: int = 150
    ) -> None:
        super().__init__(duration)
        self.rect = pygame.Rect(rect)
        self.strength = strength

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        fade = math.sin(p * math.pi)
        a = int(self.strength * fade)
        if a <= 2:
            return
        w, h = screen.get_size()
        veil = T.surface((w, h))
        veil.fill((4, 4, 12, a))
        hole = self.rect.inflate(24, 24)
        pygame.draw.rect(veil, (0, 0, 0, 0), hole, border_radius=18)
        screen.blit(veil, (0, 0))


class EmberRainAnimation(Animation):
    """Upward-drifting embers — a Challenge landing, a Magic resolving."""

    z = Z_ABOVE

    def __init__(
        self,
        size: tuple[int, int],
        duration: float = 1.6,
        *,
        count: int = 22,
        seed: int = 0,
        origin: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(duration)
        self.size = size
        ox, oy = origin if origin is not None else (size[0] // 2, size[1] // 2)
        palette = (C.EMBER, C.GOLD, C.GOLD_PALE, C.BLOOD)
        self.sparks = [
            (
                ox + (_hash01(seed + i * 19) - 0.5) * size[0] * 0.55,
                oy + _hash01(seed + i * 41) * 30,
                -40.0 - _hash01(seed + i * 67) * 90.0,
                palette[i % len(palette)],
                2 + int(_hash01(seed + i * 83) * 3),
                _hash01(seed + i * 101) * math.tau,
            )
            for i in range(max(1, count))
        ]

    def draw(self, screen: pygame.Surface) -> None:
        t = self.progress * self.duration
        fade = 1.0 if self.progress < 0.7 else 1.0 - (self.progress - 0.7) / 0.3
        for x, y, rise, colour, s, phase in self.sparks:
            px = x + math.sin(phase + t * 3.1) * 18
            py = y + rise * t
            a = int(130 * fade)
            scratch = T.surface((s * 4, s * 4))
            pygame.draw.circle(scratch, T.alpha(colour, a), (s * 2, s * 2), s)
            screen.blit(scratch, (int(px - s * 2), int(py - s * 2)))


class RunePulseAnimation(Animation):
    """A rotating hex that blooms and fades — used for Magic and Challenges."""

    z = Z_ABOVE

    def __init__(
        self,
        centre: tuple[int, int],
        colour: tuple[int, int, int] = C.ARCANE,
        duration: float = 0.85,
        *,
        radius: int = 90,
    ) -> None:
        super().__init__(duration)
        self.centre = centre
        self.colour = colour
        self.radius = radius

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        scale = T.ease_out_quint(min(1.0, p * 1.35))
        fade = 1.0 if p < 0.45 else 1.0 - (p - 0.45) / 0.55
        if fade <= 0.02:
            return
        r = max(8, int(self.radius * scale))
        points = []
        spin = self.elapsed * 1.4
        for i in range(6):
            a = spin + i * (math.tau / 6)
            points.append((
                int(self.centre[0] + math.cos(a) * r),
                int(self.centre[1] + math.sin(a) * r),
            ))
        scratch = T.surface((r * 2 + 12, r * 2 + 12))
        local = [(p[0] - self.centre[0] + r + 6, p[1] - self.centre[1] + r + 6) for p in points]
        pygame.draw.polygon(scratch, T.alpha(self.colour, int(120 * fade)), local, 3)
        screen.blit(scratch, (self.centre[0] - r - 6, self.centre[1] - r - 6))


def _hash01(n: int) -> float:
    """A stable 0..1 from an integer — deterministic particle scatter."""
    x = (n * 0x9E3779B1) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x85EBCA6B) & 0xFFFFFFFF
    x ^= x >> 13
    return ((x * 0xC2B2AE35) & 0xFFFFFFFF) / 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class AnimationManager:
    """Owns the queue, plus the two effects that live on the screen itself.

    Screen shake and full-screen flash are not animations in the list because
    they change how *everything else* is drawn; the scene asks for
    :attr:`shake_offset` when blitting its board and calls
    :meth:`draw_overlays` last.
    """

    def __init__(self, *, cap: int = 24) -> None:
        self.animations: list[Animation] = []
        self.cap = cap
        self.time = 0.0
        self._shake_power = 0.0
        self._shake_decay = 6.0
        self._flash: tuple[tuple[int, int, int], float, float] | None = None
        self.enabled = True

    # -- queue -------------------------------------------------------------

    def add(self, anim: Animation | None) -> Animation | None:
        if anim is None or not self.enabled:
            return anim
        if len(self.animations) >= self.cap:
            # Drop the oldest cosmetic rather than let the list grow unbounded;
            # a stutter is better than a leak, and nothing here is load-bearing.
            self.animations.pop(0)
        self.animations.append(anim)
        return anim

    def extend(self, anims: Sequence[Animation]) -> None:
        for anim in anims:
            self.add(anim)

    def update(self, dt: float) -> None:
        self.time += dt
        for anim in self.animations:
            anim.update(dt)
        if self.animations:
            self.animations = [a for a in self.animations if not a.finished]
        if self._shake_power > 0.0:
            self._shake_power = max(0.0, self._shake_power - self._shake_decay * dt * 60 / 60)
            self._shake_power *= 0.90
        if self._flash is not None:
            colour, age, duration = self._flash
            age += dt
            self._flash = None if age >= duration else (colour, age, duration)

    def draw(self, screen: pygame.Surface, *, below: int = Z_TOP) -> None:
        """Paint every queued animation with ``z < below``, in z order."""
        if not self.animations:
            return
        for anim in sorted(self.animations, key=lambda a: a.z):
            if anim.z < below and anim.started and not anim.cancelled:
                anim.draw(screen)

    def draw_top(self, screen: pygame.Surface) -> None:
        for anim in sorted(self.animations, key=lambda a: a.z):
            if anim.z >= Z_TOP and anim.started and not anim.cancelled:
                anim.draw(screen)

    def clear(self) -> None:
        self.animations.clear()
        self._shake_power = 0.0
        self._flash = None

    @property
    def busy(self) -> bool:
        return bool(self.animations)

    def count(self) -> int:
        return len(self.animations)

    # -- screen effects ----------------------------------------------------

    def shake(self, power: float = 9.0) -> None:
        if self.enabled:
            self._shake_power = max(self._shake_power, power)

    @property
    def shake_offset(self) -> tuple[int, int]:
        if self._shake_power <= 0.2:
            return (0, 0)
        p = self._shake_power
        return (
            int(math.sin(self.time * 74.0) * p),
            int(math.cos(self.time * 61.0) * p * 0.7),
        )

    def flash(self, colour: tuple[int, int, int] = C.INK_BRIGHT, duration: float = 0.22) -> None:
        if self.enabled:
            self._flash = (colour, 0.0, max(0.02, duration))

    def draw_overlays(self, screen: pygame.Surface) -> None:
        """Full-screen flash. Called after everything else, including modals."""
        if self._flash is None:
            return
        colour, age, duration = self._flash
        a = int(150 * (1.0 - age / duration) ** 1.6)
        if a <= 0:
            return
        veil = T.surface(screen.get_size())
        veil.fill((*colour, a))
        screen.blit(veil, (0, 0))


__all__ = [
    "Z_ABOVE",
    "Z_BEHIND",
    "Z_BOARD",
    "Z_CARD",
    "Z_TOP",
    "APSpendAnimation",
    "Animation",
    "AnimationManager",
    "BannerAnimation",
    "CardMoveAnimation",
    "ConfettiAnimation",
    "DealAnimation",
    "DiceRollAnimation",
    "EmberRainAnimation",
    "FlashAnimation",
    "ModifierPopAnimation",
    "ParticleBurstAnimation",
    "RingBurstAnimation",
    "RunePulseAnimation",
    "SpotlightAnimation",
    "TableLightSweep",
    "TrailAnimation",
    "die_face",
]

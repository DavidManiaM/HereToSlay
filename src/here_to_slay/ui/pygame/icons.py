"""Vector icons, drawn with primitives rather than a font.

The first version of the card renderer used Unicode symbols for class pips —
``\u2694`` for Fighter, ``\u266a`` for Bard. On a machine without a font
covering those code points every one of them renders as a hollow box, which is
exactly the kind of "works here, broken there" bug an asset-light UI should not
have. So the icons are polygons: no font dependency, they scale cleanly to a
14px pip or a 60px header, and they take their colour from the caller.

Every painter has the same shape — ``(surface, centre, size, colour)`` — so
:func:`draw_icon` can dispatch by name and callers never branch.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import pygame

from here_to_slay.ui.pygame import theme as T

Painter = Callable[[pygame.Surface, tuple[int, int], int, tuple[int, int, int]], None]


def _poly(
    dest: pygame.Surface, points: Sequence[tuple[float, float]], colour: tuple[int, int, int]
) -> None:
    if len(points) >= 3:
        pygame.draw.polygon(dest, colour, [(int(x), int(y)) for x, y in points])


def _line(
    dest: pygame.Surface,
    a: tuple[float, float],
    b: tuple[float, float],
    colour: tuple[int, int, int],
    width: int,
) -> None:
    pygame.draw.line(dest, colour, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), max(1, width))


def _rot(
    point: tuple[float, float], centre: tuple[float, float], angle: float
) -> tuple[float, float]:
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    ca, sa = math.cos(angle), math.sin(angle)
    return centre[0] + dx * ca - dy * sa, centre[1] + dx * sa + dy * ca


# ---------------------------------------------------------------------------
# Class icons
# ---------------------------------------------------------------------------


def bard(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A quaver: note head plus flagged stem."""
    cx, cy = centre
    r = max(2, size // 4)
    head = (cx - r // 2, cy + size // 4)
    pygame.draw.ellipse(
        dest, colour,
        pygame.Rect(head[0] - r, head[1] - int(r * 0.8), r * 2, int(r * 1.6)),
    )
    stem_x = head[0] + r - 1
    _line(dest, (stem_x, head[1]), (stem_x, cy - size // 2), colour, max(2, size // 9))
    _poly(dest, [
        (stem_x, cy - size // 2),
        (stem_x + size // 3, cy - size // 3),
        (stem_x + size // 4, cy - size // 6),
        (stem_x, cy - size // 4),
    ], colour)


def fighter(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """Crossed swords."""
    cx, cy = centre
    h = size // 2
    w = max(2, size // 8)
    for sign in (1, -1):
        tip = (cx + sign * h * 0.85, cy - h * 0.85)
        hilt = (cx - sign * h * 0.62, cy + h * 0.72)
        _line(dest, hilt, tip, colour, w)
        # guard, perpendicular to the blade
        gx, gy = cx - sign * h * 0.34, cy + h * 0.44
        _line(dest, (gx - sign * h * 0.26, gy - h * 0.26),
              (gx + sign * h * 0.26, gy + h * 0.26), colour, max(1, w - 1))
        # pommel
        pygame.draw.circle(dest, colour, (int(hilt[0]), int(hilt[1])), max(1, w // 2 + 1))


def guardian(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A heater shield with a cross-band."""
    cx, cy = centre
    h = size // 2
    _poly(dest, [
        (cx - h * 0.82, cy - h * 0.78),
        (cx + h * 0.82, cy - h * 0.78),
        (cx + h * 0.72, cy + h * 0.28),
        (cx, cy + h),
        (cx - h * 0.72, cy + h * 0.28),
    ], colour)
    ink = T.readable_ink(colour)
    _line(dest, (cx, cy - h * 0.5), (cx, cy + h * 0.55), ink, max(1, size // 11))
    _line(dest, (cx - h * 0.44, cy - h * 0.06), (cx + h * 0.44, cy - h * 0.06), ink,
          max(1, size // 11))


def ranger(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A bow with a nocked arrow."""
    cx, cy = centre
    h = size // 2
    w = max(2, size // 10)
    rect = pygame.Rect(int(cx - h * 0.9), int(cy - h * 0.92), int(h * 1.3), int(h * 1.84))
    pygame.draw.arc(dest, colour, rect, -math.pi / 2.1, math.pi / 2.1, w)
    _line(dest, (cx - h * 0.28, cy - h * 0.82), (cx - h * 0.28, cy + h * 0.82), colour,
          max(1, w - 1))
    _line(dest, (cx - h * 0.28, cy), (cx + h * 0.92, cy), colour, w)
    _poly(dest, [
        (cx + h, cy),
        (cx + h * 0.52, cy - h * 0.3),
        (cx + h * 0.52, cy + h * 0.3),
    ], colour)


def thief(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A dagger, point down."""
    cx, cy = centre
    h = size // 2
    w = max(2, size // 9)
    _poly(dest, [
        (cx, cy + h),
        (cx - w, cy + h * 0.1),
        (cx - w, cy - h * 0.42),
        (cx + w, cy - h * 0.42),
        (cx + w, cy + h * 0.1),
    ], colour)
    _line(dest, (cx - h * 0.62, cy - h * 0.44), (cx + h * 0.62, cy - h * 0.44), colour, w)
    _line(dest, (cx, cy - h * 0.44), (cx, cy - h * 0.94), colour, max(1, w - 1))
    pygame.draw.circle(dest, colour, (cx, int(cy - h * 0.94)), max(1, w // 2 + 1))


def wizard(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A four-point sparkle with two small satellites."""
    h = size // 2
    T.star(dest, centre, int(h * 0.95), colour, points=4, inner=0.3, rotation=math.pi / 4)
    T.star(dest, (centre[0] + int(h * 0.72), centre[1] - int(h * 0.66)),
           max(2, int(h * 0.34)), colour, points=4, inner=0.3, rotation=math.pi / 4)
    T.star(dest, (centre[0] - int(h * 0.7), centre[1] + int(h * 0.6)),
           max(2, int(h * 0.26)), colour, points=4, inner=0.3, rotation=math.pi / 4)


# ---------------------------------------------------------------------------
# Kind / UI icons
# ---------------------------------------------------------------------------


def monster(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A horned skull-ish mask: two horns, two eyes, a fanged jaw."""
    cx, cy = centre
    h = size // 2
    _poly(dest, [
        (cx - h * 0.78, cy - h * 0.2), (cx - h * 0.95, cy - h * 0.95),
        (cx - h * 0.32, cy - h * 0.52),
    ], colour)
    _poly(dest, [
        (cx + h * 0.78, cy - h * 0.2), (cx + h * 0.95, cy - h * 0.95),
        (cx + h * 0.32, cy - h * 0.52),
    ], colour)
    pygame.draw.ellipse(
        dest, colour,
        pygame.Rect(int(cx - h * 0.7), int(cy - h * 0.6), int(h * 1.4), int(h * 1.3)),
    )
    ink = T.readable_ink(colour)
    eye = max(1, size // 9)
    pygame.draw.circle(dest, ink, (int(cx - h * 0.3), int(cy - h * 0.06)), eye)
    pygame.draw.circle(dest, ink, (int(cx + h * 0.3), int(cy - h * 0.06)), eye)
    for i in range(-1, 2):
        _poly(dest, [
            (cx + i * h * 0.34 - h * 0.12, cy + h * 0.42),
            (cx + i * h * 0.34 + h * 0.12, cy + h * 0.42),
            (cx + i * h * 0.34, cy + h * 0.72),
        ], ink)


def item(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A ring with a gem — the base game's Items are trinkets."""
    cx, cy = centre
    h = size // 2
    pygame.draw.circle(dest, colour, (cx, cy + int(h * 0.18)), int(h * 0.62), max(2, size // 9))
    T.star(dest, (cx, cy - int(h * 0.58)), max(2, int(h * 0.42)), colour,
           points=4, inner=0.34, rotation=math.pi / 4)


def magic(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A swirl of three arcs."""
    cx, cy = centre
    h = size // 2
    w = max(2, size // 10)
    for i in range(3):
        r = int(h * (0.95 - i * 0.26))
        start = i * 2.1
        pygame.draw.arc(dest, colour, pygame.Rect(cx - r, cy - r, r * 2, r * 2),
                        start, start + 3.4, w)
    pygame.draw.circle(dest, colour, (cx, cy), max(1, w))


def modifier(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A plus over a minus."""
    cx, cy = centre
    h = size // 2
    w = max(2, size // 8)
    _line(dest, (cx - h * 0.6, cy - h * 0.36), (cx + h * 0.6, cy - h * 0.36), colour, w)
    _line(dest, (cx, cy - h * 0.92), (cx, cy + h * 0.2), colour, w)
    _line(dest, (cx - h * 0.6, cy + h * 0.66), (cx + h * 0.6, cy + h * 0.66), colour, w)


def challenge(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A hand, palm out: "stop"."""
    cx, cy = centre
    h = size // 2
    pygame.draw.circle(dest, colour, (cx, cy), int(h * 0.92), max(2, size // 9))
    w = max(2, size // 8)
    _line(dest, (cx - h * 0.5, cy - h * 0.5), (cx + h * 0.5, cy + h * 0.5), colour, w)


def leader(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A crown."""
    cx, cy = centre
    h = size // 2
    _poly(dest, [
        (cx - h * 0.86, cy + h * 0.52), (cx - h * 0.86, cy - h * 0.52),
        (cx - h * 0.43, cy + h * 0.02), (cx, cy - h * 0.72),
        (cx + h * 0.43, cy + h * 0.02), (cx + h * 0.86, cy - h * 0.52),
        (cx + h * 0.86, cy + h * 0.52),
    ], colour)
    _line(dest, (cx - h * 0.86, cy + h * 0.74), (cx + h * 0.86, cy + h * 0.74), colour,
          max(2, size // 9))


def dice(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A die showing five pips."""
    cx, cy = centre
    h = size // 2
    rect = pygame.Rect(int(cx - h * 0.85), int(cy - h * 0.85), int(h * 1.7), int(h * 1.7))
    pygame.draw.rect(dest, colour, rect, max(2, size // 10), border_radius=max(2, size // 6))
    pip = max(1, size // 11)
    for dx, dy in ((-0.42, -0.42), (0.42, -0.42), (0, 0), (-0.42, 0.42), (0.42, 0.42)):
        pygame.draw.circle(dest, colour, (int(cx + dx * h), int(cy + dy * h)), pip)


def info(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    pygame.draw.circle(dest, colour, (cx, cy), int(h * 0.95), max(2, size // 10))
    pygame.draw.circle(dest, colour, (cx, int(cy - h * 0.44)), max(1, size // 10))
    _line(dest, (cx, cy - h * 0.1), (cx, cy + h * 0.52), colour, max(2, size // 9))


def scroll(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A page with lines — the game log."""
    cx, cy = centre
    h = size // 2
    rect = pygame.Rect(int(cx - h * 0.7), int(cy - h * 0.88), int(h * 1.4), int(h * 1.76))
    pygame.draw.rect(dest, colour, rect, max(2, size // 11), border_radius=max(1, size // 8))
    for i in range(3):
        y = cy - h * 0.4 + i * h * 0.42
        _line(dest, (cx - h * 0.4, y), (cx + h * 0.4, y), colour, max(1, size // 13))


def gear(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    teeth = 8
    for i in range(teeth):
        a = i * math.tau / teeth
        inner = (cx + math.cos(a) * h * 0.55, cy + math.sin(a) * h * 0.55)
        outer = (cx + math.cos(a) * h * 0.98, cy + math.sin(a) * h * 0.98)
        _line(dest, inner, outer, colour, max(2, size // 7))
    pygame.draw.circle(dest, colour, (cx, cy), int(h * 0.55), max(2, size // 9))


def close(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    w = max(2, size // 7)
    _line(dest, (cx - h * 0.6, cy - h * 0.6), (cx + h * 0.6, cy + h * 0.6), colour, w)
    _line(dest, (cx + h * 0.6, cy - h * 0.6), (cx - h * 0.6, cy + h * 0.6), colour, w)


def check(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    w = max(2, size // 6)
    pygame.draw.lines(dest, colour, False, [
        (int(cx - h * 0.7), int(cy)),
        (int(cx - h * 0.14), int(cy + h * 0.56)),
        (int(cx + h * 0.72), int(cy - h * 0.56)),
    ], w)


def skull(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """Slain-monster trophy marker."""
    cx, cy = centre
    h = size // 2
    pygame.draw.circle(dest, colour, (cx, int(cy - h * 0.14)), int(h * 0.68))
    pygame.draw.rect(dest, colour, pygame.Rect(
        int(cx - h * 0.4), int(cy + h * 0.24), int(h * 0.8), int(h * 0.5)),
        border_radius=max(1, size // 10))
    ink = T.readable_ink(colour)
    eye = max(1, size // 8)
    pygame.draw.circle(dest, ink, (int(cx - h * 0.28), int(cy - h * 0.2)), eye)
    pygame.draw.circle(dest, ink, (int(cx + h * 0.28), int(cy - h * 0.2)), eye)


def hand_cards(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """Three fanned cards — the hand-size marker."""
    cx, cy = centre
    h = size // 2
    cw, ch = max(3, int(h * 0.62)), max(4, int(h * 0.95))
    for i, angle in enumerate((-0.34, 0.0, 0.34)):
        rect = pygame.Rect(0, 0, cw, ch)
        chip = T.surface((cw + 4, ch + 4))
        pygame.draw.rect(chip, colour, rect.move(2, 2), border_radius=2)
        spun = pygame.transform.rotate(chip, -angle * 40)
        dest.blit(spun, (cx - spun.get_width() // 2 + int(angle * h * 0.9),
                         cy - spun.get_height() // 2 + (2 if i == 1 else 0)))


def bolt(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """Action point / energy."""
    cx, cy = centre
    h = size // 2
    _poly(dest, [
        (cx + h * 0.28, cy - h * 0.95), (cx - h * 0.58, cy + h * 0.12),
        (cx - h * 0.06, cy + h * 0.12), (cx - h * 0.3, cy + h * 0.95),
        (cx + h * 0.6, cy - h * 0.16), (cx + h * 0.06, cy - h * 0.16),
    ], colour)


def eye(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    pygame.draw.ellipse(dest, colour, pygame.Rect(
        int(cx - h * 0.95), int(cy - h * 0.55), int(h * 1.9), int(h * 1.1)),
        max(2, size // 10))
    pygame.draw.circle(dest, colour, (cx, cy), max(2, int(h * 0.3)))


def target(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    for r in (0.95, 0.6):
        pygame.draw.circle(dest, colour, (cx, cy), int(h * r), max(2, size // 11))
    pygame.draw.circle(dest, colour, (cx, cy), max(1, int(h * 0.22)))


def flask(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """Buff / debuff marker."""
    cx, cy = centre
    h = size // 2
    _poly(dest, [
        (cx - h * 0.3, cy - h * 0.9), (cx + h * 0.3, cy - h * 0.9),
        (cx + h * 0.3, cy - h * 0.3), (cx + h * 0.78, cy + h * 0.85),
        (cx - h * 0.78, cy + h * 0.85), (cx - h * 0.3, cy - h * 0.3),
    ], colour)


def deck(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A stack of face-down cards."""
    cx, cy = centre
    h = size // 2
    cw, ch = max(4, int(h * 1.05)), max(6, int(h * 1.4))
    for i in range(3):
        rect = pygame.Rect(0, 0, cw, ch)
        rect.center = (cx + i * 2 - 2, cy + i * 2 - 2)
        pygame.draw.rect(dest, colour, rect, max(1, size // 12), border_radius=max(1, size // 8))


def discard(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    """A card with a slash — the burn pile."""
    cx, cy = centre
    h = size // 2
    rect = pygame.Rect(0, 0, max(4, int(h * 1.1)), max(6, int(h * 1.5)))
    rect.center = (cx, cy)
    pygame.draw.rect(dest, colour, rect, max(1, size // 12), border_radius=max(1, size // 8))
    _line(dest, (cx - h * 0.7, cy + h * 0.7), (cx + h * 0.7, cy - h * 0.7),
          colour, max(2, size // 9))


# -- plain UI controls ------------------------------------------------------


def plus(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    w = max(2, size // 6)
    _line(dest, (cx - h * 0.72, cy), (cx + h * 0.72, cy), colour, w)
    _line(dest, (cx, cy - h * 0.72), (cx, cy + h * 0.72), colour, w)


def minus(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    _line(dest, (cx - h * 0.72, cy), (cx + h * 0.72, cy), colour, max(2, size // 6))


def _chevron(direction: str) -> Painter:
    def paint(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
        T.chevron(dest, centre, size, colour, direction=direction, width=max(2, size // 7))
    return paint


def pause(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    bar = max(2, size // 5)
    for sign in (-1, 1):
        pygame.draw.rect(dest, colour, pygame.Rect(
            int(cx + sign * h * 0.42 - bar // 2), int(cy - h * 0.7), bar, int(h * 1.4),
        ), border_radius=1)


def play(dest: pygame.Surface, centre: tuple[int, int], size: int, colour) -> None:
    cx, cy = centre
    h = size // 2
    _poly(dest, [
        (cx - h * 0.55, cy - h * 0.75), (cx + h * 0.75, cy), (cx - h * 0.55, cy + h * 0.75),
    ], colour)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

ICONS: dict[str, Painter] = {
    "bard": bard,
    "fighter": fighter,
    "guardian": guardian,
    "ranger": ranger,
    "thief": thief,
    "wizard": wizard,
    "hero": wizard,
    "monster": monster,
    "item": item,
    "magic": magic,
    "modifier": modifier,
    "challenge": challenge,
    "party_leader": leader,
    "leader": leader,
    "dice": dice,
    "info": info,
    "scroll": scroll,
    "gear": gear,
    "close": close,
    "check": check,
    "skull": skull,
    "hand": hand_cards,
    "hand_cards": hand_cards,
    "bolt": bolt,
    "eye": eye,
    "target": target,
    "flask": flask,
    "deck": deck,
    "discard": discard,
    "plus": plus,
    "minus": minus,
    "pause": pause,
    "play": play,
    "chevron_up": _chevron("up"),
    "chevron_down": _chevron("down"),
    "chevron_left": _chevron("left"),
    "chevron_right": _chevron("right"),
}


def draw_icon(
    dest: pygame.Surface,
    name: str,
    centre: tuple[int, int],
    size: int,
    colour: tuple[int, int, int],
) -> bool:
    """Paint icon ``name``. Returns False if there is no such icon."""
    painter = ICONS.get(name)
    if painter is None:
        return False
    painter(dest, centre, max(6, size), colour[:3])
    return True


def card_icon_name(kind: str, card_class: str | None) -> str:
    """The icon that best identifies a card: its class if it has one."""
    if card_class and card_class in ICONS:
        return card_class
    return kind if kind in ICONS else "hero"


__all__ = ["ICONS", "Painter", "card_icon_name", "draw_icon"]

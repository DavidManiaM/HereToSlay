"""The design system: one place that decides what the client looks like.

Every colour, radius, font and easing curve the GUI uses is named here, so a
re-skin is an edit to this file rather than a hunt through eight renderers.
That mirrors what ``data/base/rules.yaml`` does for the rules: the widgets
below know *how* to draw a panel, this module decides *which* panel.

The visual language is a dark tabletop — deep indigo felt, translucent
"glass" panels lifted off it with soft shadows, and a warm gold accent for
anything the player is meant to look at next. Class colours are shared with
``ui/cli/render.py`` so a Bard is magenta in both clients.

Nothing here touches the engine, and every helper is a pure
``(args) -> Surface`` so results can be cached by the caller.
"""

from __future__ import annotations

import math
from functools import lru_cache

import pygame

# ---------------------------------------------------------------------------
# Colour type
# ---------------------------------------------------------------------------

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
Colour = RGB | RGBA


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


class C:
    """Named colours. Short class name because it is used constantly."""

    # -- table surface ------------------------------------------------------
    VOID = (8, 7, 16)
    FELT_DEEP = (17, 15, 34)
    FELT = (26, 23, 48)
    FELT_LIGHT = (38, 34, 66)
    VIGNETTE = (0, 0, 0, 150)

    # -- glass panels -------------------------------------------------------
    GLASS = (44, 40, 74, 205)
    GLASS_SOFT = (38, 34, 64, 165)
    GLASS_DEEP = (22, 20, 40, 232)
    GLASS_RIM = (120, 110, 190, 90)
    GLASS_RIM_HOT = (255, 208, 110, 150)
    SHADOW = (0, 0, 0, 110)

    # -- text ---------------------------------------------------------------
    INK = (232, 230, 246)
    INK_BRIGHT = (255, 255, 255)
    INK_DIM = (152, 148, 184)
    INK_FAINT = (104, 100, 136)
    INK_DARK = (24, 20, 40)

    # -- accents ------------------------------------------------------------
    GOLD = (255, 205, 92)
    GOLD_DEEP = (196, 143, 42)
    GOLD_PALE = (255, 236, 186)
    EMBER = (255, 128, 64)
    BLOOD = (214, 66, 74)
    POISON = (126, 214, 108)
    ARCANE = (150, 122, 255)
    FROST = (108, 205, 236)
    ROSE = (240, 110, 168)

    # -- semantic -----------------------------------------------------------
    GOOD = (108, 214, 132)
    BAD = (226, 86, 92)
    WARN = (245, 186, 82)
    INFO = (118, 178, 240)
    ACTIVE = (255, 205, 92)
    IDLE = (86, 82, 118)

    # -- cards --------------------------------------------------------------
    CARD_PAPER = (243, 238, 226)
    CARD_PAPER_EDGE = (206, 197, 176)
    CARD_INK = (44, 38, 34)
    CARD_INK_DIM = (108, 98, 88)
    CARD_BACK_A = (58, 40, 96)
    CARD_BACK_B = (30, 22, 56)
    CARD_BACK_MARK = (176, 142, 240)


#: Hero / Leader class -> accent colour. Shared with the CLI palette.
CLASS_COLOURS: dict[str, RGB] = {
    "bard": (196, 84, 200),
    "fighter": (214, 66, 74),
    "guardian": (72, 126, 226),
    "ranger": (78, 182, 96),
    "thief": (226, 196, 66),
    "wizard": (66, 196, 220),
}

#: A single glyph per class, drawn where real icon art would go.
CLASS_GLYPHS: dict[str, str] = {
    "bard": "\u266a",  # eighth note
    "fighter": "\u2694",  # crossed swords
    "guardian": "\u26e8",  # shield
    "ranger": "\u27b3",  # arrow
    "thief": "\u2756",  # lozenge (dagger-ish)
    "wizard": "\u2727",  # sparkle
}

CLASS_SHORT: dict[str, str] = {
    "bard": "BRD",
    "fighter": "FTR",
    "guardian": "GRD",
    "ranger": "RNG",
    "thief": "THF",
    "wizard": "WIZ",
}

#: Card kind -> accent colour, for kinds that have no class.
KIND_COLOURS: dict[str, RGB] = {
    "hero": (226, 222, 240),
    "monster": (206, 62, 70),
    "item": (226, 186, 78),
    "magic": (96, 190, 226),
    "modifier": (120, 206, 128),
    "challenge": (236, 110, 96),
    "party_leader": (198, 132, 240),
}

KIND_GLYPHS: dict[str, str] = {
    "hero": "\u2726",
    "monster": "\u2620",
    "item": "\u2692",
    "magic": "\u2735",
    "modifier": "\u00b1",
    "challenge": "\u2717",
    "party_leader": "\u265b",
}

#: Seat -> colour, so player 3 is the same teal in every panel on screen.
SEAT_COLOURS: tuple[RGB, ...] = (
    (255, 205, 92),  # gold
    (108, 205, 236),  # frost
    (240, 110, 168),  # rose
    (126, 214, 108),  # poison
    (168, 140, 255),  # arcane
    (255, 138, 84),  # ember
)


def seat_colour(seat: int) -> RGB:
    return SEAT_COLOURS[seat % len(SEAT_COLOURS)]


def class_colour(card_class: str | None, kind: str = "hero") -> RGB:
    if card_class and card_class in CLASS_COLOURS:
        return CLASS_COLOURS[card_class]
    return KIND_COLOURS.get(kind, C.INK_DIM)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class M:
    """Spacing and radius tokens. Multiples of 4, like every design system."""

    GAP_XS = 4
    GAP_S = 8
    GAP = 12
    GAP_L = 18
    GAP_XL = 26

    RADIUS_S = 6
    RADIUS = 10
    RADIUS_L = 16
    RADIUS_XL = 22

    #: Reference card geometry. Real card scans are 200x283 (~1:1.415), and
    #: keeping the UI on that ratio means art never has to be letterboxed.
    CARD_ASPECT = 1.415
    CARD_W = 132
    CARD_H = int(CARD_W * CARD_ASPECT)  # 186

    TOPBAR_H = 56
    BOTTOM_H = 214
    RAIL_L = 216
    RAIL_R = 306


def card_size(width: int) -> tuple[int, int]:
    """A card box of ``width`` px, at the canonical aspect ratio."""
    return width, round(width * M.CARD_ASPECT)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

#: Preferred families per role. pygame picks the first installed one.
FONT_DISPLAY = "bahnschrift,franklingothicmedium,impact,arialblack,dejavusans"
FONT_UI = "segoeui,calibri,trebuchetms,dejavusans,arial"
FONT_SERIF = "georgia,constantia,palatinolinotype,timesnewroman,dejavuserif"
FONT_MONO = "cascadiamono,consolas,couriernew,dejavusansmono,monospace"

_font_cache: dict[tuple[str, int, bool, bool], pygame.font.Font] = {}


def font(
    size: int, *, family: str = FONT_UI, bold: bool = False, italic: bool = False
) -> pygame.font.Font:
    """A cached ``pygame.font.Font``. Safe to call inside a draw loop."""
    key = (family, size, bold, italic)
    hit = _font_cache.get(key)
    if hit is None:
        hit = pygame.font.SysFont(family, max(6, size), bold=bold, italic=italic)
        _font_cache[key] = hit
    return hit


def ui(size: int, *, bold: bool = False, italic: bool = False) -> pygame.font.Font:
    return font(size, family=FONT_UI, bold=bold, italic=italic)


def display(size: int) -> pygame.font.Font:
    return font(size, family=FONT_DISPLAY, bold=True)


def serif(size: int, *, bold: bool = False, italic: bool = False) -> pygame.font.Font:
    return font(size, family=FONT_SERIF, bold=bold, italic=italic)


def mono(size: int, *, bold: bool = False) -> pygame.font.Font:
    return font(size, family=FONT_MONO, bold=bold)


def clear_font_cache() -> None:
    _font_cache.clear()


# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_out_quint(t: float) -> float:
    return 1.0 - (1.0 - t) ** 5


def ease_in_out(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t: float, overshoot: float = 1.70158) -> float:
    c3 = overshoot + 1
    return 1 + c3 * (t - 1) ** 3 + overshoot * (t - 1) ** 2


def ease_out_elastic(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    p = 0.3
    return 2 ** (-10 * t) * math.sin((t - p / 4) * (2 * math.pi) / p) + 1


def ease_out_bounce(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_colour(a: Colour, b: Colour, t: float) -> RGBA:
    t = max(0.0, min(1.0, t))
    aa = (*a, 255)[:4]
    bb = (*b, 255)[:4]
    return (
        int(lerp(aa[0], bb[0], t)),
        int(lerp(aa[1], bb[1], t)),
        int(lerp(aa[2], bb[2], t)),
        int(lerp(aa[3], bb[3], t)),
    )


def pulse(seconds: float, period: float = 1.4, low: float = 0.35, high: float = 1.0) -> float:
    """A smooth 0..1 oscillation for breathing highlights."""
    phase = (math.sin(seconds * (2 * math.pi / period)) + 1.0) * 0.5
    return low + (high - low) * phase


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------


def shade(colour: Colour, factor: float) -> RGB:
    """Darken (``factor < 1``) or brighten (``> 1``) a colour."""
    return (
        max(0, min(255, int(colour[0] * factor))),
        max(0, min(255, int(colour[1] * factor))),
        max(0, min(255, int(colour[2] * factor))),
    )


def mix(a: Colour, b: Colour, t: float) -> RGB:
    return lerp_colour(a, b, t)[:3]


def alpha(colour: Colour, a: int) -> RGBA:
    return (colour[0], colour[1], colour[2], max(0, min(255, a)))


def luminance(colour: Colour) -> float:
    return (0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]) / 255.0


def readable_ink(background: Colour) -> RGB:
    """Black or white, whichever will actually be legible on ``background``."""
    return C.INK_DARK if luminance(background) > 0.55 else C.INK_BRIGHT


# ---------------------------------------------------------------------------
# Surface helpers
# ---------------------------------------------------------------------------


def surface(size: tuple[int, int]) -> pygame.Surface:
    """A transparent scratch surface."""
    return pygame.Surface((max(1, size[0]), max(1, size[1])), pygame.SRCALPHA)


@lru_cache(maxsize=256)
def vgradient(width: int, height: int, top: Colour, bottom: Colour) -> pygame.Surface:
    """A vertical gradient. Cached, so backgrounds cost one blit per frame."""
    width, height = max(1, width), max(1, height)
    strip = surface((1, height))
    for y in range(height):
        strip.set_at((0, y), lerp_colour(top, bottom, y / max(1, height - 1)))
    return pygame.transform.smoothscale(strip, (width, height))


@lru_cache(maxsize=128)
def hgradient(width: int, height: int, left: Colour, right: Colour) -> pygame.Surface:
    width, height = max(1, width), max(1, height)
    strip = surface((width, 1))
    for x in range(width):
        strip.set_at((x, 0), lerp_colour(left, right, x / max(1, width - 1)))
    return pygame.transform.smoothscale(strip, (width, height))


@lru_cache(maxsize=96)
def radial_glow(radius: int, colour: Colour, *, power: float = 2.0) -> pygame.Surface:
    """A soft circular light. Built small and upscaled — smooth *and* cheap.

    The falloff is baked into the *colour*, not only the alpha, because
    :func:`blit_glow` composites additively and ``BLEND_RGBA_ADD`` ignores the
    source alpha. Ramping alpha alone would add the colour at full strength
    across the whole disc, which reads as a solid blob rather than a light.
    """
    radius = max(2, radius)
    small = max(8, min(48, radius // 2))
    surf = surface((small * 2, small * 2))
    base_a = (*colour, 255)[3]
    for r in range(small, 0, -1):
        falloff = (1.0 - (r / small)) ** power
        a = int(base_a * falloff)
        if a <= 0:
            continue
        scale = falloff * base_a / 255.0
        rgb = (int(colour[0] * scale), int(colour[1] * scale), int(colour[2] * scale))
        pygame.draw.circle(surf, (*rgb, a), (small, small), r)
    return pygame.transform.smoothscale(surf, (radius * 2, radius * 2))


def blit_glow(
    dest: pygame.Surface,
    centre: tuple[int, int],
    radius: int,
    colour: Colour,
    *,
    power: float = 2.0,
) -> None:
    glow = radial_glow(radius, colour, power=power)
    dest.blit(glow, (centre[0] - radius, centre[1] - radius), special_flags=pygame.BLEND_RGBA_ADD)


@lru_cache(maxsize=192)
def _shadow_sprite(width: int, height: int, radius: int, spread: int, a: int) -> pygame.Surface:
    surf = surface((width + spread * 2, height + spread * 2))
    layers = max(3, spread)
    for i in range(layers, 0, -1):
        t = i / layers
        grow = int(spread * t)
        step_a = int(a * (1.0 - t) ** 1.6) + 6
        pygame.draw.rect(
            surf,
            (0, 0, 0, min(255, step_a)),
            pygame.Rect(spread - grow, spread - grow, width + grow * 2, height + grow * 2),
            border_radius=radius + grow,
        )
    return surf


def drop_shadow(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    radius: int = M.RADIUS,
    spread: int = 12,
    offset: tuple[int, int] = (0, 5),
    strength: int = 92,
) -> None:
    """Blur-free soft shadow: concentric rounded rects with falling alpha."""
    sprite = _shadow_sprite(rect.width, rect.height, radius, max(2, spread), strength)
    dest.blit(sprite, (rect.left - spread + offset[0], rect.top - spread + offset[1]))


def round_rect(
    dest: pygame.Surface,
    rect: pygame.Rect,
    colour: Colour,
    *,
    radius: int = M.RADIUS,
    width: int = 0,
) -> None:
    """A rounded rect that tolerates alpha in ``colour``."""
    if len(colour) == 4 and colour[3] < 255:
        scratch = surface(rect.size)
        pygame.draw.rect(
            scratch, colour, pygame.Rect(0, 0, rect.width, rect.height),
            width=width, border_radius=radius,
        )
        dest.blit(scratch, rect.topleft)
    else:
        pygame.draw.rect(dest, colour[:3], rect, width=width, border_radius=radius)


def glass(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    radius: int = M.RADIUS_L,
    fill: Colour = C.GLASS,
    rim: Colour | None = C.GLASS_RIM,
    shadow: bool = True,
    sheen: bool = True,
) -> None:
    """The house panel: soft shadow, translucent body, lit top edge.

    Used for every floating surface in the client so they read as one family.
    """
    if rect.width < 2 or rect.height < 2:
        return
    if shadow:
        drop_shadow(dest, rect, radius=radius, spread=14, offset=(0, 6), strength=96)

    body = surface(rect.size)
    pygame.draw.rect(
        body, fill, pygame.Rect(0, 0, rect.width, rect.height), border_radius=radius
    )
    if sheen and rect.height > 12:
        sheen_h = min(rect.height // 2, 56)
        top = surface((rect.width, sheen_h))
        top.blit(vgradient(rect.width, sheen_h, (255, 255, 255, 26), (255, 255, 255, 0)), (0, 0))
        mask = surface(rect.size)
        pygame.draw.rect(
            mask, (255, 255, 255, 255), pygame.Rect(0, 0, rect.width, rect.height),
            border_radius=radius,
        )
        top.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        body.blit(top, (0, 0))
    dest.blit(body, rect.topleft)

    if rim is not None:
        round_rect(dest, rect, rim, radius=radius, width=1)


def inset(
    dest: pygame.Surface,
    rect: pygame.Rect,
    *,
    radius: int = M.RADIUS,
    fill: Colour = (10, 9, 20, 150),
) -> None:
    """A recessed well — used behind card slots and scroll areas."""
    round_rect(dest, rect, fill, radius=radius)
    round_rect(dest, rect, (0, 0, 0, 120), radius=radius, width=1)


def hairline(
    dest: pygame.Surface,
    start: tuple[int, int],
    end: tuple[int, int],
    colour: Colour = (255, 255, 255, 22),
) -> None:
    scratch = surface((abs(end[0] - start[0]) + 2, abs(end[1] - start[1]) + 2))
    ox, oy = min(start[0], end[0]), min(start[1], end[1])
    pygame.draw.line(
        scratch, colour, (start[0] - ox, start[1] - oy), (end[0] - ox, end[1] - oy), 1
    )
    dest.blit(scratch, (ox, oy))


def vignette(size: tuple[int, int], strength: int = 130) -> pygame.Surface:
    """Darkened edges, so the middle of the table reads as lit."""
    return _vignette_sprite(size[0], size[1], strength)


@lru_cache(maxsize=8)
def _vignette_sprite(width: int, height: int, strength: int) -> pygame.Surface:
    small = surface((64, 64))
    cx, cy = 32, 32
    for y in range(64):
        for x in range(64):
            d = math.hypot((x - cx) / cx, (y - cy) / cy)
            a = int(max(0.0, min(1.0, (d - 0.55) / 0.85)) ** 1.7 * strength)
            if a:
                small.set_at((x, y), (0, 0, 0, a))
    return pygame.transform.smoothscale(small, (max(1, width), max(1, height)))


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def text(
    dest: pygame.Surface,
    value: str,
    pos: tuple[int, int],
    fnt: pygame.font.Font,
    colour: Colour = C.INK,
    *,
    anchor: str = "topleft",
    shadow: Colour | None = (0, 0, 0, 150),
    max_width: int | None = None,
) -> pygame.Rect:
    """Draw one line, optionally ellipsised, with an optional soft shadow."""
    if max_width is not None:
        value = ellipsise(value, fnt, max_width)
    surf = fnt.render(value, True, colour[:3])
    rect = surf.get_rect(**{anchor: pos})
    if shadow is not None:
        ghost = fnt.render(value, True, shadow[:3])
        if len(shadow) == 4:
            ghost.set_alpha(shadow[3])
        dest.blit(ghost, (rect.left + 1, rect.top + 1))
    if len(colour) == 4 and colour[3] < 255:
        surf.set_alpha(colour[3])
    dest.blit(surf, rect.topleft)
    return rect


def ellipsise(value: str, fnt: pygame.font.Font, max_width: int) -> str:
    if fnt.size(value)[0] <= max_width:
        return value
    ell = "\u2026"
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fnt.size(value[:mid] + ell)[0] <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo].rstrip() + ell


def wrap(value: str, fnt: pygame.font.Font, max_width: int) -> list[str]:
    """Greedy word wrap, honouring explicit newlines."""
    lines: list[str] = []
    for paragraph in value.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for word in paragraph.split():
            probe = f"{current} {word}".strip()
            if fnt.size(probe)[0] <= max_width or not current:
                current = probe
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def draw_wrapped(
    dest: pygame.Surface,
    value: str,
    rect: pygame.Rect,
    fnt: pygame.font.Font,
    colour: Colour = C.INK,
    *,
    line_gap: int = 2,
    align: str = "left",
    shadow: Colour | None = None,
) -> int:
    """Fill ``rect`` with wrapped text; returns the height actually used."""
    line_h = fnt.get_linesize() + line_gap
    lines = wrap(value, fnt, rect.width)
    limit = max(1, rect.height // line_h)
    if len(lines) > limit:
        lines = lines[:limit]
        if lines:
            lines[-1] = ellipsise(lines[-1] + "\u2026", fnt, rect.width)
    for i, line in enumerate(lines):
        if not line:
            continue
        y = rect.top + i * line_h
        if align == "center":
            text(dest, line, (rect.centerx, y), fnt, colour, anchor="midtop", shadow=shadow)
        elif align == "right":
            text(dest, line, (rect.right, y), fnt, colour, anchor="topright", shadow=shadow)
        else:
            text(dest, line, (rect.left, y), fnt, colour, shadow=shadow)
    return len(lines) * line_h


def fit_font(
    value: str,
    max_size: int,
    min_size: int,
    box: tuple[int, int],
    *,
    family: str = FONT_UI,
    bold: bool = False,
) -> pygame.font.Font:
    """Largest font in the range whose wrapped text fits ``box``."""
    width, height = box
    for size in range(max_size, min_size - 1, -1):
        fnt = font(size, family=family, bold=bold)
        lines = wrap(value, fnt, width)
        if len(lines) * (fnt.get_linesize() + 1) <= height:
            return fnt
    return font(min_size, family=family, bold=bold)


def fit_line(
    value: str,
    max_size: int,
    min_size: int,
    width: int,
    *,
    family: str = FONT_UI,
    bold: bool = False,
) -> pygame.font.Font:
    """Largest font at which ``value`` fits ``width`` on one line.

    Distinct from :func:`fit_font` because a name plate cannot wrap: a long
    Leader name has to shrink, not spill or get an ellipsis, or the player
    cannot tell "The Divine Arrow" from "The Divine Ar…".
    """
    for size in range(max_size, min_size - 1, -1):
        fnt = font(size, family=family, bold=bold)
        if fnt.size(value)[0] <= width:
            return fnt
    return font(min_size, family=family, bold=bold)


# ---------------------------------------------------------------------------
# Small composite bits used all over the client
# ---------------------------------------------------------------------------


def pill(
    dest: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    *,
    bg: Colour = C.GLASS_DEEP,
    fg: Colour = C.INK,
    fnt: pygame.font.Font | None = None,
    border: Colour | None = None,
) -> None:
    radius = rect.height // 2
    round_rect(dest, rect, bg, radius=radius)
    if border:
        round_rect(dest, rect, border, radius=radius, width=1)
    text(
        dest, label, rect.center, fnt or ui(max(9, rect.height - 8), bold=True),
        fg, anchor="center", max_width=rect.width - 8,
    )


def badge(
    dest: pygame.Surface,
    centre: tuple[int, int],
    radius: int,
    label: str,
    *,
    bg: Colour = C.GOLD,
    fg: Colour | None = None,
    ring: Colour | None = None,
    fnt: pygame.font.Font | None = None,
) -> None:
    """A filled circle with a short label — AP counters, seat initials, counts."""
    pygame.draw.circle(dest, bg[:3], centre, radius)
    if ring:
        pygame.draw.circle(dest, ring[:3], centre, radius, 2)
    text(
        dest, label, centre, fnt or ui(max(9, int(radius * 1.1)), bold=True),
        fg or readable_ink(bg), anchor="center", shadow=None,
    )


def chevron(
    dest: pygame.Surface,
    centre: tuple[int, int],
    size: int,
    colour: Colour,
    *,
    direction: str = "down",
    width: int = 3,
) -> None:
    cx, cy = centre
    h = size // 2
    pts = {
        "down": [(cx - h, cy - h // 2), (cx, cy + h // 2), (cx + h, cy - h // 2)],
        "up": [(cx - h, cy + h // 2), (cx, cy - h // 2), (cx + h, cy + h // 2)],
        "left": [(cx + h // 2, cy - h), (cx - h // 2, cy), (cx + h // 2, cy + h)],
        "right": [(cx - h // 2, cy - h), (cx + h // 2, cy), (cx - h // 2, cy + h)],
    }[direction]
    pygame.draw.lines(dest, colour[:3], False, pts, width)


def star(
    dest: pygame.Surface,
    centre: tuple[int, int],
    radius: int,
    colour: Colour,
    *,
    points: int = 4,
    inner: float = 0.38,
    rotation: float = 0.0,
) -> None:
    verts: list[tuple[float, float]] = []
    for i in range(points * 2):
        r = radius if i % 2 == 0 else radius * inner
        a = rotation + i * math.pi / points
        verts.append((centre[0] + math.cos(a) * r, centre[1] + math.sin(a) * r))
    if len(colour) == 4 and colour[3] < 255:
        scratch = surface((radius * 2 + 4, radius * 2 + 4))
        ox, oy = centre[0] - radius - 2, centre[1] - radius - 2
        pygame.draw.polygon(scratch, colour, [(x - ox, y - oy) for x, y in verts])
        dest.blit(scratch, (ox, oy))
    else:
        pygame.draw.polygon(dest, colour[:3], verts)


def progress_bar(
    dest: pygame.Surface,
    rect: pygame.Rect,
    fraction: float,
    *,
    fill: Colour = C.GOLD,
    track: Colour = (0, 0, 0, 120),
    radius: int | None = None,
) -> None:
    radius = rect.height // 2 if radius is None else radius
    round_rect(dest, rect, track, radius=radius)
    width = int(rect.width * max(0.0, min(1.0, fraction)))
    if width > 1:
        round_rect(dest, pygame.Rect(rect.left, rect.top, width, rect.height), fill, radius=radius)


def clear_surface_caches() -> None:
    vgradient.cache_clear()
    hgradient.cache_clear()
    radial_glow.cache_clear()
    _shadow_sprite.cache_clear()
    _vignette_sprite.cache_clear()


__all__ = [
    "CLASS_COLOURS",
    "CLASS_GLYPHS",
    "CLASS_SHORT",
    "KIND_COLOURS",
    "KIND_GLYPHS",
    "SEAT_COLOURS",
    "C",
    "M",
    "alpha",
    "badge",
    "blit_glow",
    "card_size",
    "chevron",
    "class_colour",
    "clear_font_cache",
    "clear_surface_caches",
    "display",
    "draw_wrapped",
    "drop_shadow",
    "ease_in_out",
    "ease_out_back",
    "ease_out_bounce",
    "ease_out_cubic",
    "ease_out_elastic",
    "ease_out_quint",
    "ellipsise",
    "fit_font",
    "font",
    "glass",
    "hairline",
    "hgradient",
    "inset",
    "lerp",
    "lerp_colour",
    "luminance",
    "mix",
    "mono",
    "pill",
    "progress_bar",
    "pulse",
    "radial_glow",
    "readable_ink",
    "round_rect",
    "seat_colour",
    "serif",
    "shade",
    "star",
    "surface",
    "text",
    "ui",
    "vgradient",
    "vignette",
    "wrap",
]

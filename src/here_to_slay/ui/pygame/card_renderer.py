"""Procedural card rendering — draws *any* card from its ``CardDef``.

A card surface is laid out as:

    ┌──────────────────────────┐
    │  HEADER (class colour)   │  ← name, kind icon
    ├──────────────────────────┤
    │                          │
    │       BODY               │  ← card text (word-wrapped)
    │                          │
    ├──────────────────────────┤
    │  FOOTER                  │  ← roll threshold, tags
    └──────────────────────────┘

Face-down cards show a generic card-back pattern.  Highlighted cards get a
glowing border.  Tapped cards are rotated 90° and dimmed.

Surfaces are cached by ``(def_id, width, height, tapped, highlighted,
face_down)`` so the same card drawn 60 times a second is one dict lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.ui.pygame.colors import (
    BG_CARD,
    BG_CARD_BACK,
    CLASS_COLOURS,
    HIGHLIGHT,
    KIND_COLOURS,
    TEXT,
    TEXT_BRIGHT,
    TEXT_DIM,
    get_font,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_card_cache: dict[tuple[str, int, int, bool, bool, bool], pygame.Surface] = {}


def clear_card_cache() -> None:
    _card_cache.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

CARD_W = 120
CARD_H = 170
CARD_W_SMALL = 80
CARD_H_SMALL = 112
CARD_CORNER = 8


def render_card(
    card_def: Any,
    width: int = CARD_W,
    height: int = CARD_H,
    *,
    tapped: bool = False,
    highlighted: bool = False,
    face_down: bool = False,
) -> pygame.Surface:
    """Render a card surface.  Cached — safe to call every frame."""
    key = (
        card_def.id if card_def else "__back__",
        width,
        height,
        tapped,
        highlighted,
        face_down,
    )
    cached = _card_cache.get(key)
    if cached is not None:
        return cached

    if face_down or card_def is None:
        surf = _render_back(width, height)
    else:
        surf = _render_face(card_def, width, height)

    if highlighted:
        _draw_highlight_border(surf, width, height)

    if tapped:
        surf = _apply_tapped(surf)

    _card_cache[key] = surf
    return surf


def render_card_back(
    width: int = CARD_W,
    height: int = CARD_H,
) -> pygame.Surface:
    """Render a generic card back."""
    return render_card(None, width, height, face_down=True)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _render_face(card_def: Any, w: int, h: int) -> pygame.Surface:
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))

    # Card body background (rounded rect)
    pygame.draw.rect(surf, BG_CARD, (0, 0, w, h), border_radius=CARD_CORNER)

    # Header bar
    header_h = max(h // 6, 20)
    kind = getattr(card_def, "kind", "hero")
    card_class = getattr(card_def, "card_class", None)
    header_colour = CLASS_COLOURS.get(card_class or "", KIND_COLOURS.get(kind, BG_CARD))
    pygame.draw.rect(
        surf, header_colour, (0, 0, w, header_h),
        border_top_left_radius=CARD_CORNER,
        border_top_right_radius=CARD_CORNER,
    )

    # Card name in header
    name = getattr(card_def, "name", "???")
    name_font = get_font(max(w // 10, 10), bold=True)
    _draw_text_clipped(surf, name, name_font, TEXT_BRIGHT, 4, 2, w - 8, header_h - 4)

    # Kind badge (small text in the body, just below header)
    kind_font = get_font(max(w // 13, 8))
    kind_label = kind.replace("_", " ").title()
    if card_class:
        kind_label = f"{card_class.title()} {kind_label}"
    kind_surf = kind_font.render(kind_label, True, TEXT_DIM)
    surf.blit(kind_surf, (4, header_h + 2))

    # Card text (word-wrapped)
    text = getattr(card_def, "text", "")
    if text:
        text_font = get_font(max(w // 13, 8))
        text_top = header_h + kind_surf.get_height() + 6
        text_area_h = h - text_top - (h // 6) - 4
        _draw_wrapped_text(surf, text, text_font, TEXT, 4, text_top, w - 8, text_area_h)

    # Footer — roll threshold for heroes / monsters
    footer_h = max(h // 6, 18)
    footer_y = h - footer_h
    pygame.draw.rect(
        surf, _darken(header_colour, 0.6), (0, footer_y, w, footer_h),
        border_bottom_left_radius=CARD_CORNER,
        border_bottom_right_radius=CARD_CORNER,
    )
    footer_text = _footer_text(card_def)
    if footer_text:
        ft_font = get_font(max(w // 12, 9))
        _draw_text_clipped(
            surf, footer_text, ft_font, TEXT_BRIGHT, 4, footer_y + 2, w - 8, footer_h - 4
        )

    # Outline
    pygame.draw.rect(surf, _darken(header_colour, 0.8), (0, 0, w, h), 2, border_radius=CARD_CORNER)

    return surf


def _render_back(w: int, h: int) -> pygame.Surface:
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))

    pygame.draw.rect(surf, BG_CARD_BACK, (0, 0, w, h), border_radius=CARD_CORNER)

    # Decorative diamond pattern
    cx, cy = w // 2, h // 2
    size = min(w, h) // 4
    diamond = [
        (cx, cy - size),
        (cx + size, cy),
        (cx, cy + size),
        (cx - size, cy),
    ]
    pygame.draw.polygon(surf, (90, 70, 120), diamond)
    pygame.draw.polygon(surf, (110, 90, 140), diamond, 2)

    # Inner diamond
    size2 = size // 2
    diamond2 = [
        (cx, cy - size2),
        (cx + size2, cy),
        (cx, cy + size2),
        (cx - size2, cy),
    ]
    pygame.draw.polygon(surf, (70, 55, 100), diamond2)

    # Border
    pygame.draw.rect(surf, (100, 80, 130), (0, 0, w, h), 2, border_radius=CARD_CORNER)

    return surf


def _draw_highlight_border(surf: pygame.Surface, w: int, h: int) -> None:
    """Draw a bright glowing border around the card."""
    # Outer glow
    for i in range(3, 0, -1):
        alpha = 60 + (3 - i) * 40
        glow_colour = (*HIGHLIGHT[:3], alpha)
        glow_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(glow_surf, glow_colour, (0, 0, w, h), i + 1, border_radius=CARD_CORNER)
        surf.blit(glow_surf, (0, 0))
    # Solid border
    pygame.draw.rect(surf, HIGHLIGHT, (0, 0, w, h), 2, border_radius=CARD_CORNER)


def _apply_tapped(surf: pygame.Surface) -> pygame.Surface:
    """Rotate 90° clockwise and dim."""
    rotated = pygame.transform.rotate(surf, -90)
    dimmed = rotated.copy()
    dark = pygame.Surface(dimmed.get_size(), pygame.SRCALPHA)
    dark.fill((0, 0, 0, 80))
    dimmed.blit(dark, (0, 0))
    return dimmed


def _footer_text(card_def: Any) -> str:
    """Extract a concise footer string from a card definition."""
    # Heroes with abilities → show roll threshold
    ability = getattr(card_def, "ability", None)
    if ability is not None:
        roll = getattr(ability, "roll", None)
        if roll is not None:
            outcomes = getattr(roll, "outcomes", [])
            for band in outcomes:
                tag = getattr(band, "tag", None)
                if tag in ("success", "effect"):
                    bmin = getattr(band, "min", None)
                    if bmin is not None:
                        return f"{bmin}+ to succeed"

    # Monsters → show roll threshold
    roll = getattr(card_def, "roll", None)
    if roll is not None:
        outcomes = getattr(roll, "outcomes", [])
        for band in outcomes:
            tag = getattr(band, "tag", None)
            if tag in ("success", "slay"):
                bmin = getattr(band, "min", None)
                if bmin is not None:
                    return f"{bmin}+ to slay"

    kind = getattr(card_def, "kind", "")
    if kind == "modifier":
        return "Modifier"
    if kind == "challenge":
        return "Challenge"
    if kind == "item":
        return "Item"
    if kind == "magic":
        return "Magic"

    return ""


# ---------------------------------------------------------------------------
# Text rendering helpers
# ---------------------------------------------------------------------------


def _draw_text_clipped(
    surf: pygame.Surface,
    text: str,
    font: pygame.font.Font,
    colour: tuple[int, int, int],
    x: int,
    y: int,
    max_w: int,
    max_h: int,
) -> None:
    """Render a single line of text, clipped to fit."""
    rendered = font.render(text, True, colour)
    clip_rect = pygame.Rect(
        0, 0, min(rendered.get_width(), max_w), min(rendered.get_height(), max_h)
    )
    surf.blit(rendered, (x, y), clip_rect)


def _draw_wrapped_text(
    surf: pygame.Surface,
    text: str,
    font: pygame.font.Font,
    colour: tuple[int, int, int],
    x: int,
    y: int,
    max_w: int,
    max_h: int,
) -> None:
    """Word-wrap text into the given area."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        tw, _ = font.size(test)
        if tw <= max_w:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_h = font.get_linesize()
    for i, line in enumerate(lines):
        ty = y + i * line_h
        if ty + line_h > y + max_h:
            break
        rendered = font.render(line, True, colour)
        clip_rect = pygame.Rect(0, 0, min(rendered.get_width(), max_w), rendered.get_height())
        surf.blit(rendered, (x, ty), clip_rect)


def _darken(colour: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, int(c * factor)) for c in colour)  # type: ignore[return-value]


__all__ = [
    "CARD_CORNER",
    "CARD_H",
    "CARD_H_SMALL",
    "CARD_W",
    "CARD_W_SMALL",
    "clear_card_cache",
    "render_card",
    "render_card_back",
]

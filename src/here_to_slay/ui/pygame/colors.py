"""Centralised colour palette and theme constants for the pygame UI.

Class colours mirror the CLI palette (``ui/cli/render.py``) so a bard is
always magenta and a fighter is always red, regardless of the client.
"""

from __future__ import annotations

import pygame

# ---------------------------------------------------------------------------
# Card class colours (hero / leader class → colour)
# ---------------------------------------------------------------------------

CLASS_COLOURS: dict[str, tuple[int, int, int]] = {
    "bard": (180, 60, 180),       # magenta
    "fighter": (200, 50, 50),     # red
    "guardian": (50, 100, 200),   # blue
    "ranger": (50, 160, 50),     # green
    "thief": (200, 180, 40),     # yellow
    "wizard": (40, 180, 200),    # cyan
}

# ---------------------------------------------------------------------------
# Card kind colours
# ---------------------------------------------------------------------------

KIND_COLOURS: dict[str, tuple[int, int, int]] = {
    "hero": (220, 220, 230),
    "monster": (180, 50, 50),
    "item": (200, 180, 60),
    "magic": (60, 180, 200),
    "modifier": (60, 180, 80),
    "challenge": (200, 60, 60),
    "party_leader": (180, 80, 200),
}

# ---------------------------------------------------------------------------
# UI theme
# ---------------------------------------------------------------------------

BG = (24, 24, 32)
BG_PANEL = (36, 36, 48)
BG_PANEL_HOVER = (48, 48, 64)
BG_CARD = (50, 50, 60)
BG_CARD_BACK = (60, 50, 80)

TEXT = (230, 230, 240)
TEXT_DIM = (140, 140, 160)
TEXT_BRIGHT = (255, 255, 255)

HIGHLIGHT = (255, 220, 60)
HIGHLIGHT_GLOW = (255, 240, 100, 80)

BUTTON_BG = (60, 60, 80)
BUTTON_HOVER = (80, 80, 110)
BUTTON_TEXT = (230, 230, 240)
BUTTON_BORDER = (100, 100, 140)

TOAST_BG = (40, 40, 55, 220)
TOAST_TEXT = (255, 255, 255)

BORDER_ACTIVE = (100, 200, 100)
BORDER_INACTIVE = (70, 70, 90)

DICE_BG = (255, 255, 255)
DICE_FG = (20, 20, 30)
DICE_MODIFIER_POS = (80, 200, 80)
DICE_MODIFIER_NEG = (200, 80, 80)

# Victory / defeat
WIN_GOLD = (255, 215, 0)
LOSE_GREY = (120, 120, 130)

# Interstitial
INTERSTITIAL_BG = (20, 20, 28)
INTERSTITIAL_TEXT = (200, 200, 220)

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_font_cache: dict[tuple[int, bool], pygame.font.Font] = {}


def get_font(size: int, *, bold: bool = False) -> pygame.font.Font:
    """Return a cached pygame font. Uses the default system font."""
    key = (size, bold)
    if key not in _font_cache:
        font = pygame.font.SysFont("segoeui,arial,sans-serif", size, bold=bold)
        _font_cache[key] = font
    return _font_cache[key]


def clear_font_cache() -> None:
    _font_cache.clear()


__all__ = [
    "BG",
    "BG_CARD",
    "BG_CARD_BACK",
    "BG_PANEL",
    "BG_PANEL_HOVER",
    "BORDER_ACTIVE",
    "BORDER_INACTIVE",
    "BUTTON_BG",
    "BUTTON_BORDER",
    "BUTTON_HOVER",
    "BUTTON_TEXT",
    "CLASS_COLOURS",
    "DICE_BG",
    "DICE_FG",
    "DICE_MODIFIER_NEG",
    "DICE_MODIFIER_POS",
    "HIGHLIGHT",
    "HIGHLIGHT_GLOW",
    "INTERSTITIAL_BG",
    "INTERSTITIAL_TEXT",
    "KIND_COLOURS",
    "LOSE_GREY",
    "TEXT",
    "TEXT_BRIGHT",
    "TEXT_DIM",
    "TOAST_BG",
    "TOAST_TEXT",
    "WIN_GOLD",
    "clear_font_cache",
    "get_font",
]

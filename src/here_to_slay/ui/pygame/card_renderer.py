"""Draws a card from its ``CardDef``. Any card, at any size, no per-card code.

A face is composed rather than authored: an art window (a real scan when the
reference pack has one, a generated sigil when it does not), a class-coloured
frame, a name plate, the rules text, and a footer of *facts* pulled out of the
data — a Hero's roll threshold, a Monster's requirement, an Item's slot.
Because every part is derived from the definition, a card added to YAML this
afternoon renders correctly this afternoon.

Three things make it fast enough to call from a draw loop:

* **Level of detail.** A 44px mini in the opponent rail draws art and a rim; a
  132px board card draws the full face; the detail overlay draws everything
  plus flavour. Nobody wraps text nobody can read.
* **Everything is cached** by ``(def_id, size, flags)``. A board of forty cards
  is forty dict lookups per frame after the first.
* **Frames are separate from faces.** Selection glow, hover lift and the
  "tapped" rotation are applied to a cached face, so hovering a card does not
  re-render its text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.art import library
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.theme import C, M

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

CARD_W = M.CARD_W
CARD_H = M.CARD_H
CARD_W_SMALL = 84
CARD_H_SMALL = int(CARD_W_SMALL * M.CARD_ASPECT)
CARD_CORNER = 9

#: Width thresholds at which the face gains detail.
LOD_TINY = 58
LOD_SMALL = 96
LOD_FULL = 118


# ---------------------------------------------------------------------------
# Facts extracted from a definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CardFacts:
    """The handful of numbers a player actually looks for on a card.

    Read off the definition generically: a band tagged ``success`` is the
    threshold, whatever card it belongs to. Cards that tag nothing simply
    report nothing rather than the renderer guessing.
    """

    kind: str = "hero"
    card_class: str | None = None
    threshold: int | None = None
    threshold_label: str = ""
    requirement: str = ""
    slot: str = ""
    passive: bool = False
    triggers: int = 0
    reaction_window: str = ""

    @property
    def accent(self) -> tuple[int, int, int]:
        return T.class_colour(self.card_class, self.kind)

    @property
    def type_line(self) -> str:
        if self.card_class:
            label = "Leader" if self.kind == "party_leader" else self.kind.replace("_", " ")
            return f"{self.card_class.title()} {label.title()}"
        return self.kind.replace("_", " ").title()


def _band_threshold(roll: Any, tags: tuple[str, ...]) -> tuple[int | None, str]:
    """The lowest ``min`` among bands whose tag is interesting."""
    if roll is None:
        return None, ""
    for band in getattr(roll, "outcomes", ()) or ():
        tag = getattr(band, "tag", None)
        low = getattr(band, "min", None)
        if tag in tags and low is not None:
            return int(low), str(tag)
    return None, ""


def card_facts(card_def: Any) -> CardFacts:
    """Pull the display-worthy facts off any card definition."""
    if card_def is None:
        return CardFacts()
    kind = str(getattr(card_def, "kind", "hero") or "hero")
    card_class = getattr(card_def, "card_class", None)

    threshold: int | None = None
    label = ""
    passive = False

    ability = getattr(card_def, "ability", None)
    if ability is not None:
        passive = str(getattr(ability, "activation", "action")) == "passive"
        threshold, _tag = _band_threshold(getattr(ability, "roll", None), ("success", "effect"))
        if threshold is not None:
            label = "to use"

    roll = getattr(card_def, "roll", None)
    if roll is not None and threshold is None:
        threshold, _tag = _band_threshold(roll, ("slay", "success"))
        if threshold is not None:
            label = "to slay"

    slot = ""
    equip = getattr(card_def, "equip", None)
    if equip is not None:
        slot = "Equip"
    elif kind == "item":
        slot = "Item"

    reaction = getattr(card_def, "reaction", None)
    window = str(getattr(reaction, "window", "") or "") if reaction is not None else ""

    return CardFacts(
        kind=kind,
        card_class=card_class,
        threshold=threshold,
        threshold_label=label,
        requirement=str(getattr(card_def, "requirement_text", "") or ""),
        slot=slot,
        passive=passive,
        triggers=len(getattr(card_def, "triggers", ()) or ()),
        reaction_window=window,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_face_cache: dict[tuple, pygame.Surface] = {}
_back_cache: dict[tuple[int, int, int], pygame.Surface] = {}


def clear_card_cache() -> None:
    _face_cache.clear()
    _back_cache.clear()


def cache_size() -> int:
    return len(_face_cache) + len(_back_cache)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_card(
    card_def: Any,
    width: int = CARD_W,
    height: int = CARD_H,
    *,
    tapped: bool = False,
    highlighted: bool = False,
    face_down: bool = False,
    dimmed: bool = False,
    selected: bool = False,
    detail: bool = False,
) -> pygame.Surface:
    """A card surface. Cached — safe to call every frame for every card.

    ``highlighted`` marks a legal target, ``selected`` a confirmed pick, and
    ``dimmed`` something the player may look at but not touch.
    """
    width, height = max(6, int(width)), max(8, int(height))
    key = (
        getattr(card_def, "id", None) if card_def is not None else "__back__",
        width, height, tapped, highlighted, face_down, dimmed, selected, detail,
    )
    hit = _face_cache.get(key)
    if hit is not None:
        return hit

    if face_down or card_def is None:
        surf = render_card_back(width, height)
    else:
        surf = _face(card_def, width, height, detail=detail)

    if dimmed:
        surf = _dim(surf, 118)
    if highlighted or selected:
        surf = _with_target_ring(surf, selected=selected)
    if tapped:
        surf = _tapped(surf)

    _face_cache[key] = surf
    return surf


def render_card_back(width: int = CARD_W, height: int = CARD_H, *, tone: int = 0) -> pygame.Surface:
    """The reverse of a card. ``tone`` picks a hue so decks read apart."""
    width, height = max(6, int(width)), max(8, int(height))
    key = (width, height, tone)
    hit = _back_cache.get(key)
    if hit is not None:
        return hit

    radius = _radius(width)
    surf = T.surface((width, height))
    base_a = (C.CARD_BACK_A, (96, 46, 58), (34, 62, 96))[tone % 3]
    base_b = (C.CARD_BACK_B, (44, 18, 30), (16, 30, 56))[tone % 3]
    mark = (C.CARD_BACK_MARK, (238, 152, 150), (150, 196, 240))[tone % 3]

    T.round_rect(surf, pygame.Rect(0, 0, width, height), base_b, radius=radius)
    body = pygame.Rect(2, 2, width - 4, height - 4)
    inner = T.surface(body.size)
    inner.blit(T.vgradient(body.width, body.height, base_a, base_b), (0, 0))
    mask = T.surface(body.size)
    T.round_rect(mask, pygame.Rect(0, 0, body.width, body.height), (255, 255, 255, 255),
                 radius=max(2, radius - 2))
    inner.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(inner, body.topleft)

    cx, cy = width // 2, height // 2
    reach = int(min(width, height) * 0.3)

    # A lattice of diamonds — the classic card-back read, scaled to fit.
    if width >= 40:
        step = max(9, width // 7)
        for gy in range(-step, height + step, step):
            for gx in range(-step, width + step, step):
                off = (step // 2) if (gy // step) % 2 else 0
                px, py = gx + off, gy
                s = max(2, step // 6)
                pygame.draw.polygon(
                    surf, T.alpha(mark, 26),
                    [(px, py - s), (px + s, py), (px, py + s), (px - s, py)],
                )

    T.blit_glow(surf, (cx, cy), int(reach * 2.1), T.alpha(mark, 52), power=2.2)
    # A rotated square cartouche rather than rings: circles on a card back read
    # as a dartboard, and the diamond echoes the lattice behind it.
    if width >= 34:
        for scale, a in ((1.0, 120), (0.66, 190)):
            r = max(3, int(reach * scale))
            pygame.draw.polygon(
                surf, T.alpha(mark, a),
                [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                max(1, width // 80),
            )
    T.star(surf, (cx, cy), max(3, int(reach * 0.46)), T.alpha(C.GOLD_PALE, 220),
           points=4, inner=0.28, rotation=math.pi / 4)

    T.round_rect(surf, pygame.Rect(0, 0, width, height), T.alpha(mark, 90), radius=radius, width=1)
    _back_cache[key] = surf
    return surf


def render_card_detail(card_def: Any, width: int = 340) -> pygame.Surface:
    """The big hover/inspect face: full text, facts, and flavour."""
    return render_card(card_def, *T.card_size(width), detail=True)


# ---------------------------------------------------------------------------
# Face composition
# ---------------------------------------------------------------------------


def _radius(width: int) -> int:
    return max(3, min(18, int(width * 0.075)))


def _face(card_def: Any, w: int, h: int, *, detail: bool = False) -> pygame.Surface:
    facts = card_facts(card_def)
    accent = facts.accent
    radius = _radius(w)
    surf = T.surface((w, h))

    # -- body -------------------------------------------------------------
    T.round_rect(surf, pygame.Rect(0, 0, w, h), T.shade(accent, 0.42), radius=radius)
    paper = pygame.Rect(2, 2, w - 4, h - 4)
    body = T.surface(paper.size)
    body.blit(T.vgradient(paper.width, paper.height, C.CARD_PAPER,
                          T.mix(C.CARD_PAPER, accent, 0.20)), (0, 0))
    _apply_round_mask(body, max(2, radius - 2))
    surf.blit(body, paper.topleft)

    if w < LOD_TINY:
        _mini_face(surf, card_def, facts, w, h, radius)
        return surf

    # -- art window -------------------------------------------------------
    pad = max(3, w // 24)
    # The detail view gives the art a little back to the rules text: it is
    # opened *to read the card*, and a clipped ability is the one thing it
    # must not do.
    art_h = int(h * (0.40 if detail else (0.44 if w >= LOD_FULL else 0.58)))
    art_rect = pygame.Rect(pad, pad, w - pad * 2, art_h)
    art = library().art(card_def, art_rect.size)
    framed = art.copy()
    _apply_round_mask(framed, max(2, radius - 3))
    surf.blit(framed, art_rect.topleft)
    T.round_rect(surf, art_rect, T.alpha(T.shade(accent, 0.5), 190),
                 radius=max(2, radius - 3), width=1)
    # A gradient hem under the art so the name plate does not float.
    hem_h = max(4, art_h // 5)
    surf.blit(
        T.vgradient(art_rect.width, hem_h, (0, 0, 0, 0), T.alpha(T.shade(accent, 0.3), 170)),
        (art_rect.left, art_rect.bottom - hem_h),
    )

    # -- class pip --------------------------------------------------------
    if w >= LOD_SMALL:
        pip_r = max(8, w // 11)
        pip_c = (art_rect.left + pip_r + 2, art_rect.top + pip_r + 2)
        pygame.draw.circle(surf, T.shade(accent, 0.3), pip_c, pip_r + 2)
        pygame.draw.circle(surf, accent, pip_c, pip_r)
        pygame.draw.circle(surf, T.alpha(C.INK_BRIGHT, 90), pip_c, pip_r, 1)
        draw_icon(
            surf, card_icon_name(facts.kind, facts.card_class), pip_c,
            int(pip_r * 1.25), T.readable_ink(accent),
        )

    # -- name plate -------------------------------------------------------
    name = str(getattr(card_def, "name", "?"))
    plate_h = max(14, int(h * 0.11))
    plate = pygame.Rect(pad, art_rect.bottom + max(1, pad // 2), w - pad * 2, plate_h)
    T.round_rect(surf, plate, T.alpha(T.shade(accent, 0.55), 235), radius=max(2, radius - 4))
    name_font = T.fit_line(name.upper(), max(9, int(plate_h * 0.78)), 7, plate.width - 10,
                           family=T.FONT_DISPLAY, bold=True)
    T.text(surf, name.upper(), plate.center, name_font, C.INK_BRIGHT, anchor="center",
           max_width=plate.width - 6, shadow=(0, 0, 0, 160))

    if w < LOD_FULL:
        _footer_strip(surf, facts, w, h, pad, compact=True)
        return surf

    # -- rules text -------------------------------------------------------
    footer_h = max(16, int(h * 0.115))
    text_rect = pygame.Rect(
        pad + 2, plate.bottom + max(2, pad // 2),
        w - pad * 2 - 4, h - plate.bottom - footer_h - pad * 2,
    )
    blocks: list[tuple[str, tuple[int, int, int], bool]] = []
    if facts.requirement:
        blocks.append((f"Requires: {facts.requirement}", T.shade(accent, 0.75), True))
    text_value = str(getattr(card_def, "text", "") or "")
    if text_value:
        blocks.append((text_value, C.CARD_INK, False))

    # Scaled with the card, but capped: past ~17px the words stop fitting and
    # start being elided, which is the opposite of what a bigger card is for.
    size = max(8, min(17, int(w * (0.085 if detail else 0.079))))
    y = text_rect.top
    for value, colour, is_bold in blocks:
        if y >= text_rect.bottom - 4:
            break
        fnt = T.ui(size, bold=is_bold)
        used = T.draw_wrapped(
            surf, value,
            pygame.Rect(text_rect.left, y, text_rect.width, text_rect.bottom - y),
            fnt, colour, line_gap=1,
        )
        y += used + 3

    if detail and facts.passive:
        T.text(surf, "Passive", (text_rect.left, text_rect.bottom - 12),
               T.ui(max(8, w // 14), italic=True), C.CARD_INK_DIM, shadow=None)

    _footer_strip(surf, facts, w, h, pad, compact=False)
    return surf


def _mini_face(
    surf: pygame.Surface, card_def: Any, facts: CardFacts, w: int, h: int, radius: int
) -> None:
    """Below ~58px there is no room for words: art, rim, one letter."""
    art = library().art(card_def, (w - 2, h - 2))
    framed = art.copy()
    _apply_round_mask(framed, max(2, radius - 1))
    surf.blit(framed, (1, 1))
    surf.blit(
        T.vgradient(w - 2, max(4, h // 3), (0, 0, 0, 0), (0, 0, 0, 190)),
        (1, h - 1 - max(4, h // 3)),
    )
    initial = str(getattr(card_def, "name", "?"))[:1].upper()
    T.text(surf, initial, (w // 2, h - 3), T.display(max(8, int(h * 0.26))),
           C.INK_BRIGHT, anchor="midbottom")
    T.round_rect(surf, pygame.Rect(0, 0, w, h), T.alpha(facts.accent, 220), radius=radius, width=2)


def _footer_strip(
    surf: pygame.Surface, facts: CardFacts, w: int, h: int, pad: int, *, compact: bool
) -> None:
    """Roll threshold on the left, type line on the right."""
    strip_h = max(13, int(h * 0.105))
    strip = pygame.Rect(pad, h - strip_h - pad, w - pad * 2, strip_h)
    T.round_rect(surf, strip, T.alpha(T.shade(facts.accent, 0.5), 225),
                 radius=max(2, strip_h // 2))

    left = strip.left + 6
    if facts.threshold is not None:
        label = f"{facts.threshold}+"
        pill_font = T.ui(max(8, strip_h - 5), bold=True)
        pill_w = min(strip.width // 2, pill_font.size(label)[0] + 12)
        pill_rect = pygame.Rect(strip.left + 2, strip.top + 2, pill_w, strip_h - 4)
        T.pill(surf, pill_rect, label, bg=C.GOLD, fg=C.INK_DARK, fnt=pill_font)
        left = pill_rect.right + 4

    # The type line is the first thing to give up room, so it shrinks rather
    # than truncating a word the player needs ("Fighter Hero", not "Fighter H...").
    right_label = T.CLASS_SHORT.get(facts.card_class or "", "") if compact else facts.type_line
    if not right_label:
        right_label = facts.kind[:3].upper() if compact else facts.type_line
    room = max(10, strip.right - left - 8)
    top_size = max(7, strip_h - 6)
    label_font = T.ui(top_size, bold=True)
    for size in range(top_size, 6, -1):
        label_font = T.ui(size, bold=True)
        if label_font.size(right_label)[0] <= room:
            break
    T.text(surf, right_label, (strip.right - 6, strip.centery), label_font,
           C.INK_BRIGHT, anchor="midright", max_width=room, shadow=None)


# ---------------------------------------------------------------------------
# Surface effects
# ---------------------------------------------------------------------------


def _apply_round_mask(surf: pygame.Surface, radius: int) -> None:
    """Clip ``surf`` in place to a rounded rectangle."""
    w, h = surf.get_size()
    mask = T.surface((w, h))
    pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, w, h), border_radius=radius)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)


def _dim(surf: pygame.Surface, strength: int) -> pygame.Surface:
    out = surf.copy()
    veil = T.surface(out.get_size())
    veil.fill((6, 6, 16, strength))
    veil.blit(out, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    out.blit(veil, (0, 0))
    return out


def _with_target_ring(surf: pygame.Surface, *, selected: bool) -> pygame.Surface:
    """Paint a glowing ring *inside* the card bounds.

    Deliberately size-preserving: every caller lays cards out on a grid, and a
    surface that grew when highlighted would shift its neighbours. The glow
    that bleeds *outside* a card is drawn by the sprite (which knows where the
    card sits on screen) rather than baked into the face.
    """
    w, h = surf.get_size()
    out = surf.copy()
    colour = C.GOOD if selected else C.GOLD
    radius = _radius(w)
    band = max(2, w // 26)

    for i in range(band, 0, -1):
        a = int(120 * (1 - i / (band + 1)) ** 1.4) + (70 if selected else 45)
        T.round_rect(
            out, pygame.Rect(i - 1, i - 1, w - (i - 1) * 2, h - (i - 1) * 2),
            T.alpha(colour, min(235, a)), radius=max(2, radius - i + 1), width=2,
        )
    T.round_rect(out, pygame.Rect(0, 0, w, h), colour, radius=radius, width=2)
    if selected and w >= LOD_TINY:
        r = max(7, w // 11)
        centre = (w - r - 4, r + 4)
        pygame.draw.circle(out, C.GOOD, centre, r)
        pygame.draw.circle(out, C.INK_BRIGHT, centre, r, 2)
        draw_icon(out, "check", centre, int(r * 1.4), C.INK_DARK)
    return out


def _tapped(surf: pygame.Surface) -> pygame.Surface:
    """Rotate 90 degrees and dim — the tabletop convention for "used"."""
    rotated = pygame.transform.rotate(surf, -90)
    return _dim(rotated, 76)


__all__ = [
    "CARD_CORNER",
    "CARD_H",
    "CARD_H_SMALL",
    "CARD_W",
    "CARD_W_SMALL",
    "LOD_FULL",
    "LOD_SMALL",
    "LOD_TINY",
    "CardFacts",
    "cache_size",
    "card_facts",
    "clear_card_cache",
    "render_card",
    "render_card_back",
    "render_card_detail",
]

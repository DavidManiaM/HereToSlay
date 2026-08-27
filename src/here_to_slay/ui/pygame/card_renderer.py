"""Draws a card from its ``CardDef``. Any card, at any size, no per-card code.

A table face is the card's artwork, full-bleed, with a thin class-coloured rim.
Rules, names and facts are *not* typeset onto the face — they live on the hover
/ inspect panel so the illustration stays visible at every size. When the
reference pack has no file, a generated sigil fills the same rectangle.

Three things make it fast enough to call from a draw loop:

* **One composition for every size.** Tiny rail minis and board cards are the
  same full-bleed art with a rim; hover/inspect draws the same face plus a
  Romanian rules column beside it.
* **Everything is cached** by ``(def_id, size, flags)``, in *bounded* LRUs, and
  the plain face is cached separately from the flagged variants — so a card that
  is on screen both dimmed and highlighted composes once. A board of forty cards
  is forty dict lookups per frame after the first.
* **Frames are separate from faces.** Selection glow, hover lift and the
  "tapped" rotation are applied to a cached face, so hovering a card does not
  re-render its text.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import pygame

from here_to_slay.ui import lexicon as L
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

#: Draw faces at this multiple then smoothscale down (anti-pixelation).
_SUPERSAMPLE = 2


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
    tags: tuple[str, ...] = ()
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
        return L.type_line(self.kind, self.card_class, tags=self.tags)


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
            label = "pentru abilitate"

    roll = getattr(card_def, "roll", None)
    if roll is not None and threshold is None:
        threshold, _tag = _band_threshold(roll, ("slay", "success"))
        if threshold is not None:
            label = "pentru împrietenire"

    slot = ""
    equip = getattr(card_def, "equip", None)
    if equip is not None or kind == "item":
        tags = tuple(str(t) for t in (getattr(card_def, "tags", ()) or ()))
        slot = L.HACK if "cursed" in {t.lower() for t in tags} else L.CHEAT
    else:
        tags = tuple(str(t) for t in (getattr(card_def, "tags", ()) or ()))

    reaction = getattr(card_def, "reaction", None)
    window = str(getattr(reaction, "window", "") or "") if reaction is not None else ""

    return CardFacts(
        kind=kind,
        card_class=card_class,
        tags=tags,
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

#: How many finished faces to keep. A busy four-player board draws forty-odd
#: cards at three or four sizes, so this holds a whole screen several times over
#: — but it is a *bound*, which the plain dict this replaced was not: every
#: distinct pixel size ever asked for stayed resident for the life of the
#: process, and dragging a window edge asks for a new set of sizes on every
#: frame of the drag. Six camera cycles alone took the cache from 49 surfaces
#: to 608, and nothing ever came out again.
FACE_CACHE_LIMIT = 384
#: Composed faces before flags and exact sizing. Fewer, because they are shared
#: by every flag combination of the same card.
BASE_CACHE_LIMIT = 192
BACK_CACHE_LIMIT = 64

_face_cache: OrderedDict[tuple, pygame.Surface] = OrderedDict()
_base_cache: OrderedDict[tuple, pygame.Surface] = OrderedDict()
_back_cache: OrderedDict[tuple[int, int, int], pygame.Surface] = OrderedDict()


def clear_card_cache() -> None:
    _face_cache.clear()
    _base_cache.clear()
    _back_cache.clear()


def cache_size() -> int:
    return len(_face_cache) + len(_base_cache) + len(_back_cache)


def _remember(
    cache: OrderedDict[Any, pygame.Surface], key: Any, surf: pygame.Surface, limit: int
) -> pygame.Surface:
    """Store, evicting the least recently used once ``limit`` is passed."""
    cache[key] = surf
    while len(cache) > limit:
        cache.popitem(last=False)
    return surf


def _recall(cache: OrderedDict[Any, pygame.Surface], key: Any) -> pygame.Surface | None:
    surf = cache.get(key)
    if surf is not None:
        cache.move_to_end(key)
    return surf


# Composing at a *rounded* size and rescaling to the exact one was tried here
# and measured worse, so it is not in the code: card widths move by more than
# any tolerable rounding step, so nearly every frame still missed — and now paid
# a rescale of forty cards on top. Exact composition, over a camera change:
# 35 ms/frame. Rounded to ~2%: 51 ms. Rounded to ~8%, which is already visibly
# soft: 49 ms. The saving never covers the rescale.


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
    hit = _recall(_face_cache, key)
    if hit is not None:
        return hit

    if face_down or card_def is None:
        surf = render_card_back(width, height)
    else:
        surf = _sized_face(card_def, width, height, detail=detail)

    if dimmed:
        surf = _dim(surf)
    if highlighted or selected:
        surf = _with_target_ring(surf, selected=selected)
    if tapped:
        surf = _tapped(surf)

    return _remember(_face_cache, key, surf, FACE_CACHE_LIMIT)


def _sized_face(card_def: Any, width: int, height: int, *, detail: bool) -> pygame.Surface:
    """The plain face, before any flag is applied. Cached in its own right.

    Separate from :data:`_face_cache` because ``dimmed``, ``highlighted``,
    ``selected`` and ``tapped`` all derive from this one surface: a Monster that
    is on screen dimmed in one panel and highlighted in another used to compose
    its whole face twice.
    """
    key = (getattr(card_def, "id", None), width, height, detail)
    base = _recall(_base_cache, key)
    if base is not None:
        return base
    if width >= LOD_TINY and _SUPERSAMPLE > 1:
        hi = _face(card_def, width * _SUPERSAMPLE, height * _SUPERSAMPLE, detail=detail)
        base = pygame.transform.smoothscale(hi, (width, height))
    else:
        base = _face(card_def, width, height, detail=detail)
    return _remember(_base_cache, key, base, BASE_CACHE_LIMIT)


def render_card_back(width: int = CARD_W, height: int = CARD_H, *, tone: int = 0) -> pygame.Surface:
    """The reverse of a card. ``tone`` picks a hue so decks read apart."""
    width, height = max(6, int(width)), max(8, int(height))
    key = (width, height, tone)
    hit = _recall(_back_cache, key)
    if hit is not None:
        return hit

    if width >= LOD_TINY and _SUPERSAMPLE > 1:
        hi = _paint_card_back(width * _SUPERSAMPLE, height * _SUPERSAMPLE, tone=tone)
        surf = pygame.transform.smoothscale(hi, (width, height))
    else:
        surf = _paint_card_back(width, height, tone=tone)

    return _remember(_back_cache, key, surf, BACK_CACHE_LIMIT)


def _paint_card_back(width: int, height: int, *, tone: int = 0) -> pygame.Surface:
    radius = _radius(width)
    surf = T.surface((width, height))
    base_a = (C.CARD_BACK_A, (72, 120, 168), (48, 88, 140))[tone % 3]
    base_b = (C.CARD_BACK_B, (28, 56, 92), (18, 36, 64))[tone % 3]
    mark = (C.CARD_BACK_MARK, (220, 236, 250), (186, 216, 240))[tone % 3]

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
    """Artwork as the whole card. ``detail`` is unused: hover text is a panel."""
    del detail
    facts = card_facts(card_def)
    accent = facts.accent
    radius = _radius(w)
    surf = T.surface((w, h))

    art = library().art(card_def, (w, h))
    framed = art.copy()
    _apply_round_mask(framed, radius)
    surf.blit(framed, (0, 0))

    rim = max(2, min(5, w // 36))
    T.round_rect(surf, pygame.Rect(0, 0, w, h), T.alpha(accent, 235), radius=radius, width=rim)
    T.round_rect(surf, pygame.Rect(0, 0, w, h), T.alpha(C.INK_DARK, 110), radius=radius, width=1)
    return surf


# ---------------------------------------------------------------------------
# Surface effects
# ---------------------------------------------------------------------------


def _apply_round_mask(surf: pygame.Surface, radius: int) -> None:
    """Clip ``surf`` in place to a rounded rectangle."""
    w, h = surf.get_size()
    mask = T.surface((w, h))
    pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, w, h), border_radius=radius)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)


def _dim(surf: pygame.Surface, *, mix: float = 0.55, shade: float = 0.88) -> pygame.Surface:
    """Desaturate and darken while keeping the face fully opaque.

    Ghostly whole-surface alpha made unplayable cards look transparent; a grey
    multiply keeps them solid paper that simply is not in play. Uses blend
    modes only (no numpy / surfarray).
    """
    out = surf.copy()
    w, h = out.get_size()
    # Pull toward grey, then darken — both stay fully opaque.
    grey = int(128 + 40 * (1.0 - mix))
    level = max(40, min(255, int(255 * shade)))
    desat = T.surface((w, h))
    desat.fill((grey, grey, grey, 255))
    out.blit(desat, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    dark = T.surface((w, h))
    dark.fill((level, level, level, 255))
    out.blit(dark, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    # Darker rim so dimmed cards still read as solid objects.
    T.round_rect(
        out, pygame.Rect(0, 0, w, h), T.alpha(C.INK_DARK, 90),
        radius=_radius(w), width=2,
    )
    return out


def _with_target_ring(surf: pygame.Surface, *, selected: bool) -> pygame.Surface:
    """Paint a crisp selection ring *inside* the card bounds.

    Size-preserving so layout grids do not shift. No additive halo — the face
    stays opaque paper with a solid cyan/green rim.
    """
    w, h = surf.get_size()
    out = surf.copy()
    colour = C.GOOD if selected else C.CYAN
    radius = _radius(w)
    width = 3 if selected else 2
    # Outer crisp stroke, then a slightly inset underline stroke for weight.
    T.round_rect(out, pygame.Rect(0, 0, w, h), colour, radius=radius, width=width)
    T.round_rect(
        out, pygame.Rect(2, 2, w - 4, h - 4),
        T.alpha(colour, 200), radius=max(2, radius - 2), width=1,
    )
    if selected and w >= LOD_TINY:
        r = max(7, w // 11)
        centre = (w - r - 4, r + 4)
        pygame.draw.circle(out, C.GOOD, centre, r)
        pygame.draw.circle(out, C.INK_BRIGHT, centre, r, 2)
        draw_icon(out, "check", centre, int(r * 1.4), C.INK_DARK)
    return out


def _tapped(surf: pygame.Surface) -> pygame.Surface:
    """Rotate 90 degrees and dim — the tabletop convention for "used"."""
    rotated = pygame.transform.rotozoom(surf, -90, 1.0)
    return _dim(rotated, mix=0.35, shade=0.88)


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

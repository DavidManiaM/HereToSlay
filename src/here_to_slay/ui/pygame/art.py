"""Card artwork: find it on disk, or invent something that looks deliberate.

Roughly half of the 88 base card definitions have a matching image in
``assets/ref/here_to_slay/`` (all six Leaders, a third of the Heroes, five
Monsters). The rest would be a blank rectangle, and a board where half the
cards are blank rectangles looks broken rather than incomplete.

So this module has two halves:

* :class:`ArtLibrary` resolves ``base.hero.bear_claw`` to a file — first from
  ``assets/ref/here_to_vibe/`` (Here to Vibe (Code) art), then from
  ``assets/ref/here_to_slay/``. Within a pack it uses ``meta/catalog.json``'s
  ``game_id`` field, then slug against the ``images/by_type/`` folders, then a
  couple of naming fixups (Leaders are filed as ``the_shadow_claw``).
  Surfaces are cached per requested size.
* When there is no file, :func:`procedural_art` draws a **sigil**: a coloured
  field plus a geometric emblem seeded from the card id, so every card gets a
  distinct, stable, obviously-intentional image. Nothing on screen ever says
  "missing".

The library is engine-agnostic on purpose — it takes a ``CardDef``-shaped
object and reads only ``id``, ``kind`` and ``card_class``, so the dev console
can ask for art for a card that is not in play.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.theme import C

#: Original reference pack (fallback), relative to the repository root.
REF_ROOT = Path("assets/ref/here_to_slay")

#: Here to Vibe (Code) art. Checked first so new portraits win over scans.
VIBE_ROOT = Path("assets/ref/here_to_vibe")

#: Extensions to try, in preference order. The reference pack stores WebP data
#: under ``.png`` names (SDL_image sniffs the header, so this just works).
EXTENSIONS = (".png", ".jpeg", ".jpg", ".webp")

#: ``kind`` -> the ``images/by_type`` folder that holds it.
KIND_FOLDERS: dict[str, tuple[str, ...]] = {
    "hero": ("heroes", "unknown"),
    "monster": ("monsters", "unknown"),
    "item": ("items", "unknown"),
    "magic": ("magic", "unknown"),
    "party_leader": ("leaders", "unknown"),
    "modifier": ("modifiers", "misc", "unknown"),
    "challenge": ("challenges", "misc", "unknown"),
    "misc": ("misc", "unknown"),
}


def _repo_root() -> Path:
    """The project root, found by walking up from this file.

    ``src/here_to_slay/ui/pygame/art.py`` -> five parents up. Falls back to the
    working directory so a relocated install still starts (with placeholders).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "assets").is_dir() or (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# Procedural art
# ---------------------------------------------------------------------------


def _seed_of(card_id: str) -> int:
    return int.from_bytes(hashlib.sha256(card_id.encode("utf-8")).digest()[:8], "big")


class _Sprinkle:
    """A tiny deterministic PRNG.

    ``core/`` is forbidden from importing ``random`` because the game must be
    reproducible; the same discipline is worth keeping here for a different
    reason — placeholder art must look *identical* every launch, or the board
    would shimmer between runs and screenshots would never match.
    """

    __slots__ = ("_x",)

    def __init__(self, seed: int) -> None:
        self._x = (seed ^ 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF

    def _next(self) -> int:
        self._x = (self._x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self._x
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def unit(self) -> float:
        return (self._next() >> 11) / float(1 << 53)

    def between(self, low: float, high: float) -> float:
        return low + (high - low) * self.unit()

    def below(self, n: int) -> int:
        return self._next() % max(1, n)

    def pick(self, seq: Any) -> Any:
        return seq[self.below(len(seq))]


def procedural_art(
    card_id: str,
    kind: str,
    card_class: str | None,
    size: tuple[int, int],
) -> pygame.Surface:
    """A distinctive emblem for a card with no scan, seeded by its id."""
    width, height = max(8, size[0]), max(8, size[1])
    rng = _Sprinkle(_seed_of(card_id))
    accent = T.class_colour(card_class, kind)
    deep = T.shade(accent, 0.24)
    mid = T.mix(deep, C.FELT_DEEP, 0.45)

    surf = T.surface((width, height))
    surf.blit(T.vgradient(width, height, T.mix(mid, accent, 0.16), T.shade(mid, 0.55)), (0, 0))

    cx, cy = width // 2, int(height * 0.47)
    reach = int(min(width, height) * 0.46)

    # A halo, so the emblem sits in light rather than on a flat field.
    T.blit_glow(surf, (cx, cy), int(reach * 1.7), T.alpha(accent, 60), power=2.4)

    # Concentric rings of varying weight — the "arcane seal" read.
    rings = 2 + rng.below(3)
    for i in range(rings):
        r = int(reach * (0.42 + 0.24 * i))
        a = 40 + rng.below(70)
        pygame.draw.circle(surf, T.alpha(accent, a), (cx, cy), r, 1 + rng.below(2))

    # A rotated polygon: the silhouette that makes cards tell apart at a glance.
    sides = 3 + rng.below(5)
    spin = rng.between(0.0, math.tau)
    poly = [
        (
            cx + math.cos(spin + i * math.tau / sides) * reach * 0.72,
            cy + math.sin(spin + i * math.tau / sides) * reach * 0.72,
        )
        for i in range(sides)
    ]
    pygame.draw.polygon(surf, T.alpha(T.shade(accent, 1.15), 44), poly)
    pygame.draw.polygon(surf, T.alpha(T.shade(accent, 1.4), 150), poly, max(1, width // 60))

    # Orbiting motes.
    for _ in range(4 + rng.below(6)):
        a = rng.between(0.0, math.tau)
        d = rng.between(reach * 0.8, reach * 1.25)
        r = max(1, int(rng.between(1.2, 3.4) * width / 130))
        px, py = int(cx + math.cos(a) * d), int(cy + math.sin(a) * d)
        pygame.draw.circle(surf, T.alpha(C.GOLD_PALE, 90 + rng.below(120)), (px, py), r)

    # The class (or kind) emblem, centred. Vector, so it never falls back to a
    # hollow box on a machine without a symbol font.
    emblem = T.surface((reach * 2 + 8, reach * 2 + 8))
    ec = (reach + 4, reach + 4)
    draw_icon(emblem, card_icon_name(kind, card_class), ec, int(reach * 1.15),
              T.mix(C.INK_BRIGHT, accent, 0.25))
    ghost = emblem.copy()
    ghost.fill((0, 0, 0, 140), special_flags=pygame.BLEND_RGBA_MULT)
    surf.blit(ghost, (cx - ec[0] + 2, cy - ec[1] + 3))
    surf.blit(emblem, (cx - ec[0], cy - ec[1]))

    # Horizon band, which reads as ground and stops the art feeling weightless.
    band_y = int(height * 0.78)
    surf.blit(
        T.vgradient(width, height - band_y, T.alpha(deep, 0), T.alpha(T.shade(deep, 0.6), 220)),
        (0, band_y),
    )
    return surf


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


@dataclass
class ArtLibrary:
    """Resolves and caches card artwork.

    ``root`` is the repository root. By default the library indexes
    ``assets/ref/here_to_vibe`` first, then ``assets/ref/here_to_slay``.
    Pass ``ref`` to use a single pack instead (tests / fixtures).
    """

    root: Path = field(default_factory=_repo_root)
    ref: Path | None = None
    #: def_id -> resolved file, or None once we know there is nothing to find
    _paths: dict[str, Path | None] = field(default_factory=dict, repr=False)
    _surfaces: dict[tuple[str, int, int], pygame.Surface] = field(default_factory=dict, repr=False)
    _catalog: dict[str, Path] = field(default_factory=dict, repr=False)
    _slugs: dict[str, Path] = field(default_factory=dict, repr=False)
    _scanned: bool = field(default=False, repr=False)
    _packs: tuple[Path, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        if self.ref is not None:
            self._packs = (self.ref,) if self.ref.is_dir() else ()
            return
        packs: list[Path] = []
        for rel in (VIBE_ROOT, REF_ROOT):
            path = self.root / rel
            if path.is_dir():
                packs.append(path)
        self._packs = tuple(packs)
        self.ref = self._packs[0] if self._packs else self.root / REF_ROOT

    # -- index ------------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self._packs)

    def _scan(self) -> None:
        """Index the reference packs once, lazily. Earlier packs win."""
        if self._scanned:
            return
        self._scanned = True
        for i, pack in enumerate(self._packs):
            self._index_pack(pack, overwrite=(i == 0))

    def _index_pack(self, pack: Path, *, overwrite: bool) -> None:
        catalog = pack / "meta" / "catalog.json"
        if catalog.is_file():
            try:
                entries = json.loads(catalog.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entries = []
            for entry in entries if isinstance(entries, list) else []:
                images = entry.get("images") or []
                if not images:
                    continue
                path = None
                for rel in images:
                    candidate = pack / str(rel)
                    if candidate.is_file():
                        path = candidate
                        break
                if path is None:
                    continue
                game_id = entry.get("game_id")
                if game_id:
                    self._catalog.setdefault(str(game_id), path)
                slug = entry.get("slug")
                if slug:
                    self._slugs.setdefault(str(slug), path)

        by_type = pack / "images" / "by_type"
        if not by_type.is_dir():
            return
        for folder in sorted(by_type.iterdir()):
            if not folder.is_dir():
                continue
            # "unknown" holds pre-reorganisation duplicates; typed folders win.
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() not in EXTENSIONS:
                    continue
                key = path.stem.lower()
                if folder.name == "unknown" or not overwrite:
                    self._slugs.setdefault(key, path)
                else:
                    self._slugs[key] = path

    # -- resolution -------------------------------------------------------

    def path_for(self, card_def: Any) -> Path | None:
        """The image file for a card definition, or ``None``."""
        if card_def is None:
            return None
        def_id = str(getattr(card_def, "id", "") or "")
        if not def_id:
            return None
        if def_id in self._paths:
            return self._paths[def_id]
        resolved = self._resolve(card_def, def_id)
        self._paths[def_id] = resolved
        return resolved

    def _resolve(self, card_def: Any, def_id: str) -> Path | None:
        self._scan()

        # An explicit `art:` in the YAML always wins — that is the whole point
        # of the field, and it is how a mod ships its own images.
        declared = getattr(card_def, "art", None)
        if declared:
            for base in (self.root / "assets", *self._packs, self.root):
                candidate = Path(base) / str(declared)
                if candidate.is_file():
                    return candidate

        if def_id in self._catalog:
            return self._catalog[def_id]

        kind = str(getattr(card_def, "kind", "") or "")
        slug = def_id.rsplit(".", 1)[-1].lower()
        # Leaders are filed under their spoken name ("the_shadow_claw").
        candidates = [slug]
        if kind == "party_leader":
            candidates.insert(0, f"the_{slug}")
        candidates.append(slug.removeprefix("the_"))

        for key in candidates:
            hit = self._slugs.get(key)
            if hit is not None:
                return hit

        # Last resort: the by_type folders for this kind, by filename.
        for pack in self._packs:
            for folder in KIND_FOLDERS.get(kind, ()):
                directory = pack / "images" / "by_type" / folder
                if not directory.is_dir():
                    continue
                for key in candidates:
                    for ext in EXTENSIONS:
                        candidate = directory / f"{key}{ext}"
                        if candidate.is_file():
                            return candidate
        return None

    def named_art(
        self,
        *relative: str,
        size: tuple[int, int],
        fit: bool = False,
    ) -> pygame.Surface | None:
        """Load pack-relative artwork that is not a playable card (trophy, UI).

        ``fit=True`` scales the image to sit entirely inside ``size``
        (letterbox). The default still cover-crops, which is what playable
        card faces want.
        """
        width, height = max(4, size[0]), max(4, size[1])
        self._scan()
        loader = self._load_fitted if fit else self._load_cropped
        for rel in relative:
            key = Path(rel).stem.lower()
            candidates: list[Path] = []
            hit = self._slugs.get(key)
            if hit is not None:
                candidates.append(hit)
            for pack in self._packs:
                candidates.append(pack / rel)
            for path in candidates:
                if path.is_file():
                    loaded = loader(path, width, height)
                    if loaded is not None:
                        return loaded
        return None

    def has_art(self, card_def: Any) -> bool:
        return self.path_for(card_def) is not None

    # -- surfaces ---------------------------------------------------------

    def art(self, card_def: Any, size: tuple[int, int]) -> pygame.Surface:
        """Artwork for ``card_def`` at exactly ``size``, cropped to fill.

        Never returns ``None``: a card with no scan gets its procedural sigil,
        so callers have no missing-art branch to forget.
        """
        width, height = max(4, size[0]), max(4, size[1])
        def_id = str(getattr(card_def, "id", "?") or "?")
        key = (def_id, width, height)
        hit = self._surfaces.get(key)
        if hit is not None:
            return hit

        path = self.path_for(card_def)
        surf: pygame.Surface | None = None
        if path is not None:
            surf = self._load_cropped(path, width, height)
        if surf is None:
            surf = procedural_art(
                def_id,
                str(getattr(card_def, "kind", "hero") or "hero"),
                getattr(card_def, "card_class", None),
                (width, height),
            )
        self._surfaces[key] = surf
        return surf

    def _load_cropped(self, path: Path, width: int, height: int) -> pygame.Surface | None:
        """Load, scale to cover, and centre-crop. Returns ``None`` on any
        failure — a corrupt file must degrade to a placeholder, not a crash."""
        try:
            raw = pygame.image.load(str(path))
        except (pygame.error, OSError):
            return None
        if pygame.display.get_surface() is not None:
            with contextlib.suppress(pygame.error):
                raw = raw.convert_alpha()

        sw, sh = raw.get_size()
        if sw <= 0 or sh <= 0:
            return None
        scale = max(width / sw, height / sh)
        tw, th = max(1, math.ceil(sw * scale)), max(1, math.ceil(sh * scale))
        try:
            scaled = pygame.transform.smoothscale(raw, (tw, th))
        except (pygame.error, ValueError):
            scaled = pygame.transform.scale(raw, (tw, th))

        out = T.surface((width, height))
        # Card scans frame the character slightly above centre; biasing the
        # crop upward keeps faces in frame when the box is squarer than 1:1.4.
        out.blit(scaled, (-(tw - width) // 2, -int((th - height) * 0.38)))
        return out

    def _load_fitted(self, path: Path, width: int, height: int) -> pygame.Surface | None:
        """Load and scale to fit entirely inside ``(width, height)``. Never crops."""
        try:
            raw = pygame.image.load(str(path))
        except (pygame.error, OSError):
            return None
        if pygame.display.get_surface() is not None:
            with contextlib.suppress(pygame.error):
                raw = raw.convert_alpha()

        sw, sh = raw.get_size()
        if sw <= 0 or sh <= 0:
            return None
        scale = min(width / sw, height / sh)
        tw, th = max(1, int(sw * scale)), max(1, int(sh * scale))
        try:
            scaled = pygame.transform.smoothscale(raw, (tw, th))
        except (pygame.error, ValueError):
            scaled = pygame.transform.scale(raw, (tw, th))

        out = T.surface((width, height))
        out.blit(scaled, ((width - tw) // 2, (height - th) // 2))
        return out

    # -- housekeeping -----------------------------------------------------

    def preload(self, card_defs: Any, size: tuple[int, int]) -> int:
        """Warm the cache for a whole pack. Returns how many surfaces exist."""
        for card_def in card_defs:
            self.art(card_def, size)
        return len(self._surfaces)

    def clear(self) -> None:
        self._surfaces.clear()

    def stats(self) -> dict[str, int]:
        self._scan()
        return {
            "indexed_files": len(self._slugs),
            "catalog_matches": len(self._catalog),
            "resolved": sum(1 for v in self._paths.values() if v is not None),
            "placeholders": sum(1 for v in self._paths.values() if v is None),
            "cached_surfaces": len(self._surfaces),
        }


#: The client's shared library. One index, one surface cache, one place to
#: clear on resize.
_library: ArtLibrary | None = None


def library() -> ArtLibrary:
    global _library
    if _library is None:
        _library = ArtLibrary()
    return _library


def trophy_card(size: tuple[int, int]) -> pygame.Surface:
    """Legitimația lui Andrei — the win trophy, not a playable card.

    Fitted, not cropped: the badge is landscape, and cover-cropping it into a
    portrait box slices the gold frame and laurels off.
    """
    surf = library().named_art(
        "images/cards/Legitimatia_lui_Andrei_Here_to_Vibe.png",
        "images/by_type/misc/legitimatia_andrei.png",
        size=size,
        fit=True,
    )
    if surf is not None:
        return surf
    return procedural_art("legitimatia_andrei", "item", None, size)


def clear_art_cache() -> None:
    if _library is not None:
        _library.clear()


__all__ = [
    "EXTENSIONS",
    "KIND_FOLDERS",
    "REF_ROOT",
    "VIBE_ROOT",
    "ArtLibrary",
    "clear_art_cache",
    "library",
    "procedural_art",
    "trophy_card",
]

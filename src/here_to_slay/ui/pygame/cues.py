"""Which sound a moment plays — as a table, not an ``if`` chain.

The board used to choose cues with a ladder of comparisons against zone names:
``slain`` roared, ``discard`` rustled, ``party`` thumped. Every one of those
strings is *content*. ``data/variants/overclock`` adds a ``cache`` zone, and the
ladder had no branch for it, so a card entering it was silent — the same class
of bug Phase 10 found in the class tracker, where the UI was quietly hard-coded
while the engine underneath was not.

So the mapping is data, in three layers:

1. :data:`BASE_CUES` — what the base game sounds like.
2. a per-zone-kind *fallback* so an invented zone is audible rather than silent;
   a card landing somewhere new still thumps.
3. ``sounds.yaml`` in any loaded pack, which may re-point an existing key, add a
   key for its own zone or band tag, and declare entirely new voices
   (:meth:`~here_to_slay.ui.pygame.sound.SoundBoard.define`) without shipping a
   single audio file or line of Python.

Keys are ``<family>.<name>``: ``zone.party``, ``band.success``, ``ui.open``,
``game.turn``. A family's ``*`` entry is its fallback. Nothing here validates
against the pack vocabulary — a cue for a zone that does not exist is a
harmless dead entry, and refusing to start a game over one would be absurd.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOUNDS_FILE = "sounds.yaml"

#: ``key -> cue name`` (optionally ``(cue, volume)``). ``*`` is the family's
#: fallback; an empty cue name means "deliberately silent".
BASE_CUES: dict[str, tuple[str, float]] = {
    # A card arriving somewhere. `*` is what an invented zone gets.
    "zone.*": ("card_play", 0.7),
    "zone.slain": ("slay", 1.0),
    "zone.discard": ("card_discard", 1.0),
    "zone.party": ("card_play", 1.0),
    "zone.hand": ("card_deal", 0.7),
    "zone.limbo": ("card_play", 0.8),
    "zone.monster_row": ("card_deal", 1.0),
    "zone.leader": ("", 0.0),
    "zone.main_deck": ("card_discard", 0.5),
    "zone.monster_deck": ("card_discard", 0.5),
    # An outcome band, by its tag. Tags are base-pack convention, so a variant
    # with its own tag names it here.
    "band.*": ("", 0.0),
    "band.success": ("success", 0.7),
    "band.slay": ("success", 0.7),
    "band.failure": ("failure", 0.7),
    "band.backfire": ("failure", 0.7),
    # Things that happen to the game.
    "game.*": ("", 0.0),
    "game.turn": ("turn", 1.0),
    "game.victory": ("victory", 1.0),
    "game.modifier": ("modifier", 1.0),
    "game.steal": ("challenge", 0.6),
    "game.deal": ("card_deal", 0.5),
    "game.roll": ("dice_roll", 1.0),
    "game.roll_land": ("dice_land", 0.8),
    # Chrome.
    "ui.*": ("click", 1.0),
    "ui.open": ("open", 1.0),
    "ui.close": ("close", 0.5),
    "ui.click": ("click", 1.0),
    "ui.hover": ("hover", 1.0),
    "ui.error": ("error", 1.0),
    "ui.save": ("open", 0.35),
}


@dataclass(frozen=True, slots=True)
class Cue:
    """One resolved cue: what to play and how loud. Empty name = silence."""

    name: str = ""
    volume: float = 1.0

    def __bool__(self) -> bool:
        return bool(self.name) and self.volume > 0.0


def _as_cue(value: Any, fallback: Cue = Cue()) -> Cue:
    """``"slay"``, ``{cue: slay, volume: 0.6}``, ``[slay, 0.6]``, or ``null``."""
    if value is None:
        return Cue()
    if isinstance(value, str):
        return Cue(value.strip(), 1.0)
    if isinstance(value, Mapping):
        name = str(value.get("cue", value.get("sound", ""))).strip()
        try:
            volume = float(value.get("volume", 1.0))
        except (TypeError, ValueError):
            volume = 1.0
        return Cue(name, max(0.0, min(1.0, volume)))
    if isinstance(value, (list, tuple)) and value:
        name = str(value[0]).strip()
        try:
            volume = float(value[1]) if len(value) > 1 else 1.0
        except (TypeError, ValueError):
            volume = 1.0
        return Cue(name, max(0.0, min(1.0, volume)))
    return fallback


class CueTable:
    """Resolves ``family.name`` to a :class:`Cue`, with per-family fallbacks."""

    def __init__(self, table: Mapping[str, tuple[str, float]] | None = None) -> None:
        source = BASE_CUES if table is None else table
        self._cues: dict[str, Cue] = {
            key: Cue(name, volume) for key, (name, volume) in source.items()
        }
        #: cue name -> declarative spec, for whatever a pack invented
        self.voices: dict[str, dict[str, Any]] = {}

    # -- reading -----------------------------------------------------------

    def get(self, key: str) -> Cue:
        """The cue for ``key``, falling back to its family's ``*``, then silence."""
        hit = self._cues.get(key)
        if hit is not None:
            return hit
        family = key.split(".", 1)[0]
        return self._cues.get(f"{family}.*", Cue())

    def zone(self, zone_kind: str | None) -> Cue:
        return self.get(f"zone.{zone_kind}") if zone_kind else Cue()

    def band(self, tag: str | None) -> Cue:
        return self.get(f"band.{tag}") if tag else Cue()

    def game(self, name: str) -> Cue:
        return self.get(f"game.{name}")

    def ui(self, name: str) -> Cue:
        return self.get(f"ui.{name}")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._cues))

    # -- writing -----------------------------------------------------------

    def update(self, mapping: Mapping[str, Any]) -> int:
        """Apply a pack's ``cues:`` block. Returns how many keys it changed."""
        changed = 0
        for key, value in mapping.items():
            name = str(key).strip()
            if not name:
                continue
            self._cues[name] = _as_cue(value)
            changed += 1
        return changed

    def add_voices(self, mapping: Mapping[str, Any]) -> int:
        """Record a pack's ``voices:`` block for :meth:`install`."""
        added = 0
        for name, spec in mapping.items():
            if isinstance(spec, Mapping) and str(name).strip():
                self.voices[str(name).strip()] = dict(spec)
                added += 1
        return added

    def install(self, board: Any) -> int:
        """Teach a :class:`SoundBoard` every voice a pack declared."""
        installed = 0
        for name, spec in self.voices.items():
            if board.define(name, spec):
                installed += 1
        return installed

    # -- loading -----------------------------------------------------------

    def load_file(self, path: Path | str) -> bool:
        """Merge one ``sounds.yaml``. A broken file is skipped, never fatal.

        A pack whose sound file has a typo should still deal cards. The failure
        mode this avoids is the one that would make the feature not worth
        having: a variant that cannot be played because of its audio.
        """
        import yaml

        target = Path(path)
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return False
        if not isinstance(raw, Mapping):
            return False
        cues = raw.get("cues")
        if isinstance(cues, Mapping):
            self.update(cues)
        voices = raw.get("voices")
        if isinstance(voices, Mapping):
            self.add_voices(voices)
        return True

    @classmethod
    def for_registry(cls, registry: Any) -> CueTable:
        """The base table plus every loaded pack's ``sounds.yaml``, in order.

        Pack order is load order, so a variant listed after the pack it requires
        overrides it — the same precedence rule the content loader uses for
        everything else.
        """
        table = cls()
        for root in getattr(registry, "roots", ()) or ():
            candidate = Path(root) / SOUNDS_FILE
            if candidate.is_file():
                table.load_file(candidate)
        return table


__all__ = ["BASE_CUES", "SOUNDS_FILE", "Cue", "CueTable"]

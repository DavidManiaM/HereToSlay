"""The immutable result of loading one or more packs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from here_to_slay.content.schema import DECK_FOR_KIND, CardDef, PackDef, RuleSet


@dataclass(frozen=True, slots=True)
class ContentRegistry:
    """Validated, immutable content: the engine's read-only substrate.

    ``content_hash`` identifies the exact content a game was played with, so a
    replay can refuse to run against edited cards (architecture §7) — and, since
    a pack may ship a ``plugin.py``, against edited *code* too.
    """

    rules: RuleSet
    cards: Mapping[str, CardDef]
    packs: tuple[PackDef, ...] = ()
    #: card id -> "path/to/file.yaml[3]", for error messages
    sources: Mapping[str, str] = field(default_factory=dict)
    roots: tuple[Path, ...] = ()
    #: memoised :attr:`content_hash`. Not an optimisation for its own sake:
    #: ``build_view`` asks for it once per view, and answering meant
    #: re-serialising every card definition to JSON each time.
    _hash: list[str] = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cards", MappingProxyType(dict(self.cards)))
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))

    # -- lookup ------------------------------------------------------------

    def __getitem__(self, card_id: str) -> CardDef:
        return self.cards[card_id]

    def __contains__(self, card_id: str) -> bool:
        return card_id in self.cards

    def __len__(self) -> int:
        return len(self.cards)

    def get(self, card_id: str) -> CardDef | None:
        return self.cards.get(card_id)

    def of_kind(self, kind: str) -> tuple[CardDef, ...]:
        return tuple(card for card in self.cards.values() if card.kind == kind)

    def source_of(self, card_id: str) -> str:
        return self.sources.get(card_id, card_id)

    @property
    def pack_ids(self) -> tuple[str, ...]:
        return tuple(pack.id for pack in self.packs)

    @property
    def plugin_paths(self) -> tuple[Path, ...]:
        """``plugin.py`` files declared by loaded packs.

        The content layer only *finds* these; importing them (which registers
        new ops with the engine) is the caller's job — ``content/`` must not
        import ``core/``.
        """
        out: list[Path] = []
        for pack, root in zip(self.packs, self.roots, strict=False):
            if pack.plugin:
                out.append(root / pack.plugin)
        return tuple(out)

    # -- derived data ------------------------------------------------------

    def deck_composition(self) -> dict[str, int]:
        """How many physical cards land in each setup zone."""
        totals: dict[str, int] = {}
        for card in self.cards.values():
            zone = DECK_FOR_KIND.get(card.kind, "main_deck")
            totals[zone] = totals.get(zone, 0) + card.copies
        return totals

    def plugin_digests(self) -> dict[str, str]:
        """``{pack_id: sha256(plugin.py)}`` for every pack that ships one.

        A pack's Python is as much a part of "what was this game played with"
        as its YAML: two ``plugin.py`` files can leave the cards identical and
        still make ``cache_burn`` charge nothing. Without this, a replay would
        pass :func:`~here_to_slay.core.log.check_content` and then produce a
        plausible, wrong game — precisely the failure that function exists to
        prevent.
        """
        digests: dict[str, str] = {}
        for pack, root in zip(self.packs, self.roots, strict=False):
            if not pack.plugin:
                continue
            try:
                source = (root / pack.plugin).read_bytes()
            except OSError:
                # Hashing must never be the thing that raises; an unreadable
                # plugin is already fatal at load time, and a stable marker
                # keeps this deterministic if it somehow is not.
                digests[pack.id] = "<unreadable>"
            else:
                digests[pack.id] = hashlib.sha256(source).hexdigest()
        return digests

    def as_data(self) -> dict[str, Any]:
        """Canonical JSON-able projection — the input to :attr:`content_hash`."""
        data: dict[str, Any] = {
            "rules": self.rules.model_dump(mode="json"),
            "cards": {
                card_id: self.cards[card_id].model_dump(mode="json")
                for card_id in sorted(self.cards)
            },
        }
        # Added only when something is there, so a pack with no plugin hashes to
        # exactly what it hashed to before plugins existed and its saved logs
        # keep replaying.
        digests = self.plugin_digests()
        if digests:
            data["plugins"] = digests
        return data

    @property
    def content_hash(self) -> str:
        if not self._hash:
            blob = json.dumps(self.as_data(), sort_keys=True, separators=(",", ":"))
            self._hash.append(hashlib.sha256(blob.encode("utf-8")).hexdigest())
        return self._hash[0]

"""``hts diff-pack base variants/x`` — what does this variant actually change?

A variant ships as a diff, but YAML deep-merge makes that diff invisible: you
read ``rules.yaml`` and see six keys without knowing which of them differ from
the base, or which base action they quietly re-cost. This module answers that by
loading both sides and comparing the *resolved* content — the tables the engine
will really walk — rather than the files.

The comparison is over dotted paths, with one wrinkle that matters: a list of
``{id: ...}`` objects is keyed by id, not by index, because that is how the
loader merged it. Inserting an action at the top of a variant's list must not
report every later action as changed.

A plugin is *imported* but never installed: what it adds is read off the
`Plugin` object it declares, so diffing a pack neither registers its ops nor
depends on whether something else already did.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from here_to_slay.content.loader import load_packs
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.modding.plugins import loaded_plugins, ops_of

#: what a missing value prints as. ASCII, because a legacy Windows
#: console cannot encode an em dash and this table is meant to be readable
#: everywhere `hts validate` is.
ABSENT = "-"


@dataclass(frozen=True, slots=True)
class Change:
    """One differing dotted path."""

    path: str
    before: Any
    after: Any

    @property
    def kind(self) -> str:
        if self.before is None and self.after is not None:
            return "added"
        if self.after is None and self.before is not None:
            return "removed"
        return "changed"


@dataclass(frozen=True, slots=True)
class CardChange:
    """One card that a variant added, removed, or edited."""

    card_id: str
    kind: str  # added | removed | changed
    name: str = ""
    changes: tuple[Change, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PackDiff:
    """Everything the right-hand pack changes about the left-hand one."""

    base_packs: tuple[str, ...]
    variant_packs: tuple[str, ...]
    rules: tuple[Change, ...] = field(default_factory=tuple)
    cards: tuple[CardChange, ...] = field(default_factory=tuple)
    ops: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def added_packs(self) -> tuple[str, ...]:
        return tuple(p for p in self.variant_packs if p not in self.base_packs)

    @property
    def cards_added(self) -> tuple[CardChange, ...]:
        return tuple(c for c in self.cards if c.kind == "added")

    @property
    def cards_removed(self) -> tuple[CardChange, ...]:
        return tuple(c for c in self.cards if c.kind == "removed")

    @property
    def cards_changed(self) -> tuple[CardChange, ...]:
        return tuple(c for c in self.cards if c.kind == "changed")

    @property
    def is_empty(self) -> bool:
        return not (self.rules or self.cards or self.ops)


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def _is_id_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) and "id" in item for item in value)
    )


def flatten(value: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every leaf of a JSON-able tree, as ``("a.b[0].c", leaf)``.

    An ``{id: ...}`` list is addressed by id (``actions[draw].cost``) so that a
    variant which merely *adds* an entry does not appear to have rewritten the
    whole table.
    """
    if isinstance(value, dict):
        if not value:
            yield prefix, {}
            return
        for key in sorted(value):
            yield from flatten(value[key], f"{prefix}.{key}" if prefix else str(key))
    elif _is_id_list(value):
        for item in value:
            yield from flatten(item, f"{prefix}[{item['id']}]")
    elif isinstance(value, list):
        if not value:
            yield prefix, []
            return
        for index, item in enumerate(value):
            yield from flatten(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def diff_trees(before: Any, after: Any) -> tuple[Change, ...]:
    """Compare two JSON-able trees path by path, in path order."""
    left = dict(flatten(before))
    right = dict(flatten(after))
    return tuple(
        Change(path, left.get(path), right.get(path))
        for path in sorted(left.keys() | right.keys())
        if left.get(path) != right.get(path)
    )


# ---------------------------------------------------------------------------
# Loading both sides
# ---------------------------------------------------------------------------


def _load(
    paths: Sequence[str | Path], search_paths: Sequence[str | Path]
) -> tuple[ContentRegistry, dict[str, tuple[str, ...]]]:
    """Load a pack and note which ops its plugins declare, registering nothing.

    The ops come from the :class:`Plugin` objects themselves rather than from
    diffing ``registered_ops()`` around the import. Diffing the global tables
    reports *nothing* when the pack happens to be installed already — which is
    the normal state after an ``hts validate`` in the same process — and would
    make ``diff-pack`` quietly claim a plugin adds no ops.
    """
    registry = load_packs([str(p) for p in paths], search_paths=[str(p) for p in search_paths])
    plugins = [plugin for loaded in loaded_plugins(registry) for plugin in loaded.plugins]
    return registry, ops_of(plugins)


def diff_packs(
    base: Sequence[str | Path],
    variant: Sequence[str | Path],
    *,
    search_paths: Sequence[str | Path] = (),
) -> PackDiff:
    """Load both sides and report what the second changes about the first."""
    base_registry, base_ops = _load(base, search_paths)
    variant_registry, variant_ops = _load(variant, search_paths)

    rules = diff_trees(
        base_registry.rules.model_dump(mode="json"),
        variant_registry.rules.model_dump(mode="json"),
    )

    cards: list[CardChange] = []
    for card_id in sorted(set(base_registry.cards) | set(variant_registry.cards)):
        left = base_registry.get(card_id)
        right = variant_registry.get(card_id)
        if left is None and right is not None:
            cards.append(CardChange(card_id, "added", right.name))
        elif right is None and left is not None:
            cards.append(CardChange(card_id, "removed", left.name))
        elif left is not None and right is not None:
            changes = diff_trees(left.model_dump(mode="json"), right.model_dump(mode="json"))
            if changes:
                cards.append(CardChange(card_id, "changed", right.name, changes))

    ops = {
        table: tuple(sorted(set(names) - set(base_ops.get(table, ()))))
        for table, names in variant_ops.items()
        if set(names) - set(base_ops.get(table, ()))
    }

    return PackDiff(
        base_packs=base_registry.pack_ids,
        variant_packs=variant_registry.pack_ids,
        rules=rules,
        cards=tuple(cards),
        ops=ops,
    )


def render_value(value: Any, width: int = 60) -> str:
    """A one-line rendering of a leaf, for a table cell."""
    if value is None:
        return ABSENT
    text = str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


__all__ = [
    "ABSENT",
    "CardChange",
    "Change",
    "PackDiff",
    "diff_packs",
    "diff_trees",
    "flatten",
    "render_value",
]

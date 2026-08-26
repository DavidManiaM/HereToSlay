"""``hts new-pack`` — the first five minutes of a variant, written for you.

The templates are deliberately *runnable*, not empty: a fresh pack validates and
plays immediately, so the modder's first edit is a change to something that
already works rather than a guess at a schema. Every file is commented with the
one thing a beginner gets wrong about it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from here_to_slay.content.errors import ContentError, ContentIssue
from here_to_slay.content.schema import SLUG_PATTERN


@dataclass(frozen=True, slots=True)
class Scaffolded:
    """What :func:`new_pack` wrote."""

    root: Path
    files: tuple[Path, ...]


def _pack_yaml(pack_id: str, requires: Sequence[str], with_plugin: bool) -> str:
    required = "[" + ", ".join(requires) + "]" if requires else "[]"
    return f"""# The pack manifest: identity, dependencies, and what this directory provides.
#
# `requires:` makes this pack a *diff* rather than a copy — the base game loads
# first and this pack's rules.yaml deep-merges over it, so you ship only the
# keys you actually change.
id: {pack_id}
name: "{pack_id.replace("_", " ").title()}"
version: "0.1.0"
schema_version: 1
requires: {required}
load_after: []
plugin: {"plugin.py" if with_plugin else "null"}

provides:
  rules: rules.yaml
  cards: [cards/*.yaml]

# Change another pack's card without copying it:
# patches:
#   - target: base.monster.dragon
#     set: {{ text: "Now angrier." }}
"""


def _rules_yaml(pack_id: str) -> str:
    return f"""# Everything here is merged OVER the packs in `requires:`.
#
# Three merge rules are worth knowing before you edit (docs/card_schemas.md 1.1):
#   * mappings deep-merge, so you name only the keys you change
#   * lists of {{id: ...}} objects merge BY ID — re-cost one action without
#     restating the table, or drop one with {{id: x, remove: true}}
#   * every other list REPLACES, so `classes:` and a phase's `allows:` must be
#     written out in full
id: {pack_id}

# Turn length is the shortest interesting first edit. Uncomment to try it:
# turn:
#   action_points_per_turn: 4

# A new zone is one line. `scope: player` gives every seat its own.
# zones:
#   - {{ id: cache, scope: player, visibility: public, ordered: true }}

# A new action needs an entry here *and* a mention in the phase that allows it.
# actions:
#   - id: meditate
#     label: "Meditate"
#     cost: {{ action_points: 1 }}
#     effect: {{ op: draw, target: $self, count: 1 }}
#
# phases:
#   - id: main
#     allows: [draw, play_hero, use_hero_ability, use_leader_ability,
#              attack_monster, equip_item, cast_magic, discard_and_draw, meditate]

# A win condition is a predicate over the board, checked after every event.
# victory:
#   - id: hoarder
#     text: "Hold ten cards at once"
#     condition: {{ op: hand_size, player: $player, cmp: ">=", value: 10 }}
"""


def _cards_yaml(pack_id: str) -> str:
    return f"""# Card ids are '<pack>.<kind>.<slug>' — the validator enforces it, and it is
# what lets `patches:` point at another pack's card unambiguously.
#
# Delete this example once you have a card of your own.

- id: {pack_id}.magic.hello_world
  kind: magic
  name: "Hello, World"
  copies: 2
  text: "Trage doua carti."
  play:
    effect: {{ op: draw, target: $self, count: 2 }}
"""


def _plugin_py(pack_id: str) -> str:
    return f'''"""New verbs for the {pack_id} pack.

Everything declared on this object is wired into BOTH the engine registries and
the validator's vocabulary, so `hts validate` and the running game agree about
what exists. See docs/modding_guide.md 4.
"""

from __future__ import annotations

from typing import Any

from here_to_slay.core.events import Outcome
from here_to_slay.modding import Plugin, Role

plugin = Plugin("{pack_id}")


@plugin.condition(
    "hand_is_empty",
    params={{"player": Role.REF}},
    doc="true when that player holds nothing",
)
def hand_is_empty(ctx: Any, params: dict[str, Any]) -> bool:
    player = ctx.resolve_player(params.get("player"))
    return len(ctx.state.zone_of("hand", player)) == 0


@plugin.effect(
    "shout",
    params={{"text": Role.VALUE}},
    doc="emit a custom event other cards can trigger on",
)
def shout(ctx: Any, params: dict[str, Any]) -> Any:
    """A generator op: `yield from ctx.emit(...)` is how anything reaches the bus."""
    result = yield from ctx.emit("{pack_id}.shouted", {{"text": params.get("text")}})
    return Outcome.DONE if result.ok else Outcome.CANCELLED


@plugin.mutator("{pack_id}.shouted")
def shouted(state: Any, event: Any) -> None:
    """The one place `{pack_id}.shouted` changes the state. Declaring a mutator
    is also what tells the validator the event name exists."""
'''


def _readme(pack_id: str) -> str:
    return f"""# {pack_id}

A content pack for *Here to Slay*.

```bash
uv run hts validate {pack_id}
uv run hts diff-pack data/base {pack_id}
uv run hts play {pack_id}
uv run hts gui {pack_id} --players 4 --ai 3
```

Start in `rules.yaml`. See `docs/modding_guide.md` for the tour.
"""


def new_pack(
    pack_id: str,
    *,
    directory: str | Path = "data/variants",
    requires: Sequence[str] = ("base",),
    with_plugin: bool = False,
    force: bool = False,
) -> Scaffolded:
    """Write a runnable skeleton pack. Returns the files created, in order."""
    if not SLUG_PATTERN.match(pack_id):
        raise ContentError(
            ContentIssue(
                pack_id,
                "a pack id must be a lower_snake_case slug",
                hint="try something like 'grimdark' or 'my_variant'",
            ),
            "cannot scaffold",
        )

    root = Path(directory) / pack_id
    if root.exists() and any(root.iterdir()) and not force:
        raise ContentError(
            ContentIssue(
                root.as_posix(),
                "directory already exists and is not empty",
                hint="pass --force to write into it anyway",
            ),
            "cannot scaffold",
        )

    files: dict[Path, str] = {
        root / "pack.yaml": _pack_yaml(pack_id, requires, with_plugin),
        root / "rules.yaml": _rules_yaml(pack_id),
        root / "cards" / "cards.yaml": _cards_yaml(pack_id),
        root / "README.md": _readme(pack_id),
    }
    if with_plugin:
        files[root / "plugin.py"] = _plugin_py(pack_id)

    written: list[Path] = []
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return Scaffolded(root=root, files=tuple(written))


__all__ = ["Scaffolded", "new_pack"]

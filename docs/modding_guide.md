# Modding Guide

This is the document the whole project exists for. Everything below is done from a directory of
your own — no file under `src/here_to_slay/core/` is ever edited, and if you find something that
would need one, that is a bug in the engine and it belongs in [`build_plan.md`](build_plan.md),
not in your fork.

The tour, in the order it gets harder:

| § | You want to… | You edit |
|---|---|---|
| 2 | change a number | `rules.yaml` |
| 3 | add a card | `cards/*.yaml` |
| 4 | add a **verb**, a **predicate**, a **selector**, a **currency** | `plugin.py` |
| 5 | add a zone, an action, a reaction window, a win condition | `rules.yaml` |
| 6 | edit somebody else's card | `pack.yaml` → `patches:` |
| 7 | know what you changed | `hts diff-pack` |
| 8 | prove it works | `hts validate --strict`, `hts sim --strict` |
| 10 | look up a command | the table in §10 |

The worked example is [`data/variants/overclock`](../data/variants/overclock), which does all of
it. `tests/test_variant_overclock.py` is its acceptance test, including the one that greps
`core/` to prove nothing there ever learned the pack's name.

---

## 1. Thirty seconds

```bash
uv run hts new-pack my_variant --plugin
uv run hts validate data/variants/my_variant
uv run hts play data/variants/my_variant
```

`new-pack` writes a skeleton that already validates and already deals, so your first edit is a
change to something that works rather than a guess at a schema. Drop `--plugin` if you do not
need Python yet — most variants never do.

### The four files

```
data/variants/my_variant/
├── pack.yaml       identity, dependencies, patches
├── rules.yaml      merged OVER the packs you require
├── cards/*.yaml    your cards
└── plugin.py       new ops (optional)
```

`hts validate` is the fastest loop you have. It is the same load path the game uses, so a pack
that validates cannot fail at load time; run it after every edit.

---

## 2. Changing a number

`rules.yaml` is merged over the packs in `requires:`, so you ship only what you change:

```yaml
id: my_variant

turn:
  action_points_per_turn: 4
setup:
  starting_hand: 7
```

Everything else — zones, actions, phases, windows, victory — is inherited untouched.

### The three merge rules, and the one that catches everyone

| What | How it merges |
|---|---|
| a mapping (`turn:`, `setup:`, `windows:`) | **deep-merges** — name only the keys you change |
| a list of `{id: ...}` objects (`actions:`, `zones:`, `victory:`) | merges **by id**; `{id: x, remove: true}` deletes an entry |
| any other list (`classes:`, a phase's `allows:`) | **replaces wholesale** |

The last row is the one that bites. Adding a seventh class means writing all seven:

```yaml
classes: [bard, fighter, guardian, ranger, thief, wizard, hacker]
```

and adding an action means restating the phase's whole `allows:` list, or the action exists and
is never offered.

---

## 3. Adding a card

A card is data. Its id is `<pack>.<kind>.<slug>`, which is what lets another pack patch it later.

```yaml
- id: my_variant.magic.short_circuit
  kind: magic
  name: "Short Circuit"
  copies: 2
  text: "Trage doua carti."
  play:
    effect: { op: draw, target: $self, count: 2 }
```

The full schema — every kind, every block, every op and its params — is
[`card_schemas.md`](card_schemas.md). Two habits worth forming early:

* **Read a base card before writing yours.** `data/base/cards/heroes.yaml` is 48 worked examples
  of the same shape, commented with the idioms (`bind:`/`then:`, flags for lasting effects).
* **Let `hts validate` teach you the vocabulary.** An unknown op is reported with the nearest
  known name, and an unbindable `$ref` is reported with the scope it was looked up in.

---

## 4. Adding a verb: `plugin.py`

When no combination of existing ops says what you mean, write one. A plugin declares each new op
**once**, and that one declaration reaches both places it has to:

* the **engine registries**, so the interpreter can run it;
* the **validator's vocabulary**, so `hts validate` knows it exists and what params it takes.

```python
# data/variants/my_variant/plugin.py
from here_to_slay.core.events import Outcome
from here_to_slay.modding import Plugin, Role

plugin = Plugin("my_variant")


@plugin.effect("upload_card", params={"card": (Role.REF, True), "player": Role.REF})
def upload_card(ctx, params):
    """A generator op: `yield from ctx.emit(...)` is how anything reaches the bus."""
    card = ctx.resolve_card(params.get("card"))
    player = ctx.resolve_player(params.get("player"))
    result = yield from ctx.emit("cache.uploaded", {"card": card, "player": player}, actor=player)
    return Outcome.DONE if result.ok else Outcome.CANCELLED
```

Then in `pack.yaml`:

```yaml
plugin: plugin.py
```

### The five decorators

| Decorator | Adds | Signature | Rules |
|---|---|---|---|
| `@plugin.effect(name)` | a **verb** | `(ctx, params) -> Generator \| Outcome` | may ask questions by yielding |
| `@plugin.condition(name)` | a **predicate** | `(ctx, params) -> bool` | pure: no yielding, no mutation |
| `@plugin.selector(name)` | a **target set** | `(ctx, params) -> Sequence[Id]` | yields **ids**, never objects |
| `@plugin.mutator(event)` | how an event **applies** | `(state, event) -> None` | exactly one per event name |
| `@plugin.cost(name)` | a **currency** | `(ctx, params) -> bool` | must answer `check_only` from state alone |

Three constraints are worth understanding rather than memorising:

**A condition may not ask.** Conditions run inside `legal_intents()`, which the UI calls every
frame to decide what to draw. A condition that stopped to ask a question would deadlock it. Same
reason a cost must answer `params["check_only"]` without mutating: merely *looking* at the menu
prices every action.

**A selector yields ids.** `exclude:` compares them, a `where:` filter binds one as `$candidate`,
and the decision log prints them. Handing back live objects breaks all three.

**Emit; do not move.** If you move a card directly, no reaction window can open around it and no
trigger can fire on it. Emit an event and let a `@plugin.mutator` perform the move — that is
what buys your new action a Challenge-shaped card for free (§5.3).

### Declaring params

`params={...}` is what the validator walks. The role tells it which values are nested effects,
conditions or selectors:

```python
@plugin.effect(
    "twice_if",
    params={"condition": (Role.CONDITION, True), "effect": (Role.EFFECT, True)},
    body=("effect",),          # params that hold nested effects
    binds=None,                # the param that introduces a $name, if any
)
```

Get this wrong and the op still runs — but `hts validate` will not look inside the nested effect,
so a typo in it surfaces mid-game instead of at load time.

### Your `plugin.py` is part of the content hash

`hts play` saves a decision log, and `hts replay` refuses to run it against content that has
changed since — a replay that quietly runs against different rules produces a plausible, wrong
game, which is worse than no replay. Your Python counts as content: the hash covers a sha256 of
each pack's `plugin.py`, because two versions of one file can leave every card byte-identical and
still change what an op does.

So: **editing `plugin.py` invalidates the logs you recorded before the edit.** That is the
intended behaviour, not a bug to work around. If you need an old log to keep replaying, keep the
old plugin next to it. A pack with no plugin is unaffected — the digest is only added when there
is one, so `data/base` hashes to exactly what it always did.

### Registration is deferred, and why that matters

The decorators only *record*; `Plugin.install()` applies. This is not ceremony: Python caches
modules in `sys.modules`, so import-time decorators run exactly once per process. Anything that
loads two packs in a row — `hts diff-pack`, the test suite — would otherwise find the second
pack's ops missing with no way to get them back. Installing the same plugin twice is a no-op;
*another* pack claiming a name you already registered is still an error.

---

## 5. The four structural seams

### 5.1 A new zone

```yaml
zones:
  - { id: cache, scope: player, visibility: public, ordered: true }
```

`scope: player` mints one per seat at setup. Nothing in Python names a zone: `core/setup.py`
walks this table, `move_card` resolves `{player: $self, zone: cache}` against it, and the
invariant checker counts cards in it like any other. A shared, ordered, hidden zone is shuffled
at setup automatically, so your own face-down deck needs no code either.

### 5.2 A new action

```yaml
actions:
  - id: upload
    label: "Upload in cache"
    cost: { action_points: 1 }
    targets:
      - param: card
        from: { selector: cards, of: { player: $self, zone: hand } }
        prompt: "Ce urci in cache?"
    effect: { op: upload_card, card: $intent.card, player: $self }

phases:
  - id: main
    allows: [draw, play_hero, use_hero_ability, use_leader_ability, attack_monster,
             equip_item, cast_magic, discard_and_draw, upload]
```

`targets:` is what keeps the menu data: the engine expands the action into one intent per legal
combination, so "which cards may I upload?" is a selector plus a filter, never a Python branch.
An action whose target has no candidates is not offered at all — which is why an empty cache
means no `download` on the menu, with nobody writing that rule down.

Remember the `allows:` list replaces. A new action that never appears is almost always this.

### 5.3 A new reaction window

```yaml
windows:
  cache_upload:
    on: cache.uploaded
    timing: pre
    order: seat_left_of_actor
    reopen_on_action: false
```

The bus opens a declared window *inside* the dispatch of the named event, which is what lets a
card played into it cancel the very thing it is reacting to. `on:` may name an event the engine
has never heard of — registering a `@plugin.mutator` for it is what makes the name legal, and
`order:` is one of `seat_left_of_active`, `seat_left_of_actor`, `active_first`, `turn_order`.

A card reacts by naming the window:

```yaml
- id: my_variant.challenge.firewall
  kind: challenge
  name: "Firewall"
  reaction:
    window: cache_upload
    challengeable: false
    condition: { op: not_self }
    effect: { op: cancel_event, and_discard: true, reason: "Firewall" }
```

`and_discard: true` sets a flag your emitting op reads back off the result — see `upload_card`
in the sample variant, which sends the blocked card to the discard rather than the hand, matching
what the base game does to a challenged play.

### 5.4 A new win condition

Victory is checked for every seat after every event resolution, so a condition is just a
predicate over the board:

```yaml
victory:
  - { id: full_party, remove: true }
  - id: full_cache
    text: "Tine patru carti in cache"
    condition: { op: cache_size, player: $player, cmp: ">=", value: 4 }
```

`$player` is the seat being tested. `remove: true` deletes an inherited condition — the honest
way to drop a route, rather than overriding it into something unreachable.

---

## 6. Editing somebody else's card

Redefining another pack's card id is an error, not a silent override. To change one, patch it:

```yaml
# pack.yaml
patches:
  - target: base.monster.dracos
    set:
      roll.outcomes.0.min: 8      # dotted paths reach inside anything
      roll.outcomes.1.max: 7
  - target: base.hero.dodgy_dealer
    remove: true
  - target: rules                 # the rule set is patchable too
    set: { turn.hand_limit: 7 }
```

A patch applies after every required pack has loaded, so ordering is never your problem.

---

## 7. Knowing what you changed

```bash
uv run hts diff-pack data/base data/variants/my_variant
```

Deep-merge makes a variant's diff invisible: you read your `rules.yaml` and cannot tell which
base action you quietly re-costed. `diff-pack` loads both sides and compares the *resolved*
tables — the ones the engine really walks — and lists every op your plugin adds. Add `--cards`
to expand each edited card field by field.

It imports your plugin inside `core.registry.temporarily()`, so running it never leaves your ops
registered in that process.

---

## 8. Testing your pack

```bash
uv run hts validate data/variants/my_variant --strict   # errors AND warnings
uv run hts sim data/variants/my_variant --games 200 --strict
```

`sim` plays headless games with the random agent and reports errors, invariant violations and
which win condition actually fired. `--strict` turns on the invariant checker after *every*
mutation, so a card that strands a copy in `limbo` fails on the game it happened rather than
three turns later. It is the cheapest bug-finder in the project — a variant that survives a few
hundred games is usually correct, and the win-condition breakdown doubles as a balance check.

---

## 9. Where to look when it goes wrong

| Symptom | Almost always |
|---|---|
| `unknown effect op 'x'` | a typo, or `plugin: plugin.py` missing from `pack.yaml` |
| the action never appears | not in the phase's `allows:`, or its target has no candidates |
| `$ref 'x' is not bound here` | a `bind:` whose scope is `body`, referenced from a sibling |
| the reaction is never offered | the window's `condition:`, or the card's own `condition:` |
| your window never opens | `on:` names an event nothing emits — check for a mutator |
| a card vanishes mid-play | an effect moved it without emitting; run with `HTS_STRICT=1` |
| games never end | a victory condition nobody can reach — `hts sim` reports the timeouts |

Every content error is path-qualified down to the YAML node
(`cards/heroes.yaml[3].ability.roll.outcomes[1].effect.op`). If you ever get a bare `KeyError`
out of a pack, that is a bug in the loader worth reporting.

---

## 10. The commands, in one place

| Command | What it does |
|---|---|
| `hts new-pack <name> [--plugin] [--dir D] [--requires ...] [--force]` | Write a runnable skeleton pack |
| `hts validate <pack> [--strict] [--no-art-check] [-q]` | Load, structurally validate, semantically validate. Imports the pack's plugin first, so its ops are known. CI-able: non-zero on error |
| `hts diff-pack <base> <variant> [--cards]` | What the variant changes: rules, cards, and the ops its plugin adds |
| `hts play <pack> [--players N] [--seed S] [--max-turns N] [--no-save]` | Terminal hot-seat |
| `hts gui <pack> [--players N] [--ai N] [--seed S] [--reveal-all] …` | The PyGame client |
| `hts sim <pack> [--games N] [--players N] [--agent random\|heuristic] [--strict]` | Headless fuzzing: errors, invariant violations, which win condition fired |
| `hts replay <log.json> <pack> [--step]` | Re-run a saved decision log |

Every one of them loads content the same way (`cli.load_content`), which is why a variant with a
`plugin.py` works everywhere the base game does.

Two flags worth knowing early:

* **`--strict` on `validate`** promotes warnings to errors. Run it before you ship a pack.
* **`--strict` on `sim`** turns on the invariant checker after *every* mutation, so a card that
  strands a copy in `limbo` fails on the game it happened rather than three turns later. A few
  hundred games is the cheapest bug-finder in the project.

### A pack next door is found for you

`data/variants/overclock` says `requires: [base]`, and `base` lives in `data/`. The loader scans
the requested pack's own directory, its neighbours, *and one level up*, so this works with no
flags:

```bash
uv run hts validate data/variants/overclock
```

`--search-path DIR` (repeatable) is still there for a pack that lives somewhere unusual.

---

## 11. Further reading

* [`card_schemas.md`](card_schemas.md) — every schema, and the full op catalogue
* [`rules_engine.md`](rules_engine.md) — how the event bus, windows and victory checks fit together
* [`architecture_notes.md`](architecture_notes.md) — §5 is the registries, §3 the bus
* [`data/variants/overclock`](../data/variants/overclock) — a variant that uses every seam on this page

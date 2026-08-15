# Card & Content Schemas

All content is YAML, validated by **pydantic v2** at load time. A malformed pack fails loudly
with a path-qualified error (`data/base/cards/heroes.yaml[3].ability.on_success.steps[1].op`)
rather than crashing mid-game.

Run `uv run hts validate data/base` to check a pack without launching a game.

---

## 1. Content Pack

```yaml
# data/base/pack.yaml
id: base
name: "Here to Slay — Base Game"
version: "1.0.0"
schema_version: 1
requires: []            # other pack ids
load_after: []          # ordering hint
plugin: null            # optional "plugin.py" registering new effect ops
provides:
  rules: rules.yaml
  cards: [cards/*.yaml]
```

Packs stack. A later pack may **add** cards, or **patch** an earlier one:

```yaml
# data/variants/grimdark/pack.yaml
id: grimdark
requires: [base]
patches:
  - target: base.monster.dragon
    set: {roll.thresholds.slay: 11}
  - target: base.hero.dodgy_dealer
    remove: true
```

This is the mechanism that lets your variant ship as a *diff* against the base game instead of a
copy of it.

### 1.1 How packs combine

Packs load in dependency order (`requires` + `load_after`, ties broken by pack id). Three
mechanisms stack, in this order:

| Mechanism | What it does |
|---|---|
| a later `rules.yaml` | **deep-merges** over earlier ones — you ship only the keys you change |
| lists of `{id: ...}` | merge **by id**, so re-costing one action doesn't restate the table; `{id: x, remove: true}` drops an entry |
| `patches:` | dotted-path `set:` / `remove:` against a card id, or against `target: rules` |

Redefining another pack's card id without a `patch` is an **error**, not a silent override.

### 1.2 One YAML dialect note

PyYAML implements YAML 1.1, in which `on`, `off`, `yes` and `no` are booleans — which would turn
a trigger's `on: monster.slain` key into `True: monster.slain`. The loader narrows the boolean
resolver to `true`/`false` only (YAML 1.2 behaviour), so **`on:` works unquoted** and `yes`/`no`
are plain strings. `true`/`false` are unaffected.

---

## 2. Common Card Fields

Every card shares this envelope (pydantic discriminated union on `kind`):

```yaml
id: base.hero.dodgy_dealer     # globally unique, "<pack>.<kind>.<slug>"
kind: hero                     # hero | item | magic | modifier | challenge | monster | party_leader
name: "Dodgy Dealer"
copies: 2                      # how many enter the deck at setup
text: "Trade hands with another player."   # flavour/rules text shown in UI
art: "heroes/dodgy_dealer.png"             # relative to assets/
tags: [starter, trade]         # free-form; conditions can query them
priority: 0                    # trigger ordering tiebreak
```

`tags` is intentionally unconstrained — it is the cheapest way for a variant to say "all cards
tagged `cursed` cost +1" without an engine change.

---

## 3. The Effect Tree

An **effect** is a dict with an `op` key. Ops nest. This is the whole language.

```yaml
effect:
  op: seq
  steps:
    - op: choose
      bind: victim                      # binds $victim for later steps
      chooser: $self
      from: {selector: players, exclude: [$self]}
      prompt: "Steal from whom?"
    - op: if
      condition: {op: hand_size, player: $victim, cmp: ">", value: 0}
      then: {op: steal_card, from: $victim, to: $self, count: 1, random: true}
      else: {op: draw, target: $self, count: 1}
```

### 3.1 Effect op catalogue (v1)

**Control flow**
| op | params | notes |
|---|---|---|
| `seq` | `steps[]` | run in order; abort if one is cancelled |
| `if` | `condition`, `then`, `else?` | |
| `choose_effect` | `chooser`, `options[]{label,effect,condition?}` | player picks a branch |
| `repeat` | `times`, `effect` | `times` may be an expression |
| `for_each` | `over` (selector), `bind`, `effect` | |
| `noop` | — | explicit "nothing happens" |
| `optional` | `effect`, `prompt` | asks yes/no first |

**Cards & zones**
| op | params |
|---|---|
| `draw` | `target`, `count`, `from?` (zone ref; defaults to `main_deck`) |
| `discard` | `target`, `count`, `random?`, `chooser?`, `filter?`, `zone?` |
| `move_card` | `card`, `to` (zone ref), `position?` (`top`/`bottom`/`random`) |
| `steal_card` | `from`, `to`, `count`, `random?`, `chooser?` |
| `search` | `zone`, `filter`, `count`, `bind` (names what was found), `then` (effect) |
| `reveal` | `card`/`zone`, `count`, `to` (audience) |
| `shuffle` | `zone` |

**Party & board**
| op | params |
|---|---|
| `steal_hero` | `from`, `chooser`, `filter?` |
| `destroy_hero` | `target`, `chooser?`, `filter?` |
| `sacrifice` | `target`, `filter` |
| `equip_item` | `item`, `hero` |
| `unequip_item` | `item`, `to_zone` |
| `return_monster` | `monster`, `to` |
| `refill_monster_row` | — |

**Rolls** (see `rules_engine.md §4`)
| op | params |
|---|---|
| `roll` | `dice`, `kind`, `roller`, `outcomes[]` |
| `modify_roll` | `amount`, `source`, `target_roll?` |
| `reroll` | `dice?`, `roller` |
| `contest_roll` | `a`, `b`, `on_a_wins`, `on_b_wins`, `on_tie?` |
| `set_roll_result` | `value` |

**Resources & meta**
| op | params |
|---|---|
| `gain_action_points` / `spend_action_points` | `target`, `amount` |
| `extra_turn` | `target` |
| `end_turn` | — |
| `cancel_event` | `and_discard?` |
| `set_flag` / `clear_flag` | `scope` (`game`/`player`/`card`), `key`, `value` |
| `emit` | `event`, `payload` — fire a custom event for other cards to hook |
| `win_game` | `target` |

> Anything not in this table is a **plugin op**: one decorated generator function in a pack's
> `plugin.py`. The table is a starting vocabulary, not a ceiling.

This table is machine-readable: `content/vocabulary.py` declares each op with the *role* of every
parameter (`effect`, `condition`, `filter`, `selector`, `zone`, `value`, `name`, …). That is how
the validator knows `if.then` holds an effect while `if.condition` holds a predicate, and how it
tracks which `$bindings` are live where — without the schema having to close the union over op
names. A plugin adds its ops with `Vocabulary.extend(...)`.

---

## 4. Conditions (Strategy pattern)

A condition is a dict with `op`, evaluated to `bool`. Registered via `@condition(...)`.

```yaml
condition:
  op: all
  of:
    - {op: party_has_class, player: $self, class: fighter, min: 2}
    - {op: not, of: {op: flag_set, scope: game, key: night}}
    - {op: compare, left: {expr: "$self.hand_size"}, cmp: ">=", right: 3}
```

| op | params |
|---|---|
| `all` / `any` | `of[]` |
| `not` | `of` |
| `always` / `never` | — |
| `party_has_class` | `player`, `class`, `min` |
| `party_size` / `hand_size` / `discard_size` | `player`, `cmp`, `value` |
| `has_card` | `player`, `zone`, `filter` |
| `slain_count` | `player`, `cmp`, `value` |
| `card_has_tag` | `card`, `tag` |
| `event_actor_is` | `player` |
| `event_matches` | `kind_in?`, `class_in?`, `tag_in?`, `played_by?` |
| `roll_is` | `kind`, `roller?` |
| `flag_set` | `scope`, `key`, `value?` |
| `compare` | `left`, `cmp` (`==,!=,<,<=,>,>=`), `right` |

### 4.1 Filters

A `filter` is a condition applied to each candidate card (`$candidate` is bound):

```yaml
filter: {op: all, of: [{op: card_kind_is, kind: hero}, {op: card_class_is, class: bard}]}
```

---

## 5. Selectors & References

**Reference strings** (`$`-prefixed) resolve against the effect context:

| Ref | Resolves to |
|---|---|
| `$self` | the player who controls the source card |
| `$card` | the source card instance |
| `$event.player`, `$event.card`, `$event.target` | fields of the triggering event |
| `$<bind>` | anything bound by `choose`/`for_each` |
| `$rules.<key>` | a constant from `rules.yaml` |
| `$action_points` | current AP of the active player |

**Selectors** produce a list:

```yaml
from: {selector: heroes, of: $opponents, where: {op: card_class_is, class: fighter}, limit: 1}
```

| selector | of | yields |
|---|---|---|
| `players` | — | all players (`exclude:` supported) |
| `opponents` | player | everyone else |
| `cards` | zone ref | cards in a zone |
| `heroes` / `items` / `monsters` | player / row | typed shortcuts |
| `party_leaders` | players | |
| `monster_row` | — | the face-up monsters |

**Zone refs**: `{player: $self, zone: hand}` or a shared name: `{zone: discard}`.

---

## 6. Card Kinds

### 6.1 Hero

```yaml
- id: base.hero.dodgy_dealer
  kind: hero
  name: "Dodgy Dealer"
  card_class: bard          # bard|fighter|guardian|ranger|thief|wizard
  copies: 2
  text: "Trade hands with another player."
  ability:
    activation: action      # action | passive | triggered
    cost: {action_points: 1}
    once_per_turn: true
    roll:
      dice: "2d6"
      kind: hero_ability    # tags the roll so leaders/items can modify it
      outcomes:
        - {min: 7, effect: {op: seq, steps: [...]}}
        - {max: 6, effect: {op: noop}}
```

A Hero with a passive instead uses `triggers:` (see §6.6). A Hero may have **both**.

### 6.2 Monster

```yaml
- id: base.monster.mega_slime
  kind: monster
  name: "Mega Slime"
  copies: 1
  requirement:                     # gate: may you even attack it?
    op: party_has_class
    player: $self
    class: fighter
    min: 2
  requirement_text: "Requires 2 Fighters"
  roll:
    dice: "2d6"
    kind: monster_attack
    outcomes:
      - {min: 8, effect: {op: slay_monster, monster: $card, by: $self}}
      - {min: 4, max: 7, effect: {op: noop}}
      - {max: 3, effect: {op: discard, target: $self, count: 2, chooser: $self}}
  on_slay: {op: gain_action_points, target: $self, amount: 1}   # the reward
```

Bands are declarative, so a variant can add a "natural 12 = double reward" band trivially.

### 6.3 Item

```yaml
- id: base.item.decoy_doll
  kind: item
  name: "Decoy Doll"
  copies: 2
  equip:
    to: {selector: heroes, of: $any_player}   # some items can be given to opponents
    cost: {action_points: 1}
  triggers:
    - on: hero.left_party
      timing: pre
      while_in: party
      condition: {op: event_matches, card: "$card.attached_to"}
      effect: {op: cancel_event}
```

### 6.4 Magic

```yaml
- id: base.magic.forced_exchange
  kind: magic
  name: "Forced Exchange"
  copies: 2
  play:
    cost: {action_points: 1}
    challengeable: true
    effect: {op: ...}
    then: {op: move_card, card: $card, to: {zone: discard}}
```

### 6.5 Modifier & Challenge (reaction cards)

These are never *actions*; they are played into an open **window** for free.

```yaml
- id: base.modifier.plus_two
  kind: modifier
  name: "+2"
  copies: 4
  reaction:
    window: roll_modification
    challengeable: false
    condition: {op: always}
    effect: {op: modify_roll, amount: 2, source: $card}
```

```yaml
- id: base.challenge.challenge
  kind: challenge
  name: "Challenge"
  copies: 8
  reaction:
    window: card_played
    condition: {op: event_matches, kind_in: [hero, item, magic], played_by: {op: not_self}}
    effect:
      op: contest_roll
      a: {roller: $self, dice: "2d6", kind: challenge}
      b: {roller: $event.player, dice: "2d6", kind: challenge}
      on_a_wins: {op: cancel_event, and_discard: true}
      on_b_wins: {op: noop}
      on_tie:    {op: noop}          # base rule: tie goes to the defender
```

### 6.6 Party Leader

```yaml
- id: base.leader.charismatic_song
  kind: party_leader
  name: "The Charismatic Song"
  card_class: bard
  text: "Each time you roll to use a Hero's effect, add +1."
  triggers:
    - on: roll.started
      timing: pre
      while_in: leader
      condition:
        op: all
        of: [{op: roll_is, kind: hero_ability}, {op: roll_is, roller: $self}]
      effect: {op: modify_roll, amount: 1, source: $card}
```

### 6.7 Trigger block (shared by all kinds)

```yaml
triggers:
  - on: monster.slain          # event name
    timing: post               # pre | resolve_after | post
    while_in: party            # zone where this ability is live
    once_per_turn: false
    priority: 0
    condition: {...}
    effect: {...}
```

---

## 7. `rules.yaml`

The base game itself is data. This is the file your variant will most want to fork.

```yaml
id: base
classes: [bard, fighter, guardian, ranger, thief, wizard]

setup:
  starting_hand: 5
  monster_row_size: 3
  leader_selection: random      # random | draft | choice

turn:
  action_points_per_turn: 3
  hand_limit: null              # null = no limit

actions:                        # THE action menu; add/remove/re-cost freely
  - id: draw
    label: "Draw a card"
    cost: {action_points: 1}
    effect: {op: draw, target: $self, count: 1}
  - id: play_hero
    label: "Play a Hero"
    cost: {action_points: 1}
    requires: {op: has_card, player: $self, zone: hand, filter: {op: card_kind_is, kind: hero}}
    effect: {op: play_card_from_hand, kind: hero, challengeable: true}
  - id: use_hero_ability
    cost: {action_points: 1}
  - id: attack_monster
    cost: {action_points: 2}
  - id: equip_item
    cost: {action_points: 1}
  - id: cast_magic
    cost: {action_points: 1}
  - id: discard_and_draw        # "discard your hand, draw that many +1" style variants
    cost: {action_points: 3}
    enabled: false              # shipped off; flip to true in a variant

windows:
  card_played:      {order: seat_left_of_active, reopen_on_action: true}
  roll_modification: {order: seat_left_of_active, reopen_on_action: true}
max_reaction_depth: 8

victory:
  - id: slay_three
    text: "Slay 3 Monsters"
    condition: {op: slain_count, player: $player, cmp: ">=", value: 3}
  - id: full_party
    text: "One Hero of each class in your party"
    condition: {op: party_covers_all_classes, player: $player}

zones:
  - {id: main_deck,   scope: shared, visibility: hidden, ordered: true}
  - {id: discard,     scope: shared, visibility: public, ordered: true}
  - {id: monster_deck,scope: shared, visibility: hidden, ordered: true}
  - {id: monster_row, scope: shared, visibility: public, ordered: true, capacity: 3}
  - {id: hand,        scope: player, visibility: owner,  ordered: false}
  - {id: party,       scope: player, visibility: public, ordered: false}
  - {id: leader,      scope: player, visibility: public, ordered: false}
  - {id: slain,       scope: player, visibility: public, ordered: false}
```

---

## 8. Validation Strategy

Pydantic gives structural validation. A second **semantic** pass runs after load:

1. Every `op` referenced exists in the effect/condition/selector registry.
2. Every `$ref` is bindable in its lexical scope (catches `$victim` used before `choose`).
3. Every `on:` event name is either a core event or emitted by some loaded card.
4. Every `card_class` is in `rules.classes`; every `art` path exists (warning only).
5. Deck arithmetic: `sum(copies)` per zone is sane; monster deck non-empty.
6. Every `id` is unique across packs unless it is an explicit `patch`.

Failures print as a table and exit non-zero — so `hts validate` is CI-able and a modder gets a
useful error instead of a `KeyError` twenty minutes into a game.

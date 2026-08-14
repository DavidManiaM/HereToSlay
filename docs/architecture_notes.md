# Architecture Notes

> **Prime directive:** this codebase is a *host* for a rules variant that does not exist yet.
> Nothing about the base game may be spelled in Python `if` statements. Python provides
> *mechanisms*; YAML provides *policy*.

---

## 1. The Three-Layer Rule

```
┌───────────────────────────────────────────────────────────┐
│  PRESENTATION      ui/cli/, ui/pygame/, ai/               │
│  - renders GameState, submits Intents & Decisions          │
│  - MAY NOT import anything that mutates state              │
└───────────────────────────────────────────────────────────┘
                 ▲ read-only view      ▼ Intent / Decision
┌───────────────────────────────────────────────────────────┐
│  ENGINE            core/                                  │
│  - GameState, EventBus, EffectInterpreter, TurnMachine     │
│  - pure Python, deterministic, zero I/O, zero pygame        │
└───────────────────────────────────────────────────────────┘
                 ▲ CardDef / RuleSet (immutable)
┌───────────────────────────────────────────────────────────┐
│  CONTENT           content/ (pydantic schema + loader)     │
│                    data/<pack>/*.yaml                      │
│  - card definitions, rule constants, win conditions        │
└───────────────────────────────────────────────────────────┘
```

**Enforced invariants** (a test in `tests/test_layering.py` will assert these by walking imports):

| Rule | Rationale |
|---|---|
| `core/` never imports `pygame`, `rich`, `input()`, `print()`, `random` | headless, testable, deterministic |
| `core/` never imports `ui/` or `ai/` | one-way dependency |
| `ui/` never mutates `GameState` directly — only via `engine.submit(...)` | single write path = single place to log/replay/network |
| `content/` never imports `core/` | schemas describe data, they don't execute it |

The last one matters most: **`CardDef` is inert data.** It has no `.play()` method. Behaviour
lives in the interpreter, keyed by strings found in the data. This is what makes a mod possible
without touching a class hierarchy.

### 1.1 Why not a `Card` base class with subclasses?

The obvious OO design (`class Hero(Card)`, `class Monster(Card)`, `card.on_play()`) is the trap
here. It forces every new card in your variant to be a new Python class, and every new *kind* of
effect to be a new method on an interface that all 100+ cards must satisfy. Instead:

- **One** runtime `CardInstance` (identity + location + attached state).
- **One** immutable `CardDef` (parsed YAML).
- Behaviour = an **effect tree** interpreted at runtime.

Adding "a Hero that, when challenged, forces the challenger to discard their leader" is a YAML
edit. Adding a genuinely new *verb* is one decorated function.

---

## 2. Core Concepts

### 2.1 GameState — a plain, snapshot-able data structure

```python
@dataclass(slots=True)
class GameState:
    content: ContentRegistry  # cards + rules, immutable; `state.rules` is the shorthand
    players: dict[PlayerId, PlayerState]  # each holds its own action_points
    turn_order: list[PlayerId]
    active_player: PlayerId
    zones: dict[ZoneId, Zone]  # shared *and* player-scoped: "discard", "hand:p1"
    cards: dict[CardId, CardInstance]  # the single registry of every instance
    rng: DeterministicRng
    phase: str
    turn_number: int
    flags: dict[str, Any]  # scratch space for mods; never read by the engine
    winner: PlayerId | None
```

Two shapes moved during Phase 2, for reasons worth keeping: the state holds the whole
`ContentRegistry` rather than just its `RuleSet` (a `CardInstance` is a def id plus a location, so
anything reasoning about one needs its `CardDef`; the registry is immutable, so clones share it),
and `action_points` lives on `PlayerState` (a variant may hand AP to a non-active player;
`state.action_points` still reads the active seat, which is what `$action_points` resolves to).

Design points:

- **Cards live in exactly one `Zone`, and `Zone` is a generic ordered container.** There is no
  `player.hand: list[Card]` *and* `player.party: list[Card]` as bespoke fields — both are
  `Zone`s in a dict, with metadata (`ordered`, `visibility`, `owner`, `capacity`). A variant that
  adds a "Vault" zone or a "Graveyard" adds a zone declaration in `rules.yaml`, not a field.
- **`flags`** is the escape hatch: `set_flag`/`get_flag` effect ops let a modder store
  arbitrary variant state (a "corruption counter", "phase of the moon") without an engine change.
- **`rng` is seeded and owned by the state**, so a game is fully reproducible from
  `(seed, decision_log)`.

### 2.2 Zones

| Zone | Owner | Visibility | Ordered |
|---|---|---|---|
| `main_deck` | shared | hidden | yes |
| `discard` | shared | public | yes |
| `monster_deck` | shared | hidden | yes |
| `monster_row` | shared | public | yes (capacity 3) |
| `leader_pool` | shared | hidden | yes (undealt Party Leaders) |
| `hand:<p>` | player | owner-only | no |
| `party:<p>` | player | public | no |
| `leader:<p>` | player | public | no |
| `slain:<p>` | player | public | no |
| `limbo` | shared | hidden | no (in-flight cards mid-resolution) |

`limbo` exists so a card being played is *somewhere* while the reaction window is open — a
Challenge that cancels it must know where to send it (→ `discard`), and a card must never be in
two zones at once.

### 2.3 CardInstance vs CardDef

```python
@dataclass(slots=True)
class CardInstance:
    id: CardId  # unique per instance ("base.hero.dodgy_dealer#2")
    def_id: str  # -> CardDef in the content registry
    zone: ZoneId
    owner: PlayerId | None  # who it belongs to
    controller: PlayerId | None  # who currently uses it (theft!)
    attachments: list[CardId]  # Items equipped onto a Hero
    attached_to: CardId | None  # the inverse link, so the checker can prove both ends
    tapped: bool  # "used this turn" marker
    state: dict[str, Any]  # per-instance mod scratch (counters etc.)
```

`owner` vs `controller` is deliberate: Here to Slay has Hero-stealing, and a variant might have
"borrow until end of turn". Splitting them now costs nothing and saves a refactor later.
`GameState.move_card` is the single write path and encodes the split mechanically: **control
follows location** (a card in a player-scoped zone is controlled by that player), while `owner` is
set once, on first acquisition, and never moves again unless an effect says so.

---

## 3. The Event Bus

Every state change of consequence is an **Event** pushed through a three-phase pipeline.

```
        ┌──────────────────────────────────────────────┐
emit →  │  PRE      subscribers may MODIFY or CANCEL   │ ── cancelled ──► stop
        ├──────────────────────────────────────────────┤
        │  RESOLVE  exactly one mutator applies it     │
        ├──────────────────────────────────────────────┤
        │  POST     subscribers may REACT (queue fx)   │
        └──────────────────────────────────────────────┘
```

- **PRE** is where Challenge cards, "prevent" abilities, and cost-reduction live. A PRE
  subscriber returns a `Verdict`: `CONTINUE`, `MODIFIED(new_event)`, or `CANCELLED(reason)`.
- **RESOLVE** is the only place `GameState` is mutated, by a single registered mutator per
  event type. Keeps mutation auditable.
- **POST** is where "whenever X happens, do Y" triggers fire. Reactions are queued and resolved
  after the current event finishes (no unbounded re-entrancy mid-mutation).

### 3.1 Subscription is declarative

Cards do not call `bus.subscribe()`. The engine subscribes on their behalf when they enter a
zone where their ability is live (`party`, `leader`, or `hand` for reaction cards), reading:

```yaml
triggers:
  - on: monster.slain
    timing: post
    while_in: party          # ability is live only from this zone
    condition: {op: event_actor_is, player: $self}
    effect: {op: draw, count: 1, target: $self}
```

Subscriptions are therefore *derived from state*, not accumulated as side effects — which means
they survive save/load and cannot leak when a card leaves play.

### 3.2 Ordering

Subscribers are sorted by `(priority, zone_kind, seat_order, card_id)`. `priority` is an integer
in the card data (default 0). Deterministic ties are non-negotiable — replay depends on it.

### 3.3 Event taxonomy

`noun.verb`, past-tense at POST, present at PRE:

```
turn.started      turn.ended        phase.changed
action.declared   action.paid       action.completed
card.drawn        card.played       card.discarded    card.moved
hero.entered_party hero.left_party  item.equipped     item.unequipped
roll.started      roll.modified     roll.resolved
monster.attacked  monster.slain     monster.failed    monster_row.refilled
challenge.declared challenge.resolved
player.won        game.ended
```

Mods may invent new event names freely; the bus does not validate against an enum (it validates
against the *union of names referenced by loaded content*, so typos are still caught at load).

---

## 4. The Suspension Problem (and its solution)

The single hardest constraint: **the engine must ask questions** ("which player do you steal
from?", "anyone want to play a Modifier?") but **must not block**, because the same engine has to
run under a CLI `input()`, a pygame frame loop, an AI, and a test harness.

**Solution: effects are Python generators.** An effect handler `yield`s a `Request` and receives
the answer:

```python
@effect("steal_hero")
def steal_hero(ctx, params):
    victim = yield ctx.ask_choose_player(chooser=ctx.self, among=ctx.opponents())
    hero = yield ctx.ask_choose_card(chooser=ctx.self, among=ctx.party_of(victim))
    yield from ctx.emit(CardMoved(hero, to=ctx.zone("party", ctx.self)))
```

`yield from` composes the whole call stack into one generator, so the *entire pending
computation* is preserved by the Python frame — no manual continuation objects, no state
machine per effect.

The driver loop:

```python
status = engine.start()  # -> Awaiting(request) | Quiescent | GameOver
while isinstance(status, Awaiting):
    answer = presenter.answer(status.request)  # CLI prompt / pygame click / AI policy
    status = engine.submit(answer)
```

**Trade-off, accepted deliberately:** a suspended generator stack is not serialisable, so
save-games are only permitted at *quiescent* points (between actions), never mid-decision. This
is fine for a card game and buys enormous readability. Documented in `rules_engine.md §6`.

---

## 5. Registries (the extension seams)

Five decorator-based registries. Each is the answer to "how do I add a new kind of X?"

| Registry | Adds | Signature |
|---|---|---|
| `@effect("op_name")` | a new **verb** | `(ctx, params) -> Generator` |
| `@condition("op_name")` | a new **predicate** (Strategy) | `(ctx, params) -> bool` |
| `@selector("name")` | a new **target set** | `(ctx, params) -> list[Entity]` |
| `@mutator(EventType)` | how an event **changes state** | `(state, event) -> None` |
| `@cost("name")` | a new **payment type** | `(ctx, params) -> Generator[bool]` |

A content pack may ship `plugin.py`; the loader imports it, which runs the decorators. So a mod
that needs a truly new verb is *still* a drop-in directory, not a fork.

`ctx` (`EffectContext`) is the only API surface effects get: state access, target resolution,
`emit`, `ask_*`, and variable binding. Keeping it narrow keeps mods from reaching into engine
internals that will change.

---

## 6. Turn Structure as a State Machine

`TurnMachine` reads its transition table from `rules.yaml`, it is not hardcoded:

```yaml
phases:
  - id: turn_start
    on_enter: [{op: set_action_points, value: "$rules.action_points_per_turn"}]
    auto_advance: true
  - id: main
    loop_while: {op: compare, left: $action_points, cmp: ">", right: 0}
    allows: [draw, play_hero, use_hero_ability, attack_monster, equip_item, cast_magic]
  - id: turn_end
    on_enter: [{op: enforce_hand_limit}]
    auto_advance: true
```

A variant with an upkeep phase, a two-action turn, or a "draft phase" edits this table. The
Python `TurnMachine` only knows how to *walk* a table.

---

## 7. Determinism, Replay, and What It Buys

`Game = f(content_hash, seed, [decision₀, decision₁, ...])`

Because the core has no ambient randomness or I/O, the decision log fully reproduces a game.
Consequences we get for free:

- **Replay / undo** — re-run the log to step *n*.
- **Network play** — send decisions, not state.
- **AI search** — deep-copy state, run rollouts with a scripted policy.
- **Bug reports** — a seed + log is a reproducible test case.
- **Regression tests** — golden logs assert card behaviour end to end.

Rule: `random` is banned in `core/`; only `state.rng` may be used, and every draw from it is
logged. `DeterministicRng` implements splitmix64 itself rather than wrapping `random.Random`,
because the standard library makes no promise that `shuffle` produces the same permutation in a
future Python — and a replay log that stops reproducing is worse than no replay log. Seeds may be
strings (`--seed dragons`), hashed with sha256 rather than `hash()`, whose salt changes per
process.

---

## 8. Presentation Layer

Both UIs implement one protocol:

```python
class Presenter(Protocol):
    def render(self, view: GameView) -> None: ...
    def answer(self, request: Request) -> Decision: ...
```

- `GameView` is a **redacted, read-only projection** of `GameState` from one player's seat
  (hidden zones become counts). Building it in the core, not the UI, means hidden information
  can never leak into a renderer — and the AI gets the same fair view.
- **CLI** (`rich`) is the primary development UI and must stay playable head-to-head forever;
  it is the fastest way to test a new card.
- **PyGame** implements the same protocol. Its main loop pumps events, calls `render(view)`
  every frame, and feeds `answer()` from clicks. Because `answer()` cannot block a frame, the
  pygame presenter is itself state-machine-driven: `pending_request` drives which widgets are
  interactive ("highlight legal targets"), and a click resolves it.

The rendering of *any* card is generic: a card's `art`, `name`, `class`, and `text` come from
its `CardDef`. **No card is ever drawn by bespoke code**, so a mod's new card renders correctly
the moment its YAML and PNG exist.

---

## 9. Directory Layout

```
here-to-slay/
├─ pyproject.toml            # uv-managed
├─ docs/                     # this directory — living memory
├─ data/
│  ├─ base/                  # the official game as a content pack
│  │  ├─ pack.yaml           # id, name, version, load order, deps
│  │  ├─ rules.yaml          # zones, phases, action costs, win conditions
│  │  └─ cards/{heroes,monsters,items,magic,modifiers,challenges,leaders}.yaml
│  └─ variants/              # ← your mod lives here, base untouched
├─ assets/                   # art, fonts (pygame only)
├─ src/here_to_slay/
│  ├─ content/   schema.py loader.py registry.py
│  ├─ core/      state.py zones.py events.py bus.py interpreter.py
│  │             effects/ conditions/ selectors/ turn_machine.py
│  │             rolls.py victory.py rng.py view.py engine.py
│  ├─ ui/cli/    presenter.py render.py
│  ├─ ui/pygame/ app.py scene.py widgets.py layout.py
│  ├─ ai/        random_agent.py heuristic_agent.py
│  └─ cli.py     # entry point: validate | play | replay | gui
└─ tests/
```

---

## 10. Open Design Questions (decide before Phase 3)

1. **Simultaneous reaction windows** — real play is "anyone may respond". Sequential polling in
   seat order is deterministic and simple; a true simultaneous window needs a priority-pass
   system. *Proposal: sequential in seat order starting left of the active player, with an
   explicit pass; re-open the window if anyone acts.*
2. **Nested challenges** — can a Challenge be challenged? In the real game, yes. The recursive
   reaction window handles it naturally; we just need a depth cap (config: `max_reaction_depth`).
3. **Undo granularity** — per-action or per-decision? *Proposal: per-action, quiescent points only.*
4. **Hidden-information AI** — the AI gets `GameView`, so it cannot cheat, but rollout search
   then needs determinisation (sampling hidden cards). Deferred to Phase 8.

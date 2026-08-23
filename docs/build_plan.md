# Build Plan

Granular roadmap. Each phase has a **deliverable**, an **acceptance test**, and an explicit
**do-not-do-yet** so scope doesn't leak forward.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## Phase 0 — Scaffolding `[x]`

- [x] `uv init` (Python 3.13)
- [x] `uv add pygame-ce pydantic pyyaml rich`, `uv add --dev pytest`
- [x] `docs/` with architecture, schemas, rules engine, build plan
- [x] `src/` layout in `pyproject.toml`; `hts` console entry point
- [x] `pytest` + `ruff` config; test run green

> **Note:** `pygame-ce` (Community Edition) is installed rather than `pygame`. It is the actively
> maintained fork, a drop-in `import pygame`, and has current wheels. Say the word if you want
> upstream `pygame` instead — it's a one-line swap.

**Acceptance:** `uv run pytest` exits 0. `uv run hts --help` prints usage.

---

## Phase 1 — Content Schema & Loader `[x]`

**Deliverable:** YAML in, validated immutable `ContentRegistry` out. No game logic at all.

- [x] `content/schema.py` — pydantic models: `PackDef`, `RuleSet`, `CardDef` (discriminated
      union on `kind`), `EffectNode`, `ConditionNode`, `TriggerDef`, `RollDef`, `Band`
- [x] `content/vocabulary.py` — the op catalogue *as data* (see note below)
- [x] `content/loader.py` — glob, parse, merge packs, apply `patches`, build registry
- [x] `content/validate.py` — the semantic pass (`card_schemas.md §8`)
- [x] `hts validate <pack>` CLI command with a `rich` error table
- [x] Fixtures: 4 hand-written cards + 6 deliberately broken packs
- [x] `data/base/rules.yaml` — the base rule set (cards still Phase 6)

**Acceptance:** ✅ `hts validate` passes on good fixtures, fails with a *path-qualified* message
on each broken one. `uv run pytest` → 53 passed.
**Not yet:** any `GameState`, any effect execution.

### Decisions taken during Phase 1

1. **The op catalogue is data, not a closed union.** `EffectNode`/`ConditionNode` validate only
   the `{op: ..., **params}` envelope; `content/vocabulary.py` declares each op's *param roles*
   (which params hold nested effects, conditions, selectors, zones, bindings). The validator and,
   later, the interpreter both walk those roles. Closing the union in pydantic would have made
   "add a new op" a schema edit — the exact coupling the project exists to avoid. A pack's
   `plugin.py` calls `Vocabulary.extend(...)`.
2. **`content/` still imports nothing from `core/`** — the vocabulary is why it doesn't have to.
   `tests/test_layering.py` asserts this by walking the import graph.
3. **YAML 1.2 booleans.** PyYAML implements YAML 1.1, where `on:` is the boolean `True` — which
   silently ate every trigger's `on: monster.slain` key. `content/loader.py:PackLoader` narrows
   the bool resolver to `true`/`false`, so `on`, `off`, `yes`, `no` stay strings.
4. **Two merge mechanisms for variants**, beyond `patches`: a later pack's `rules.yaml`
   deep-merges over earlier ones, and lists of `{id: ...}` objects (`actions`, `zones`,
   `phases`, `victory`) merge **by id** — so re-costing one action does not mean restating the
   table. `{id: x, remove: true}` drops an entry. `patches` also accepts `target: rules`.
5. **`content_hash`** on the registry (sha256 of the canonical card+rules projection) so a replay
   can refuse to run against edited content (`architecture_notes.md §7`).

---

## Phase 2 — Core State Model `[x]`

**Deliverable:** a `GameState` you can build, mutate through zone primitives, snapshot, and diff.

- [x] `core/ids.py` — `NewType` ids (`PlayerId`, `CardId`, `ZoneId`) + the composition helpers
      (`hand:p1`, `base.hero.x#2`) that keep id shapes in one place
- [x] `core/zones.py` — generic `Zone` (ordered/unordered, visibility, capacity)
- [x] `core/state.py` — `GameState`, `PlayerState`, `CardInstance`, `clone`/`snapshot`/`diff`
- [x] `core/rng.py` — `DeterministicRng` (seeded, every call logged)
- [x] `core/setup.py` — build a game from `RuleSet` + player list + seed
- [x] `core/view.py` — `GameView` redaction per seat
- [x] `core/invariants.py` — the invariant checker (`rules_engine.md §8`), `HTS_STRICT`-gated
- [x] `core/errors.py` — `SetupError`, `ZoneError`, `EngineInvariantError`
- [x] Fixture pack `tests/fixtures/table` — base rules + enough dull cards to deal a real table

**Acceptance:** ✅ 4 players on the base rule set deal 5-card hands, one leader each, a 3-monster
row, and 12 cards left in the deck; two setups with the same seed produce byte-identical
snapshots; every seat's serialised view is searched for every hidden card id and finds none.
`uv run pytest` → 136 passed.
**Not yet:** events, turns.

### Decisions taken during Phase 2

1. **`core/` implements its own PRNG.** `tests/test_layering.py` bans `random` in the engine, and
   that ban is worth keeping — ambient randomness is exactly how replay quietly breaks. `rng.py`
   is ~20 lines of splitmix64 with rejection sampling, which also buys a guarantee `random.Random`
   cannot: a log recorded today still replays on a future Python whose `shuffle` changed.
2. **`GameState` holds the whole `ContentRegistry`, not just the `RuleSet`.** A `CardInstance` is
   a def id plus a location, so anything reasoning about one needs its `CardDef`. The registry is
   immutable, so clones share it; `state.rules` is a property and reads the same everywhere else.
3. **Control follows location; ownership does not.** `move_card` sets `controller` to the
   destination zone's owner and fills in `owner` only on first acquisition. That is what makes
   "steal a Hero" and "return stolen Heroes at end of turn" both expressible without engine edits.
   `CardInstance.attached_to` was added as the inverse of `attachments` so the invariant checker
   can prove equipment links point both ways.
4. **The setup RNG order is part of the replay contract** and is written down in `setup.py`'s
   docstring: shuffle hidden shared zones (sorted by zone id) → leaders → hands → monster row.
   Cards are minted in sorted definition-id order, so renaming a YAML file cannot reshuffle an
   existing seed's game.
5. **Setup refuses rather than improvises.** Too few cards to deal hands is a `SetupError` naming
   the shortfall; a monster deck that runs short is *not* (a row refills lazily in a normal game).
   `leader_selection: draft|choice` raises until the decision system exists — it cannot be faked.
6. **Base data has no cards until Phase 6**, so `tests/fixtures/table` supplies deliberately dull
   cards against the *real* base rules. Phase 2's numbers are the shipping numbers.

---

## Phase 3 — Event Bus & Effect Interpreter `[x]` ★ *the critical phase*

**Deliverable:** the generator-driven interpreter. Everything downstream depends on this being
right, so it gets the most test attention.

- [x] `core/events.py` — `Event`, `Phase`, `Verdict` (CONTINUE/MODIFIED/CANCELLED), `EventFrame`,
      `Outcome`
- [x] `core/bus.py` — 3-phase dispatch, deterministic subscriber ordering, subscriptions
      *derived from state* (not accumulated), depth cap
- [x] `core/interpreter.py` — generator driver, `Request`/`Decision` protocol, suspend/resume
- [x] `core/context.py` — `EffectContext`: refs, selectors, bindings, `emit`, `ask_*`
- [x] `core/refs.py` — `$ref` splitting and the `{expr: ...}` evaluator
- [x] `core/registry.py` — `@effect` `@condition` `@selector` `@mutator` `@cost`
- [x] `core/mutators.py` — the RESOLVE handlers: the only writers during a dispatch
- [x] `core/effects/` — control flow, cards/zones, party, resources, meta ops
- [x] `core/conditions/` + `core/selectors.py` — the predicate and target-set catalogues
- [x] `core/log.py` — `DecisionLog`, `LogSource`, replay driver
- [x] Fixture pack `tests/fixtures/triggers` — Heroes that exist only to subscribe

**Acceptance:** ✅ a scripted test executes a nested effect (`seq` → `choose` → `if` →
`discard`), suspends twice for decisions, and resumes correctly; replaying the log reproduces the
state byte-for-byte (`diff_snapshots(...) == []`). `uv run pytest` → 352 passed.
**Not yet:** turns, rolls, real cards.

### Decisions taken during Phase 3

1. **An event is a name plus a payload, not a class.** A closed `CardDrawnEvent`/`MonsterSlainEvent`
   hierarchy would mean every new verb in a variant needs a Python type the bus knows about, while
   cards subscribe with a *string* (`on: monster.slain`). So the bus matches strings, and
   `@mutator` keys on the event name too, rather than on a type as `architecture_notes.md §5`
   originally sketched. One vocabulary, shared by content and engine.
2. **The event is immutable; the mutable part is an `EventFrame`.** `cancel_event` has to reach
   the dispatch happening around it, and the depth cap has to count nesting — both are properties
   of *this dispatch*, not of the event. So the bus owns a frame stack, and `Event.replace` /
   `EventFrame.modify` (the MODIFIED verdict) produce a new event rather than editing history.
3. **Cancellation propagates through the effect that asked.** An op whose event was cancelled
   returns `Outcome.CANCELLED`, and `seq` stops there. Without that, a countered discard would
   still pay out the rest of the card's text.
4. **Bindings are returned, not written into a shared scope.** `choose` returns a `Binding`, which
   `EffectContext.run` unwraps into the *immediately* enclosing `seq` and nowhere else — so a
   `choose` nested in an `if` cannot leak `$victim` to the `if`'s siblings. That is exactly the
   lexical rule `content/validate.py` already enforces at load time, so the two agree by
   construction.
5. **Forced questions are not asked.** `ask_*` resolves a choice with one legal answer itself.
   It is a pure function of state, so the replay stays exact, and it keeps both UIs from prompting
   "choose 1 of 1" on every card.
6. **`{expr: ...}` gets a 40-line recursive-descent parser, not `eval`.** `repeat.times` is
   documented as "may be an expression"; `eval` over content is both a security hole and
   non-deterministic. `+ - * / % ( )` over `$refs` and integers is as expressive as card text needs.
7. **Action points are written directly, not through an event** — the one deliberate exception to
   "state changes are events". There is nothing to cancel about a number going up, and Phase 4's
   `action.paid` is the right hook for cost reduction. Everything else in the catalogue emits.
8. **Selectors yield ids, never objects**, so `exclude:` can compare them, a filter can bind one as
   `$candidate`, and a decision log can print one.
9. **`slay_monster` does not refill the row.** *When* a new Monster turns up is policy, so it is a
   `refill_monster_row` step in `rules.yaml` (wired up in Phase 4), not a line of Python.
10. **Two vocabulary additions**, both in `content/`: `search` gained a `bind:` (without it the op
    could only ever be "look, then forget"), and `draw` gained an optional `from:` zone.

### Carried into Phase 4 on purpose

The ops that need the roll pipeline, the turn machine or reaction windows are declared in the
vocabulary but deliberately have no handler yet, so a pack using one fails loudly rather than
silently doing nothing: `roll`, `modify_roll`, `reroll`, `contest_roll`, `set_roll_result`,
`play_card_from_hand`, `attack_monster`, `use_ability`. Also deferred:

* the `@cost` catalogue — the registry seam exists, but its call shape belongs with `core/actions.py`
* **deck exhaustion**: `draw` stops at an empty deck; reshuffling the discard back in is policy and
  wants a `rules.yaml` knob, not a hardcoded rule
* `end_turn` / `extra_turn` leave a game flag for the turn machine to read at the next safe point
* a *chosen* (not random) pick out of a hidden hand marks the request `hidden: true` so a presenter
  renders backs, but the ids are still in the request; a genuinely leak-proof blind pick uses
  `random: true`

---

## Phase 4 — Turn Machine, Rolls, Victory `[x]`

**Deliverable:** a legal but cardless game — draw, pass, end turn, detect a win.

- [x] `core/turn_machine.py` — walks the `phases` table from `rules.yaml`
- [x] `core/actions.py` — intent validation, `legal_intents()`, cost payment
- [x] `core/rolls.py` — the full roll pipeline (`rules_engine.md §4`), dice-string parser
- [x] `core/windows.py` — reaction windows, seat ordering, re-entrancy, depth cap
- [x] `core/victory.py` — condition loop, checked after every resolution
- [x] `core/engine.py` — `Engine` facade: `start`/`submit`/`view`/`legal_intents`
- [x] `core/effects/rolls.py` + `core/effects/actions.py` — the ops carried over from Phase 3
- [x] Fixture packs `tests/fixtures/{play,cardless,deadlock}`

**Acceptance:** ✅ 4 players on a draw-only rule set play 20 turns and 60 actions with zero
invariant violations; a game seeded so the third Monster dies ends *on that attack*, mid-turn,
not at end of turn; a whole game replays from its log to a byte-identical state.
`uv run pytest` → 468 passed, and again under `HTS_STRICT=1`.
**Not yet:** UI beyond a test harness.

### Decisions taken during Phase 4

1. **The action menu is data too.** `legal_intents()` cannot enumerate "which Heroes may I play?"
   from a `requires:` condition — a predicate answers yes/no, it does not produce a list. So
   `ActionDef` gained `targets: [{param, from, where, prompt}]`, and the engine expands an action
   into one `Intent` per legal combination. An action whose target has no candidates is simply not
   offered, which is why "you may only play a Hero if you're holding one" now needs no `requires:`
   at all. This is the seam that keeps the CLI, pygame and the AI from each re-deriving legality.
2. **Windows declare when they open** (`on:`, `timing:`, `condition:` on `WindowDef`), and the
   *bus* opens them. A Challenge has to cancel the `card.played` it is challenging, so the window
   must run inside that dispatch's PRE phase — opening it around the emit would leave the Challenge
   nothing to cancel. Putting the trigger in data rather than in `play_card_from_hand` is also what
   makes `rules_engine.md §5`'s claim true: a variant adds a `damage_prevention` window on its own
   event with zero engine edits. `challengeable: false` reaches the bus as a window `condition`
   over `$event.challengeable`, so the bus never learns the word.
3. **`roll_modification` opens on `roll.resolved` PRE**, between the dice landing and the band
   being chosen. That ordering is the whole reason a Modifier changes *which outcome happens*
   rather than just a number, and it is one line of YAML.
4. **Rolls are objects passed by reference.** `Roll` is mutable and travels in `ctx.roll` and in
   the event payload, because a Modifier played three frames deep has to reach *this* roll.
   `Event.__getattr__` was added so `$event.roll.total` and `$event.challengeable` work — content
   invents payload keys, and a window condition has to be able to read one.
5. **One step at a time.** `TurnMachine.step()` performs exactly one atomic piece of a turn and
   returns, so between steps the game is *quiescent* and therefore saveable (`rules_engine.md §6`).
   One long generator for the whole game would have been simpler and never saveable.
6. **Two termination guards, both found by tests rather than reasoned about.** A phase that loops
   while nothing is legal ends (`tests/fixtures/deadlock`), and a phase whose actions keep being
   *cancelled before they pay for anything* ends after three refusals — the fixture leader "you may
   not draw" turned an infinite menu loop into an infinite game before that guard existed.
7. **Costs answer twice.** `@cost` handlers take `check_only`, so `can_afford()` (a plain function
   the UI may call every frame) shares one implementation with payment. A cost that tries to ask a
   question during an affordability check raises rather than half-suspending.
8. **Victory is checked after every action and at every phase boundary**, not inside a dispatch.
   Mid-Challenge the board is provisional — a Modifier still to be played can undo the slay that
   would have won — so the check lands at the first point where the board is settled again.
9. **`turn.after_action`** in `rules.yaml` is where `refill_monster_row` finally got wired,
   honouring Phase 3's decision 9: *when* a new Monster turns up is policy.

---

## Phase 5 — CLI: Playable Head-to-Head `[x]` ★ *the milestone that de-risks everything*

**Deliverable:** `uv run hts play` — a real, complete game in the terminal.

- [x] `ui/cli/render.py` — `rich` board: parties, hands, monster row, AP, discard
- [x] `ui/cli/presenter.py` — `CliPresenter(DecisionSource)`; numbered menus for every request
      kind (`choose_intent`, `choose_cards`, `choose_player`, `choose_option`, `reaction`,
      `confirm`)
- [x] Hot-seat privacy (clear screen + "press Enter" gate on seat change), `--seed`
- [x] Replay — shipped as a sibling command `hts replay <log> [--step]`, not a `--replay` flag on
      `play` (see decision 2); `hts play` auto-saves to `./hts_logs/<ts>_<seed>.json` unless
      `--no-save`
- [x] **Roll animation-lite.** Wired in Phase 7 via `Engine.recent_rolls`: the presenter prints
      dice, each modifier with its source, the total, and the band's tag once one has run.

**Acceptance:** ✅ (completed across Phases 6 and 7 — see the gap list below).
* A full head-to-head game is playable end to end against `tests/fixtures/play` — board, menus,
  costs, hot-seat gate, victory, log save and replay all work (verified by hand:
  `hts play tests/fixtures/play --search-path data`).
* "…with only the base pack" is **blocked on Phase 6**: `data/base` still provides `cards: []`,
  so the default `hts play` deals an empty table. This is expected sequencing, not a Phase 5
  defect.
* 482 tests pass (also under `HTS_STRICT=1`), 25 of them in `tests/test_cli_play.py`.

**Not yet:** pygame, AI.

### Decisions taken during Phase 5

1. **The presenter is a `DecisionSource`, not the `Presenter` protocol** sketched in
   `architecture_notes.md §8`. There is no separate `render(view)` entry point: `answer()` renders
   the board itself, immediately before it prompts. The engine only ever calls the UI at decision
   points, so a second render hook would have had no caller — pygame, which *does* need to draw
   every frame, will pull `engine.view(seat)` on its own clock instead.
2. **`hts replay` is its own command, not `play --replay`.** Replay takes arguments `play` does
   not (`--step`) and refuses arguments `play` needs (`--seed`, `--names` — the log carries both),
   so one parser would have been half-illegal flags in both directions.
3. **`cmd_replay` drives the engine with its own `ReplayViewer`, not `CliPresenter`.** It wraps
   `LogSource`, renders before every logged decision, and stops at the end of the log. The
   presenter's `silent=True` flag was built for this and is now unused by any caller.
4. **Redaction stays in the core.** `render.py` is a pure `GameView → rich` transformation; it
   prints a hand as faces only when `player.is_you`, and never touches `GameState`. The renderer
   physically cannot leak a hidden zone, because it was never given one.
5. **No card is drawn by bespoke code.** Names, class colours and monster requirement/band text
   all come from the `CardDef` via the registry, with a slug-prettifying fallback when no registry
   is passed — so a mod's card renders the moment its YAML exists.

### Known gaps carried out of Phase 5

These are real defects found by auditing the shipped code, each reproduced. None are blocking
Phase 6 (card content), but **gaps 1 and 3 should be closed before Phase 7**, whose whole subject
is rolls and reactions.

1. ~~**Roll display is dead code.**~~ **Closed in Phase 7.** `Engine.recent_rolls` harvests each
   step's `Execution` before it is discarded; the presenter reads that and keeps its own
   "already shown" count instead of writing to `state.flags`. `render_roll_result()` now has a
   caller too — it prints the band's `tag:`, which is the thing the Phase 5 bullet asked for.
2. **Emoji crash the CLI on a legacy Windows console.** `👾 💀 🎲 ⚔ 🏆` raise
   `UnicodeEncodeError` under a non-UTF-8 code page (reproduced on cp1250: `hts play` dies while
   printing the first board). *Fix shape:* construct the `Console` with an explicit UTF-8 file, or
   fall back to ASCII icons when `console.encoding` cannot take them.
3. **`_choose_cards` is the one prompt with no test, and has three bugs.**
   * `hidden=True` raises `UnboundLocalError` — line 211 reads a loop variable `c` that is not
     bound yet. It only survives today because `request.hidden` is `False` in every current
     request, and `and` short-circuits.
   * The `lo == hi == 1` auto-select branch appends `candidates[0]` unconditionally, so it can
     re-add an already-chosen card (2 candidates, `min=1 max=2`, player types `1` twice →
     `('c1','c1')` → the engine rejects it with "the same card was chosen twice"), or force a card
     the player just declined onto the selection.
   * The multi-pick loop re-prompts forever on EOF, because `_read_int` treats the empty string
     from a closed stdin as "invalid, try again". Harmless for a human, an infinite loop for a
     piped session.
4. **Header markup is printed literally.** `_render_header` calls `Text.append` with
   `"[dim][…][/dim]"`; `Text.append` does not parse markup, so the board shows
   `[dim][4f9dfbda][/dim]`. Use `Text.append(..., style="dim")` like every other line does.
5. **Monster band text is ambiguous.** `_render_monster_card` renders an open bound and the range
   separator with the same en dash, giving `––12` and `2––`. Print `≤12` / `2+` instead.
6. ~~**`cmd_replay` detects "log exhausted" by matching substrings of a `ReplayError` message.**~~
   **Closed in Phase 7.** `LogSource` raises a typed `ReplayExhausted`, and `cmd_replay` catches
   that instead of reading prose.
7. **Dead imports/locals** flagged by `ruff` in `ui/cli/` and `cli.py` (22 findings, mostly
   `F401`/`F841`/`E501` plus the ambiguous en dashes). The lint gate does not currently cover
   these files.

---

## Phase 6 — Base Content: Heroes, Monsters, Leaders `[x]`

**Deliverable:** the base game's card set as YAML. Mostly data work — where it needed Python, the
architecture had a hole and we fixed the *engine*, not the card.

- [x] 6 Party Leaders (`data/base/cards/leaders.yaml`)
- [x] 48 Heroes, 8 per class (`heroes.yaml`)
- [x] 15 Monsters with requirement gates + outcome bands + slain-Monster skills (`monsters.yaml`)
- [x] 6 Items incl. Cursed, 8 Magic (`items.yaml`, `magic.yaml`)
- [x] 32 Modifiers + 7 Challenges (`reactions.yaml`)
- [x] Golden test per card (`tests/test_base_content.py`)

**Acceptance:** ✅
* `uv run hts validate data/base --strict` → **OK, 88 card definitions, 136 physical cards**
  (115 main deck + 15 Monsters + 6 Leaders — the printed box contents).
* `uv run pytest` → **667 passed**, and again under `HTS_STRICT=1`.
* **60 random 3-player games on the real pack: 0 errors, 0 invariant violations, all terminated.**
  53 won by `slay_three`, 7 by `full_party` — both victory conditions fire in real play.
* `hts play data/base` deals, renders and plays end to end.

**Not yet:** the reaction system under load (Phase 7), AI (Phase 8), pygame (Phase 9).

### Decisions taken during Phase 6

1. **The rulebook contradicted four things this project had already built on.** They are listed
   in `rules_reference.md §7`; the sharpest is that **a Challenge tie goes to the challenger**,
   not the defender, which had been assumed the other way in both the docs and the Phase 4
   fixture. Verifying the rules turned out to be worth more than any single card.
2. **Four engine changes, each forced by a card that could not otherwise be written.** The bar
   was "no card-specific Python", not "no Python":
   * `PartyLeaderDef.ability` — The Shadow Claw's skill *costs an action point*, and a cost
     cannot live in a passive subscription.
   * `party_classes()` reads the leader zone (`CLASS_BEARING_ZONES`) — the rulebook is explicit
     that a Leader satisfies a class requirement and counts toward the six-class win, while never
     being a Hero for "N Heroes of any class".
   * `bind:`/`then:` on `draw` and `steal_card` — "DRAW 2; if one is a Challenge…" and "pull a
     card; if it is a Hero, pull another" need to *see* what moved. Without it those ops could
     only ever move cards blind. Six base cards depend on it.
   * `swap_zones` — "trade hands" cannot be two `for_each` loops, because the second would
     re-collect the cards the first just delivered. Both sides must be snapshotted first.
3. **A lasting effect is three declarative parts, not a new engine concept.** "+3 to your rolls
   until end of turn" is: an ability that sets a player flag, a `roll.started` trigger on the
   same card that reads it, and a `turn.ended` trigger that clears it. Enchanted Spell shows the
   variant — its triggers are `while_in: discard`, because a one-shot Magic card is already in
   the discard by the time any roll happens.
4. **Iron Resolve needed no mechanism at all.** "Cards you play cannot be challenged this turn"
   is a flag plus one extra clause on the `card_played` window condition in `rules.yaml` — which
   is exactly the claim `rules_engine.md §5` makes about windows being data.
5. **A slain Monster's reward is a trigger with `while_in: slain`.** The Monster sits in your
   party permanently, so that is where its subscription lives; `on_slay` stays for one-off
   payouts. No "permanent skill" concept was needed.
6. **The bottom band must be open-ended.** Bands are validated against the *natural* dice range,
   but a Modifier can push a total outside it — a -3 on a roll of 2 gives -1. Napping Nibbles
   shipped with a single `min: 2` band and raised mid-game in fuzzing. Every band table now ends
   in a band with no lower bound.
7. **Fuzzing found what the golden tests could not.** The per-card tests drive one band in
   isolation; 60 random games found an empty-deck `draw`+`bind` crash and the band-coverage hole
   above, because only a real game runs a deck to exhaustion or stacks two Modifiers.

### Phase 5 gaps closed along the way

Every card is tested through the terminal, so the CLI had to work. Closed gaps **2, 3, 4 and 5**:

* **Emoji no longer crash a legacy Windows console.** `render.py` probes `sys.stdout.encoding`
  once and falls back to an ASCII icon set; the console is also built with `errors="replace"` so
  no glyph can ever kill a game mid-board. The intent-label separator moved from an em dash to
  `-` in `core/actions.py` — typography was leaking out of the UI layer anyway.
* **`_choose_cards` rewritten.** It had an `UnboundLocalError` that was unreachable only because
  no card set `hidden` — Silent Shadow now does. It could also select the same card twice, which
  the engine rejects. It now offers only cards not already chosen, and has five tests.
* **EOF is no longer "invalid, try again".** `_read_raw` returns `None` for a closed stream
  rather than `""`, so a piped session stops instead of looping forever.
* **Header markup** (`[dim][…][/dim]` printed literally) and **band text** (`––12`, `2––`) fixed;
  bands now read `8+`, `5-7`, `4-`.

Gaps **1** and **6** were left for Phase 7, whose subject is rolls and reactions, and closed
there. Every Phase 5 gap is now closed except **7** (dead imports and long lines in `ui/cli/`,
which the lint gate still does not cover).

---

## Phase 7 — Reactions: Challenge & Modifier `[x]`

**Deliverable:** the interrupt system proven under load. Most of the machinery already existed;
the work was proving it holds when windows nest — and it did not, in three places.

- [x] Challenge cards + the contest-roll op
- [x] Modifier cards (+/- variants) + stacking
- [x] Challenge-a-challenge; Modifier-on-a-challenge-roll
- [x] Depth cap, window skip, seat-order tests
- [x] `tests/test_reactions.py` — 85 tests, including 60 randomised three-player games
- [x] Phase 5 **gap 1** (dead roll display) and **gap 6** (prose-matched replay exhaustion)
- [x] `Band.tag` — closes the three "successfully roll" deviations in `rules_reference.md §8`

**Acceptance:** ✅
* A scripted 3-deep chain (Hero ← veto ← veto ← veto) resolves correctly, strands nothing in
  `limbo`, and asks the same seats in the same order on every run.
* `uv run pytest` → **752 passed**, and again under `HTS_STRICT=1`.
* `uv run hts validate data/base --strict` → OK, 88 card definitions, 136 physical cards.
* 60 randomised 3-player games on the real pack, with seats reacting ~60% of the time: zero
  errors, zero invariant violations, nothing left in `limbo`, all terminated.

**Not yet:** AI (Phase 8), pygame (Phase 9).

### Decisions taken during Phase 7

1. **In a Challenge, both sides roll before either may be modified.** `contest_roll` used to
   resolve side A completely — modification window and all — before side B touched the dice, so
   playing a Modifier on a Challenge meant committing before you knew what you were beating. The
   rulebook has both players roll and *then* Modifiers played. Each side is now rolled
   `contested:`, the base `roll_modification` window declines to open on a contested side, and
   `contest.resolved` re-opens the same window over both rolls at once. **The engine does not
   know that a contest suppresses anything** — one `condition:` clause in `rules.yaml` does, and
   a variant that wants side-at-a-time modification deletes it.
2. **A window may declare `on:` as a list.** The alternative was a second window
   (`contest_modification`) that every Modifier card in the game would then have had to list
   alongside the first. `roll_modification` opens on `[roll.resolved, contest.resolved]`, and
   Modifier cards are unchanged.
3. **When a Modifier has two rolls to choose from, the engine asks.** `modify_roll` targets via
   a new `target_roll()` flow: an explicit ref wins, then `$roll` if one is in flight, then the
   rolls the event put on the table — one of which resolves silently, two of which raise a
   `ChooseOption`. No existing card gained a click.
4. **`Band.tag` — success is declared by the card being rolled, not inferred by the card
   watching.** A band is a range and has no opinion about whether landing in it is good. Tagging
   the band and emitting `roll.banded` with the tag is what let `Arctic Aries` and both Coins
   stop approximating "successfully" with a threshold. The engine attaches no meaning to any tag
   string; `success`/`failure`/`slain`/`spared`/`backfire` are base-pack convention.
5. **`roll.banded` is announced before the band's effect runs**, so cancelling it means "that
   outcome does not happen to you" — a subscriber, not an engine concept. `tests/fixtures/play`'s
   The Warden is the proof.
6. **Challenge-a-challenge is proved in a fixture, not in `data/base`.** The rulebook says a
   Challenge cannot be Challenged, so the base pack keeps `challengeable: false`. `Open Veto`
   (`challengeable: true`, `challenge` in its `kind_in`) shows the mechanism works with two
   settings and no engine involvement.
7. **`reopen_on_action: false` was silently disenfranchising seats.** `open_window` broke out of
   the poll *and* set `acted = False` after any reaction, so with reopening off the window shut on
   the first responder and the seats below them were never asked. It now means what it says: one
   pass, one reaction per seat.
8. **`Engine.recent_rolls` rather than a bus subscription.** Rolls live on the `Execution` of the
   turn-machine step that made them, and an `Execution` dies with its step; the engine harvests
   them as it goes into a bounded history. The CLI reads that and keeps its own "already shown"
   count — how much of the board a terminal has drawn is not part of the game, so it does not
   belong in `state.flags`, which is where the dead version of this looked.
9. **`ReplayExhausted` is its own exception.** "The log ran out" is the *expected* end of a partial
   replay, and detecting it by matching prose in a `ReplayError` message coupled `cli.py` to a
   sentence in `core/log.py`.

### Found while proving it

* The Protecting Horn (+1/-1 each time a Modifier is played) reacts to `roll.modified` and its own
  adjustment emits `roll.modified` too. It terminates because the card's condition asks *who
  played what* — not because the engine guards re-entry. That is the load case a naive
  implementation hangs on, so it now has a test of its own.
* Adding cards to `tests/fixtures/play` re-deals every seeded fixture game. A control test was
  passing for the wrong reason because the new Warden happened to be dealt to the seat it was
  asserting about. Tests that depend on a specific Leader now place it explicitly — and clear the
  dealt one first, since the `leader` zone declares no capacity and would otherwise hold both.

---

## Phase 8 — AI Agents `[x]`

**Deliverable:** solo play and the fuzz harness.

- [x] `ai/random_agent.py` — uniform over `legal_intents()`
- [x] `ai/heuristic_agent.py` — weighted scoring, weights in YAML (so *your variant can retune
      the AI without touching Python*)
- [x] `hts sim --games 1000` — fuzz for crashes, invariant breaks, non-termination

**Acceptance:** ✅
* 1000 random 3-player games simulated via `hts sim data/base --games 1000`: **0 errors, 0 invariant violations, all terminated under turn cap** (906 won by `slay_three`, 94 won by `full_party`, average 28.2 turns/game).
* Heuristic agent tested with weights from `data/base/ai_weights.yaml`: 200 games completed with 0 errors and 0 invariant violations.
* `uv run pytest` → **790 passed** (42 new AI and simulation tests in `tests/test_ai.py`).
* All `ruff` checks pass cleanly across the codebase (including Phase 5 gap 7).

**Not yet:** pygame (Phase 9).

### Decisions taken during Phase 8

1. **Both agents implement `DecisionSource`.** Because the engine's interface is pure
   request/decision flow, the AI agents pass directly to `Engine.run()` without any special-case
   branches in `core/`.
2. **AI weights are declarative YAML.** `data/base/ai_weights.yaml` configures scoring weights
   for actions, reactions, and choices. A variant pack can include its own `ai_weights.yaml`
   to retune or extend AI behavior for new actions without touching Python.
3. **Random tie-breaking prevents cycling.** When multiple choices share the highest score, the
   heuristic agent uses seeded randomness to break ties, ensuring games do not get trapped in loops.
4. **Phase 5 gap 7 closed.** Cleaned up all lint findings across `ui/cli/`, `cli.py`, and test files.

---

## Phase 9 — PyGame UI

**Deliverable:** the graphical client. Built last on purpose: by now the rules are proven, so
this phase is *only* rendering and input.

- [ ] `ui/pygame/app.py` — window, clock, scene stack, resize
- [ ] `ui/pygame/layout.py` — resolution-independent anchored layout
- [ ] `ui/pygame/widgets.py` — `CardSprite` (renders **any** card from its `CardDef`: art, name,
      class colour, text — never per-card code), `ZoneWidget`, `Button`, `Toast`
- [ ] `ui/pygame/presenter.py` — non-blocking `answer()`; `pending_request` drives which widgets
      highlight as legal targets
- [ ] Animation queue: card moves, dice tumble, modifier pop-ins (cosmetic, driven by the event
      stream — never gates the engine)
- [ ] Placeholder art generated procedurally so a new card is visible before an artist touches it

**Acceptance:** a full game start→win with mouse only; the CLI still works unchanged.

---

## Phase 10 — Modding Support ★ *the actual point of the project*

**Deliverable:** proof the architecture holds, by building a variant.

- [ ] `docs/modding_guide.md` — tutorial: new card → new condition → new effect op → new win condition
- [ ] Pack plugin loading (`plugin.py` registering ops)
- [ ] `hts new-pack <name>` scaffolder
- [ ] **A sample variant** exercising every seam: a new class, a new zone, a new action, a new
      reaction window, an altered win condition
- [ ] `hts diff-pack base variants/x` — show what a pack changes

**Acceptance:** the sample variant is implemented with **zero edits to `core/`**. If it needs
one, that's a design bug to fix here — not in your variant.

---

## Phase 11 — Polish

- [ ] Save/load at quiescent points
- [ ] Replay viewer (step through a logged game in the UI)
- [ ] Sound hooks, settings screen
- [ ] Performance pass (view construction, sprite caching)
- [ ] `README.md` with real instructions

---

## Sequencing Rationale

1. **Content before state, state before events, events before turns.** Each phase's tests only
   need the phases beneath it.
2. **CLI before pygame.** A terminal game proves the rules; debugging a rule through a graphics
   layer is the classic way to lose a week.
3. **Cards after the engine.** If cards come first, engine gaps get papered over with
   card-specific hacks — exactly what breaks modifiability.
4. **The mod is the acceptance test.** Phase 10 isn't a nicety; it's the phase that *proves*
   Phases 1–9 were built correctly.

---

## Current Status

**Phases 0–8 complete.** `uv run hts validate data/base --strict` is green on 88 card definitions
and 136 physical cards; `uv run pytest` runs **790 tests**, and the whole suite also passes under
`HTS_STRICT=1` (invariants checked after every mutation).

**The base game is playable, end to end, with the real card set.** `hts play data/base` deals six
Party Leaders, 48 Heroes across all six classes, 15 Monsters with requirement gates and outcome
bands, Items (including Cursed ones), Magic, Modifiers and Challenges — then runs a full game
with a `rich` board, numbered menus, a hot-seat privacy gate, a live roll breakdown, an auto-saved
decision log, and `hts replay` to walk it back. **Both** victory conditions fire in real play.

**AI agents and headless simulation are fully operational.** Both `RandomAgent` and
`HeuristicAgent` implement the `DecisionSource` protocol. Scoring weights for the heuristic agent
are configured via `data/base/ai_weights.yaml` so variant packs can customize AI behavior without
touching Python. `hts sim data/base --games 1000` runs 1,000 automated games with 0 errors and
0 invariant violations.

**Phase 5 gap 7 is closed.** All lint warnings and formatting issues across `ui/cli/`, `cli.py`, and
test files have been cleaned up and verified with `ruff check .`.

Next up is **Phase 9 — PyGame UI**: window, scene stack, widget hierarchy, anchored layout,
non-blocking presenter, and procedural card rendering.

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

## Phase 3 — Event Bus & Effect Interpreter ★ *the critical phase*

**Deliverable:** the generator-driven interpreter. Everything downstream depends on this being
right, so it gets the most test attention.

- [ ] `core/events.py` — `Event` base, taxonomy, `Verdict` (CONTINUE/MODIFIED/CANCELLED)
- [ ] `core/bus.py` — 3-phase dispatch, deterministic subscriber ordering, subscriptions
      *derived from state* (not accumulated)
- [ ] `core/interpreter.py` — generator driver, `Request`/`Decision` protocol, suspend/resume
- [ ] `core/context.py` — `EffectContext`: refs, selectors, bindings, `emit`, `ask_*`
- [ ] `core/registry.py` — `@effect` `@condition` `@selector` `@mutator` `@cost`
- [ ] `core/effects/` — control flow, cards/zones, party, resources, meta ops
- [ ] `core/conditions/` — the predicate catalogue
- [ ] `core/log.py` — `DecisionLog`, replay driver

**Acceptance:** a scripted test executes a nested effect (`seq` → `choose` → `if` → `draw`),
suspends twice for decisions, and resumes correctly. Replaying the log reproduces the state
byte-for-byte.
**Not yet:** turns, rolls, real cards.

---

## Phase 4 — Turn Machine, Rolls, Victory

**Deliverable:** a legal but cardless game — draw, pass, end turn, detect a win.

- [ ] `core/turn_machine.py` — walks the `phases` table from `rules.yaml`
- [ ] `core/actions.py` — intent validation, `legal_intents()`, cost payment
- [ ] `core/rolls.py` — the full roll pipeline (`rules_engine.md §4`), dice-string parser
- [ ] `core/windows.py` — reaction windows, seat ordering, re-entrancy, depth cap
- [ ] `core/victory.py` — condition loop, checked after every resolution
- [ ] `core/engine.py` — `Engine` facade: `start`/`submit`/`view`/`legal_intents`

**Acceptance:** 4 players, `draw` only, 20 turns, no invariant violations; forcing a victory
condition ends the game at the right moment.
**Not yet:** UI beyond a test harness.

---

## Phase 5 — CLI: Playable Head-to-Head ★ *the milestone that de-risks everything*

**Deliverable:** `uv run hts play` — a real, complete game in the terminal.

- [ ] `ui/cli/render.py` — `rich` board: parties, hands, monster row, AP, discard
- [ ] `ui/cli/presenter.py` — implements `Presenter`; numbered menus from `legal_intents()`
- [ ] Hot-seat privacy (clear screen between players), `--seed`, `--replay <log>`
- [ ] Roll animation-lite: show raw dice, each modifier and its source, the final band

**Acceptance:** two humans finish a full game with only the base pack. **From here on, every new
card is testable in the terminal in seconds** — this is why the CLI comes before pygame.
**Not yet:** pygame, AI.

---

## Phase 6 — Base Content: Heroes, Monsters, Leaders

**Deliverable:** the base game's card set as YAML. Pure data work — if this phase needs Python
changes, the architecture has a hole and we fix the *engine*, not the card.

- [ ] 6 Party Leaders
- [ ] Heroes, all 6 classes
- [ ] Monsters with requirement gates + outcome bands
- [ ] Items, Magic
- [ ] Golden replay test per card

**Acceptance:** every card resolves in the CLI; each has a passing golden test.
**Blocked on:** your answers in `rules_reference.md §5` (exact card text/values).

---

## Phase 7 — Reactions: Challenge & Modifier

**Deliverable:** the interrupt system proven under load.

- [ ] Challenge cards + the contest-roll op
- [ ] Modifier cards (+/- variants) + stacking
- [ ] Challenge-a-challenge; Modifier-on-a-challenge-roll
- [ ] Depth cap, window skip, seat-order tests

**Acceptance:** a scripted 3-deep interrupt chain resolves correctly and deterministically.

---

## Phase 8 — AI Agents

**Deliverable:** solo play and the fuzz harness.

- [ ] `ai/random_agent.py` — uniform over `legal_intents()`
- [ ] `ai/heuristic_agent.py` — weighted scoring, weights in YAML (so *your variant can retune
      the AI without touching Python*)
- [ ] `hts sim --games 1000` — fuzz for crashes, invariant breaks, non-termination

**Acceptance:** 1000 random games complete, zero exceptions, all terminate under a turn cap.

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

**Phases 0, 1 and 2 complete.** `uv run hts validate data/base` is green; `uv run pytest` runs
136 tests, and the layering check now actually walks `core/` (it no longer skips).

Next up is **Phase 3 — Event Bus & Effect Interpreter**, the critical phase: `core/events.py`,
`bus.py`, `interpreter.py`, `context.py`, `registry.py`, the `effects/` and `conditions/`
catalogues, and `log.py`. The three open design questions in `architecture_notes.md §10` that are
marked "decide before Phase 3" are now due.

Open questions blocking later phases are collected in `rules_reference.md §5`. Only Phase 6 (base
card content) is actually blocked by them.

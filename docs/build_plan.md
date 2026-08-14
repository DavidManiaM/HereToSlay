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

## Phase 2 — Core State Model

**Deliverable:** a `GameState` you can build, mutate through zone primitives, snapshot, and diff.

- [ ] `core/ids.py` — `NewType` ids (`PlayerId`, `CardId`, `ZoneId`)
- [ ] `core/zones.py` — generic `Zone` (ordered/unordered, visibility, capacity)
- [ ] `core/state.py` — `GameState`, `PlayerState`, `CardInstance`
- [ ] `core/rng.py` — `DeterministicRng` (seeded, every call logged)
- [ ] `core/setup.py` — build a game from `RuleSet` + player list + seed
- [ ] `core/view.py` — `GameView` redaction per seat
- [ ] Invariant checker (`rules_engine.md §8`)

**Acceptance:** setup produces the right deck sizes/hands from base data; two setups with the
same seed are identical; `view()` never leaks a hidden card id.
**Not yet:** events, turns.

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

**Phases 0 and 1 complete.** `uv run hts validate data/base` is green; `uv run pytest` runs 53
tests (one skipped: the `core/` layering check, which activates when `core/` exists).

Next up is **Phase 2 — Core State Model**: `core/ids.py`, `zones.py`, `state.py`, `rng.py`,
`setup.py`, `view.py` and the invariant checker.

Open questions blocking later phases are collected in `rules_reference.md §5`. Only Phase 6 (base
card content) is actually blocked by them.

# Rules Engine

How the loop runs, how a card interrupts it, and how the game ends.

---

## 1. The Engine's Public Surface

The entire outside world talks to the engine through four things:

```python
class Engine:
    def start(self) -> Status: ...
    def submit(self, decision: Decision) -> Status: ...
    def view(self, seat: PlayerId) -> GameView: ...
    def legal_intents(self, seat: PlayerId) -> list[Intent]: ...

Status = Awaiting(request: Request) | Quiescent | GameOver(winner)
```

`legal_intents()` is what makes the UI dumb and the AI easy: **the engine computes what is
legal**, the CLI prints it as a numbered menu and pygame highlights the corresponding widgets.
Neither ever re-implements a rule. When your variant changes what's legal, both UIs follow with
zero edits.

For that to hold, legality has to be data. An action declares its `targets:` (a selector plus a
filter, `card_schemas.md §7`) and the engine expands it into one `Intent` per legal combination —
so "which Heroes may I play?" is a YAML question. An action whose target has no candidates is not
offered at all.

---

## 2. The Main Loop

```
                      ┌───────────────┐
  setup ─────────────►│  TURN_START   │  on_enter: reset AP, fire turn.started
                      └───────┬───────┘
                              ▼
                      ┌───────────────┐   AP > 0 and no winner
              ┌──────►│     MAIN      │◄──────────────┐
              │       └───────┬───────┘               │
              │               ▼                       │
              │      Awaiting(ChooseIntent)           │
              │               ▼                       │
              │       validate → pay cost             │
              │               ▼                       │
              │     ┌─────────────────────┐           │
              │     │  RESOLVE ACTION     │───────────┘
              │     │  (effect generator) │  may open reaction windows,
              │     └─────────────────────┘  may suspend for decisions
              │               ▼
              │        check victory
              └───────────────┤ AP == 0
                              ▼
                      ┌───────────────┐
                      │   TURN_END    │  hand limit, cleanup, untap
                      └───────┬───────┘
                              ▼
                      next player / GAME_OVER
```

The transition table lives in `rules.yaml` (`phases:`); `TurnMachine` only walks it. Adding an
"upkeep" phase to your variant is a YAML edit.

**Victory is checked after every event resolution**, not only at end of turn — because a variant
might slay a monster via someone else's card. The check is a loop over `rules.victory` conditions
for every player; the first satisfied wins (ties broken by `rules.tiebreak`).

---

## 3. Anatomy of an Action

Playing a Hero — the path every action follows:

| # | Step | Interruptible? |
|---|---|---|
| 1 | UI submits `Intent(action="play_hero", card=X)` | — |
| 2 | Engine validates against `legal_intents` (re-validated server-side; never trust the UI) | — |
| 3 | `action.declared` event | **PRE: cancellable** |
| 4 | Pay cost (`action_points: 1`) → `action.paid` | pluggable via `@cost` |
| 5 | Card moves `hand → limbo` | — |
| 6 | `card.played` event → **opens `card_played` window** | **Challenge lands here** |
| 7 | If cancelled: `limbo → discard`, AP is **not** refunded, stop | — |
| 8 | Card moves `limbo → party`; `hero.entered_party` | PRE-cancellable |
| 9 | POST triggers fire (other cards react) | queued |
| 10 | Victory check | — |

Steps 3–10 are one generator. A Challenge at step 6 suspends the whole thing mid-flight, runs a
nested contest roll (which itself opens a `roll_modification` window, which may itself be
challenged…), and then resumes or aborts. This falls out of `yield from` composition — there is
no special-case code for "challenging a challenge".

---

## 4. The Roll Pipeline

Every die roll in the game — hero ability, monster attack, challenge — is one object with one
lifecycle. This is why Modifier cards need no per-case logic.

```python
@dataclass
class Roll:
    id: RollId
    kind: str  # "hero_ability" | "monster_attack" | "challenge" | mod-defined
    roller: PlayerId
    source: CardId | None
    dice: str  # "2d6"
    raw: list[int]  # actual die faces (from state.rng)
    modifiers: list[Modifier]  # (source_card, amount, applied_by)
    outcomes: list[Band]  # from the card data
    contested: bool  # one half of a contest_roll; modified with its partner
    band_tag: str | None  # which band ran, once one has

    @property
    def total(self) -> int: ...
```

```
roll.started  ─── PRE ───►  passives inject Modifiers (leaders, items)
      │                      condition: roll_is(kind=..., roller=...)
      ▼
   dice rolled from state.rng  (logged)
      ▼
roll.resolved ─── PRE ───►  the `roll_modification` WINDOW opens here: each
      │     ▲         │      player, in seat order, may play Modifier cards
      │     └─ reopen ┘      (free). Any play re-opens the window, so a
      │                      Modifier can be countered by another Modifier.
      │                      Each one emits `roll.modified`; other cards react.
      ▼
   total read, matching Band selected, `roll.band_tag` set
      ▼
roll.banded   ─── PRE ───►  "this outcome is about to happen, and it is the one
      │                      tagged X". Cancelling it skips the band's effect.
      ▼
   band effect executed
```

The window hangs off `roll.resolved` rather than off a line of Python: `rules.yaml` says
`roll_modification: {on: [roll.resolved, contest.resolved], timing: pre}`. Landing there — after
the dice, before the total is read — is what makes a Modifier change *which outcome happens*.

**Key decisions:**
- Modifiers are *additive integers with a source*, so "ignore Modifiers played by your opponents"
  or "double all Modifiers" is a PRE subscriber on `roll.resolved`, not an engine change.
- Bands are evaluated in declaration order; first match wins. `{min:}`/`{max:}` are inclusive.
  An unmatched roll is a validation error at load time (bands must cover the dice range).
- The dice string is parsed (`NdM+K`), so `3d6` or `1d20` variants work without code changes.

### 4.1 `Band.tag` — what a roll has no opinion about

A roll has no notion of succeeding. A band is a *range*, and a range does not know whether
landing in it is good news. So a card that says "each time you **successfully** roll…" cannot be
written without the rolling card first saying which band counts as success.

`tag:` is that declaration, and `roll.banded` is where it is readable:

```yaml
outcomes:
  - { min: 8, tag: success, effect: {...} }   # the printed threshold
  - { max: 7, tag: failure, effect: {op: noop} }
```

```yaml
triggers:
  - on: roll.banded
    timing: post
    condition: { op: roll_is, kind: hero_ability, tag: success }
    effect: { op: draw, target: $self, count: 1 }
```

The engine attaches no meaning to any particular tag. `success` / `failure` on Heroes and
`slain` / `spared` / `backfire` on Monsters are conventions of the base pack; a variant is free to
tag `critical`, `fumble`, or nothing at all — an untagged band leaves `roll.band_tag` as `None`
and every trigger that asks for a tag simply does not fire.

`roll.banded` is announced *before* the band's effect runs, and cancelling it skips that effect.
That makes "that outcome does not happen to you" a subscriber rather than an engine concept.

### 4.2 Contests — both sides land before either is modified

`contest_roll` (the Challenge card's payload) rolls two sides. Each is a full `Roll`, but each is
rolled `contested`, and the base `roll_modification` window declines to open on a contested side:

```yaml
condition:
  op: not
  of: { op: compare, left: $event.contested, cmp: "==", right: true }
```

Once both sides have landed, `contest.resolved` is emitted with both rolls in its payload, and
the *same* window opens on that instead. Only after it closes are the totals compared and a
branch chosen.

The ordering is the rulebook's — both players roll, then Modifiers are played — and it is what
makes a Modifier on a Challenge a decision rather than a gamble. It also means a Modifier played
there has two rolls to choose between, so `modify_roll` asks which one. That question only ever
appears when it is genuinely ambiguous: an ordinary roll resolves without a prompt.

A variant that prefers side-at-a-time modification deletes the `condition:` above. Nothing in
Python changes.

---

## 5. Reaction Windows

A window is a named, re-entrant polling loop:

```python
def open_window(ctx, window_name, event):
    depth_guard(ctx)
    window = ctx.rules.windows[window_name]
    order = seat_order_from(window.order, event)
    reopen = True
    while reopen:
        reopen = False
        for player in order:
            options = playable_reactions(ctx.state, player, window_name, event)
            if not options:
                continue
            choice = yield ctx.ask_reaction(player, options)  # includes "pass"
            if choice is PASS:
                continue
            yield from ctx.play_reaction(player, choice)
            if window.reopen_on_action:
                reopen = True
                break  # everyone is asked again, from the top of the order
            # otherwise: one pass, one reaction each — carry on down the order
```

Note the `else` branch. `reopen_on_action: false` means **one pass with one reaction per seat**,
not "the window shuts as soon as anybody acts": the seats after the one that acted have not been
asked yet. Getting this wrong is easy — the loop used to `break` unconditionally and set
`acted = False`, which silently disenfranchised every seat below the first responder.

Properties that matter:

- **Deterministic order** — seat order starting left of the active player. Never a dict order,
  never a set.
- **Re-entrancy with a depth cap** (`rules.max_reaction_depth`, default 8) so a pathological mod
  card can't hang the engine.
- **Skipped automatically** when nobody holds a legal reaction — so a 2-player game with no
  Challenge cards in hand costs zero prompts. The CLI and pygame never see a window they can't act in.
- **Windows are data, including *when* they open.** A window declares `on:` (one event name, or a
  list of them) and `timing:`, and the **bus** opens it during that event's dispatch — which is
  exactly why a Challenge's `cancel_event` reaches the `card.played` it is challenging. An optional
  `condition:` gates it; that is how `challengeable: false` keeps the window shut without the bus
  ever learning the word. So a variant can add a `damage_prevention` window on its own event, and a
  card that reacts to it, with no engine edit.
- **One window may open on several events.** `roll_modification` lists
  `on: [roll.resolved, contest.resolved]`, so a lone roll and a settled Challenge both bring the
  same window up. Modifier cards name the window once and never learn which event fetched them —
  the alternative, two windows under two names, would have forced every Modifier in the game to
  list both.
- **Reaction cards are played from hand**, moved through `limbo`, announced as a `card.played`
  like any other card, and spent to the discard once resolved. A variant whose Challenges are
  themselves challengeable sets `challengeable: true` in the card's `reaction` block and gets
  challenge-a-challenge for free — the depth cap bounds it. The base game does **not** do this:
  the rulebook says a Challenge cannot be challenged. `tests/fixtures/play` ships an `Open Veto`
  that does, which is how the mechanism is proved without shipping a wrong rule.

### 5.1 What "under load" was actually tested

Phase 7's claim is not that a window works, but that windows *nest*. `tests/test_reactions.py`
covers: a reaction answered by another reaction; a three-deep chain that resolves and strands
nothing; the same chain twice, asserting the same seats were asked in the same order; the depth
cap ending a chain that could otherwise run further; `reopen_on_action` in both settings; a
Modifier deciding a Challenge from either side; two Modifiers stacking on one roll; a Party
Leader that reacts to Modifiers *without* recursing on its own; and sixty randomised three-player
games on the real card set whose seats react roughly 60% of the time, asserting only what a chain
cannot violate quietly — no card left in `limbo`, no broken invariant, and a game that ends.

The randomised run is deliberately not the Phase 8 agent. It knows nothing about any card; its
job is to reach depths and orderings a scripted test would never think to write down.

---

## 6. Decisions, Requests, and the Log

Every suspension is one `Request` and one `Decision`:

| Request | Payload | Decision |
|---|---|---|
| `ChooseIntent` | legal intents for the active player | `IntentChosen(intent)` |
| `ChooseCards` | candidates, min, max, from-zone, prompt | `CardsChosen(ids)` |
| `ChoosePlayer` | candidates | `PlayerChosen(id)` |
| `ChooseOption` | labelled branches | `OptionChosen(key)` |
| `Confirm` | prompt | `Confirmed(bool)` |
| `ReactionPrompt` | playable reactions + pass | `ReactionChosen(card \| PASS)` |

Rules:

1. Every request carries `requester: PlayerId` — the presenter knows whose input to solicit
   (and in a hot-seat CLI, when to clear the screen).
2. Every request carries the *legal* option set. `submit()` **re-validates**; an illegal decision
   raises rather than corrupting state. The UI is never trusted.
3. Every accepted decision is appended to `DecisionLog`. `(content_hash, seed, log)` reproduces
   the game exactly → replay, network, tests, bug reports.
4. **Save/load is only legal at `Quiescent`** (between actions), because a suspended generator
   stack isn't serialisable. Mid-decision saves are rejected with a clear error. Accepted
   trade-off — see `architecture_notes.md §4`.

---

## 7. Where Each Rule Actually Lives

The test of the design: for each rule, which file changes if the variant changes it?

| Rule | Where it lives |
|---|---|
| "3 action points per turn" | `rules.yaml → turn.action_points_per_turn` |
| "attacking costs 2" | `rules.yaml → actions[attack_monster].cost` |
| "you may only play Heroes from your hand" | `rules.yaml → actions[play_hero].targets` |
| "the Monster row refills after each action" | `rules.yaml → turn.after_action` |
| "a Challenge may interrupt a play" | `rules.yaml → windows[card_played].on` |
| "slay 3 monsters to win" | `rules.yaml → victory[]` |
| "6 classes exist" | `rules.yaml → classes` |
| "Challenge beats Hero on a higher roll" | `cards/challenges.yaml` |
| "+2 Modifier adds 2" | `cards/modifiers.yaml` |
| "a leader gives +1 to hero rolls" | `cards/leaders.yaml → triggers` |
| "a monster needs 2 Fighters" | `cards/monsters.yaml → requirement` |
| "reaction windows poll left-of-active" | `rules.yaml → windows` |
| *how* a roll pipeline works | `core/rolls.py` ← **engine** |
| *how* windows re-enter | `core/interpreter.py` ← **engine** |
| *what `steal_hero` means* | `core/effects/party.py` ← **engine (or a pack plugin)** |

Everything above the line is your mod's surface. If you find yourself editing below the line for
a *content* change, the design has failed and we should add a registry seam instead.

---

## 8. Error Handling & Invariants

Checked continuously in debug builds (`HTS_STRICT=1`), sampled in release:

- Every `CardInstance` is in exactly one zone.
- Zone capacities respected (`monster_row ≤ 3`).
- `action_points >= 0`.
- No event nests deeper than `max_reaction_depth`.
- Total card count is conserved (nothing vanishes) except by an explicit `remove_from_game`.
- The RNG is only ever advanced inside a logged call.

Violations raise `EngineInvariantError` with the last 20 events — a mod's bad card should point
at itself, not surface as a weird UI bug three turns later.

---

## 9. Testing Strategy

| Layer | Test |
|---|---|
| Schema | every base pack file loads; malformed fixtures fail with the right path |
| Effects | each `op` unit-tested against a hand-built `GameState` |
| Rolls | seeded RNG → deterministic totals; modifier stacking; band selection |
| Windows | challenge-a-challenge; depth cap; skip when no legal reaction |
| Cards | one golden replay per card: seed + decision log → asserted final state |
| Rules | victory conditions, action costs, turn advance |
| Layering | import-graph test: `core/` imports nothing from `ui/`, `pygame`, `random` |
| Fuzz | random agent plays 1000 games; assert no exception, no invariant violation, all terminate |

The fuzz test is the real safety net for a modding-heavy project: it catches "this new card can
deadlock the reaction window" before you do.

# Base Game Rules Reference

The rules of *Here to Slay* (Unstable Games / TeeTurtle) as implemented in `data/base/`.

> **Status: verified against the official rulebook during Phase 6.** This file was written from
> recollection and was explicitly flagged as unreliable; it has now been checked line by line
> against the published rulebook PDF, and the four places it was **wrong** are called out in §7.
> Card data was cross-checked against the Unstable Games wiki, the Here to Slay Fandom wiki and a
> third-party implementation, which agreed with each other.
>
> What remains unverified is a short list of individual card numbers, recorded in §8. Both card
> wikis block automated fetching, so those came from search summaries rather than a primary
> source. Every one of them is a one-line YAML edit — correcting one costs minutes, not a
> refactor.

---

## 1. Components

- **Party Leader** — one per player, each of a class, each with a passive ability.
- **Main deck** — Heroes, Items, Magic, Modifiers, Challenges.
- **Monster deck** — 3 face-up in the Monster Row at all times.

## 2. Classes (6)

`Bard · Fighter · Guardian · Ranger · Thief · Wizard`

## 3. Setup

1. Each player takes a Party Leader (random deal, or draft).
2. Shuffle the main deck; deal **5** cards each.
3. Reveal **3** Monsters into the Monster Row.

## 4. Turn Structure

**3 action points per turn**, spent in any combination, repeatable:

| Action                                     | Cost  |
| ------------------------------------------ | ----- |
| Draw a card                                | 1     |
| Play a Hero / Item / Magic card from hand  | 1     |
| Use a Hero's ability (roll)                | 1     |
| **Attack a Monster** (roll)                | **2** |
| **Discard your whole hand and DRAW five**  | **3** |

Playing a Hero lets you **roll its effect immediately, at no extra action point**. Once it is in
your party you may spend an action point to use it, **once per turn** — and a failed roll still
spends the point.

**Free, out-of-turn, not actions:**
- **Modifier** — played into any roll, +/- to the total. Any number, by anyone, on any roll.
  Base modifiers offer two options ("+1 or -3"); you declare which on play.
- **Challenge** — played when someone plays a Hero / Item / Magic card. Both roll `2d6`;
  **the challenger wins ties** ("equal to or higher"). Challenger wins → the card is discarded
  and the action point is *not* refunded. **Each card may be challenged only once.**
  A Hero's *ability* is never challengeable — only *playing* a card is.

**Rolls** are `2d6` plus modifiers, compared against the card's threshold.

**Monsters** have a party *requirement* to attack, an outcome band for the roll
(slay / nothing / penalty), and a permanent skill gained on slay. A slain Monster joins your
party and cannot be stolen, destroyed or attacked. A failed attack leaves the Monster in the row
and applies the penalty immediately.

**Requirement grammar.** A class symbol may be satisfied by a Hero *or* your Party Leader. A
generic symbol means "a Hero of any class" and the Party Leader does **not** count. One Hero
cannot pay for two symbols.

## 5. Victory

**Either** condition wins immediately:
- Slay **3** Monsters, **or**
- Have a Hero of **each of the 6 classes** in your party.

---

## 6. Open Questions — all answered

Kept as a record of what was assumed versus what the rulebook actually says.

| # | Question | Answer |
|---|---|---|
| 1 | Rulebook available? | **Yes** — verified against the official PDF in Phase 6. |
| 2 | Expansions? | **No** — base game only. `classes` is a list in `rules.yaml`, so adding one later is a data edit. |
| 3 | Player count? | **2–6** (`setup.min_players` / `max_players`). |
| 4 | Challenge ties? | **The challenger wins.** The earlier answer here was wrong — see §7. |
| 5 | Hero abilities once per turn? | **Yes**, and a failed roll still spends the action point. A Hero played this turn may roll immediately, free. |
| 6 | Failed monster attack? | **Monster stays in the row; penalty applies immediately.** Confirmed. |
| 7 | Hand limit? | **None.** `turn.hand_limit: null`. |
| 8 | Can a Hero's ability be Challenged? | **No** — only *playing* a Hero, Item or Magic card is challengeable. Confirmed. |

---

## 7. Where this file was wrong

Four corrections the rulebook forced during Phase 6. Each had already been built on, so each cost
a change beyond the YAML:

1. **Challenge ties go to the challenger, not the defender.** The rulebook: "If your roll is
   equal to or higher than the other player's roll, the card being challenged is moved to the
   discard pile." `base.challenge.challenge` therefore cancels on `on_tie` as well as
   `on_a_wins`. The old note in `tests/fixtures/play/cards/cards.yaml` said the opposite.
2. **The 3-action-point move is "DISCARD your whole hand and DRAW five"**, not "discard your
   hand, draw that many plus one". It is a real base action, so `discard_and_draw` is now
   `enabled: true` — it had shipped switched off.
3. **Playing a Hero lets you roll its effect immediately, for free.**
4. **Each card may be Challenged only once**, which the Challenge card enforces with a
   `challenged` flag on the card being played.

Two further rulebook details drove *engine* changes rather than data:

* A **Party Leader satisfies a class requirement** and counts toward the six-class win, while
  never counting as a Hero for "N Heroes of any class". `core/conditions/board.py` now reads the
  leader zone through a `CLASS_BEARING_ZONES` table.
* **A Leader's skill may cost an action point** (The Shadow Claw), which a passive subscription
  cannot express — hence `PartyLeaderDef.ability` and the `use_leader_ability` action.

---

## 8. Unverified card values

Both card wikis block automated fetching (HTTP 403/402), so these came from search summaries
rather than a primary source. Everything else in `data/base/cards/` was confirmed by at least two
independent sources. **Each of these is a one-line YAML edit.**

**Roll thresholds only** (the effect text is confirmed):
`Bullseye` 7+ · `Hook` 6+ · `Lookie Rookie` 5+ · `Quick Draw` 8+ · `Meowzio` 10+ ·
`Plundering Puma` 6+ · `Kit Napper` 9+ · `Fluffy` 10+ · `Whiskers` 11+

**Whole card unconfirmed** (name and class are real; the effect is a placeholder in that class's
idiom): `Wily Red` · `Silent Shadow` · `Buttons` · `Snowball`

**Deck split.** 48 Heroes, 12 Items and 16 Magic follow from confirmed lists. The remaining 39 is
split 32 Modifiers / 7 Challenges to hit the printed 115-card main deck; the true split is
unconfirmed.

**Deliberate deviations**, where the printed card needs something the engine has no concept of:

* `Arctic Aries`, `Particularly Rusty Coin`, `Suspiciously Shiny Coin` say "**successfully** roll".
  A roll's bands are declarative ranges with no notion of success, so there is no event meaning
  "the good band ran". The first fires on every Hero-ability roll; the coins use a total of ≤6 /
  ≥7 as a proxy. Closing this properly wants a `tag:` on `Band` — an engine change, and its own
  piece of work.
* ~~`card_schemas.md` advertises `{expr: "$self.hand_size"}`, which the engine does not
  resolve.~~ **Fixed** — the doc example now uses the `hand_size` condition. `$ref.<field>` paths
  reach `PlayerState` attributes only; zone sizes go through a condition.

---

## 9. Legal / Content Note

Game *rules and mechanics* aren't copyrightable, but *card text and art* are. What shipped uses
the real card **names and mechanics** with **condensed, functional wording** rather than
transcribed flavour text, and no art at all — `art:` is unset on every card, and Phase 9 will
generate placeholders procedurally. Since this is a personal modding base, swapping in verbatim
text is your call; the schema handles either identically.

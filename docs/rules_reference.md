# Base Game Rules Reference

My working model of *Here to Slay* (Unstable Games / TeeTurtle), written from recollection.

> **⚠️ This file is the one place in `docs/` that is NOT authoritative.** Everything else
> describes code I control; this describes a physical game I'm recalling. Treat every number
> below as a hypothesis to confirm. Phase 6 (base card content) is blocked on §5.
>
> The good news: because all of this lives in `data/base/`, correcting any of it is a YAML edit,
> not a code change. Getting it wrong costs minutes, not a refactor.

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

**3 action points per turn**, spent in any combination:

| Action                      | Cost  |
| --------------------------- | ----- |
| Draw a card                 | 1     |
| Play a Hero (to your party) | 1     |
| Use a Hero's ability (roll) | 1     |
| Equip an Item to a Hero     | 1     |
| Cast a Magic card           | 1     |
| **Attack a Monster** (roll) | **2** |

**Free, out-of-turn, not actions:**
- **Modifier** — played into any roll, +/- to the total. Stacks; can be played by anyone.
- **Challenge** — played when someone plays a Hero / Item / Magic card. Both players roll;
  higher total wins. Challenger wins → the card is discarded and the action is wasted.

**Rolls** are `2d6` plus modifiers, compared against the card's threshold.

**Monsters** have a party *requirement* to attack (e.g. "2 Fighters"), an outcome band for the
roll (slay / nothing / penalty), and a reward on slay.

## 5. Victory

**Either** condition wins immediately:
- Slay **3** Monsters, **or**
- Have a Hero of **each of the 6 classes** in your party.

---

## 5. Open Questions — I need your answers before Phase 6

These don't block the architecture (Phases 1–5), only the base card data.

1. **Do you own the physical game / have the rulebook?** If you can photograph or transcribe the
   card list, I'll encode it exactly. Otherwise I'll build a *mechanically faithful but
   originally-worded* card set — which may actually suit you better, since you're modding anyway
   and it avoids reproducing copyrighted card text verbatim.
2. **Which expansions, if any?** (*Warriors & Druids*, *Berserkers & Necromancers*, …) They add
   classes — and since `classes` is just a list in `rules.yaml`, adding them later is trivial.
   Programmer answer: no expansion
3. **Player count target?** Base is 2–6. Affects setup rules and AI defaults.
4. **Ties on a Challenge roll** — I've assumed the *attacker* wins ties (the challenge fails).
   Confirmed
5. **Hero abilities: once per turn, or repeatable?** I've assumed a Hero can be used once per
   turn (`once_per_turn: true`), and that Heroes played this turn can be used immediately.
6. **Failed monster attack** — does the monster stay in the row (yes, I assume) and does the
   attacker suffer the penalty band immediately?
   Answer: yes, the monster stays in the row; the penalty is applied immediately.
7. **Hand limit at end of turn?** I've assumed none.
8. **Can a Hero's ability be Challenged?** I've assumed no — only *playing* a card is
   challengeable, not *using* one. Confirm?

---

## 6. Legal / Content Note

Game *rules and mechanics* aren't copyrightable, but *card text and art* are. Default plan:
mechanically faithful cards with original wording and procedurally generated placeholder art,
kept in `data/base/`. If this is purely for your own use and you'd rather transcribe the real
cards, that's your call — the schema handles either identically.

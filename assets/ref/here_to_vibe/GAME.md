# Here to Vibe — game (same as Here to Slay)

This reskin is **Here to Vibe (Code)**. The engine still *is* Here to Slay.
Romanian names and jokes sit on top.

Official rules: `docs/rules_reference.md` and `data/base/`. Table language:
`GLOSSARY.md`. Roster: `ROSTER.md`. Locked changes: `QUESTIONS.md`.

**Win:** same two conditions, flavour = **primești legitimația lui Andrei**.
Trophy art: `images/by_type/misc/legitimatia_andrei.png` (transparent PNG).
**Monsters:** you **befriend** besties instead of slaying them. Same rolls.

### Locked mechanical deltas

- **Random.org**: +2 on every `np.random()`, not only Șia all in.
- **Ce-Zar**: replaces Spooky; cap one player at 6 (one die) until **that player's turn is over**.
- **Items**: 12 unique cheats/hacks, one copy each (not 6×2).
- **Măști**: `/0` Magician … `/30` Cântăreț; **6 extra** main-deck cards; equipped persoană counts as that class.
- **Cafelutza puternicoasă**: at turn start, before prompts, the equipped persoană's ability fires free (no roll).
- **Freeze**: Aerul condiționat vs Rota-lorifer (new).
- Challenge card name: **Șia all in**.

---

## Goal

Win immediately by **either**:

1. Slaying **3 Monsters**, or
2. Having a Hero of **each of the 6 classes** in your party.

A Party Leader counts as its class for the six-class win, but it is **not** a
Hero (it cannot be stolen, destroyed, or used to fill a "Hero of any class"
monster requirement).

---

## Classes

| Class | Colour (UI) | Flavour for art |
|-------|-------------|-----------------|
| Fighter | red `(214, 66, 74)` | strength, weapons, confrontation |
| Bard | magenta `(196, 84, 200)` | music, charm, social / party energy |
| Guardian | blue `(72, 126, 226)` | protection, shields, calm resolve |
| Ranger | green `(78, 182, 96)` | hunting, bows, tracking, outdoors |
| Thief | gold `(226, 196, 66)` | stealth, theft, tricks |
| Wizard | cyan `(66, 196, 220)` | magic, spells, weirdness |

---

## Setup

1. Each player gets one **Party Leader** (one class each).
2. Deal **5** cards from the main deck.
3. Reveal **3 Monsters** in the Monster Row.

Players: **2–6**. No hand limit.

---

## Turn (3 action points)

Spend in any mix, repeatable:

| Action | Cost |
|--------|------|
| Draw a card | 1 |
| Play a Hero / Item / Magic from hand | 1 |
| Use a Hero's ability (roll 2d6) | 1 |
| Attack a Monster (roll 2d6) | 2 |
| Discard whole hand, draw five | 3 |

Playing a Hero lets you **roll its effect immediately for free**. After that,
using it costs 1 AP, once per turn. A failed roll still spends the point.

Rolls are **2d6** plus modifiers vs a printed threshold (Heroes) or vs bands
(Monsters: slay / nothing / penalty).

---

## Interrupt cards (not actions)

- **Modifier** — anyone may play any number into any roll (`+1 or -3`, etc.).
- **Challenge** — when someone plays a Hero, Item, or Magic. Both roll 2d6;
  challenger wins ties. Winner of the challenge discards the played card; the
  action point is not refunded. Each card may be challenged only once.
  Using a Hero *ability* cannot be challenged — only *playing* a card can.

---

## Card kinds (what we will illustrate)

### Party Leader (6)

Permanent, one per player. Five are passives. **The Shadow Claw** (Thief)
spends 1 AP to pull a random card from an opponent's hand.

### Hero (48)

Goes into your party. Has a class and a roll-to-activate ability. One Item
may be equipped to a Hero.

### Monster (15)

Attack costs 2 AP and needs a party requirement (class symbols and/or "any
Hero"). Slay it and it joins your party with a permanent skill. Failed attack
stays in the row and applies the penalty.

### Item (6 kinds, 2 copies each)

Equip to a Hero (blessings on yours, curses on an opponent's). Travels with
the Hero if stolen/destroyed unless the Item says otherwise.

### Magic (8 kinds, 2 copies each)

Play, resolve, discard.

### Modifier / Challenge

Played out of turn. Usually abstract / object art, not portraits.

---

## Vocabulary used on cards (for posing)

- **DRAW** — take from the deck
- **DISCARD** — from hand to discard
- **DESTROY** — a Hero leaves a party to discard
- **SACRIFICE** — you destroy one of your own Heroes
- **STEAL** — take a chosen Hero from another party
- **Pull** — take a **random** card from a hidden hand
- **SLAY** — succeed on a Monster attack

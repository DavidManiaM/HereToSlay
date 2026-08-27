# Here to Vibe — reference assets

Reskin of *Here to Slay* as **Here to Vibe (Code)**. Same engine IDs as
`data/base/` except the locked changes in `QUESTIONS.md`. The table language
is **Romanian**. New art replaces the animal-hero illustrations with real
people (and objects) in a playful anime-ish style that still looks like the
person.

**Source pack to mirror:** `assets/ref/here_to_slay/`

**This pack:** `assets/ref/here_to_vibe/`

---

## Start here

| File | Purpose |
|------|---------|
| [`GAME.md`](GAME.md) | How the game works (mechanics we must not break) |
| [`GLOSSARY.md`](GLOSSARY.md) | Romanian table language (prompt, barfă, bestie, …) |
| [`ROSTER.md`](ROSTER.md) | Original card → Zaimans name + ability |
| [`PEOPLE.md`](PEOPLE.md) | Recurring faces (same person, many cards) |
| [`QUESTIONS.md`](QUESTIONS.md) | Mapping holes to confirm with Matei |
| [`STYLE.md`](STYLE.md) | Art style, framing, class palettes |
| [`WORKFLOW.md`](WORKFLOW.md) | How each image request is handled |
| [`CARDS.md`](CARDS.md) | Original English base roster (engine text) |
| [`meta/catalog.json`](meta/catalog.json) | Per-card index (same shape as the original pack) |

---

## Layout

```
assets/ref/here_to_vibe/
  README.md
  GAME.md
  STYLE.md
  WORKFLOW.md
  ROSTER.md
  CARDS.md
  images/
    cards/                  ← named art (slug or spoken title)
    by_type/                ← copy organized by leaders|heroes|monsters|items|…
      leaders/
      heroes/
      monsters/
      items/
      magic/
      modifiers/
      challenges/
      unknown/
      misc/                 ← UI extras (winning trophy, not a playable card)
    sources/                ← photos the user provides as identity reference
  text/                     ← per-card notes for the vibe version
  meta/                     ← catalog.json and coverage
```

Filenames stay the **original card slug** (`bear_claw.png`, `the_shadow_claw.png`)
so `src/here_to_slay/ui/pygame/art.py` can find them the same way it finds the
official-pack files. If the GUI is pointed at this pack, a file named after
the YAML id suffix is enough.

---

## What is in scope

- Șefi de grup (6)
- Persoane (48, 8 per class)
- Besties (15)
- Cheats + hacks (12 unique, one copy each)
- Măști `/0`–`/30` (6, one class each)
- Scripturi (8 kinds)
- Download/upload speed + **Șia all in**

Generate **illustration only** (character / object portrait), not a full
typeset card with name, class line, and ability text. The PyGame client crops
the image to fill the card face and draws the frame and text itself.

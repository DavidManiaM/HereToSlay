# Here to Vibe — image workflow

One card at a time unless the user batches several.

---

## What the user provides per card

1. A **photo** (person or object) — identity reference.
2. The **role**: Zaimans name + original slug + class (e.g. Sebi Șoimul =
   Bear Claw, persoană, Luptător). Look it up in `ROSTER.md`.
3. **What to add** (chitară, gheare, pelerină, …).
4. Optional extra notes (pose, expression, outfit, Romanian in-joke).

## What I do

1. Look up the card in [`CARDS.md`](CARDS.md) so the pose matches the ability.
2. Confirm / update the row in [`ROSTER.md`](ROSTER.md).
3. Save the user's photo under `images/sources/<slug>/` (original filename
   plus a short note if needed).
4. Generate a new illustration:
   - likeness from the photo
   - style from [`STYLE.md`](STYLE.md)
   - class colour + props from the request
5. Save in the **same pattern as** `assets/ref/here_to_slay`:

   ```
   images/cards/<Original_Title_Here_to_Vibe>.png
   images/by_type/<kind_folder>/<slug>.png
   ```

   Examples:

   | Original pack | Vibe pack |
   |---------------|-----------|
   | `images/by_type/heroes/bear_claw.png` | `images/by_type/heroes/bear_claw.png` |
   | `images/by_type/leaders/the_shadow_claw.png` | `images/by_type/leaders/the_shadow_claw.png` |
   | `images/cards/Bear_Claw_Here_to_Slay.png` | `images/cards/Bear_Claw_Here_to_Vibe.png` |

6. Write `text/<slug>.md` with who is pictured, props, and file paths.
7. Append / update `meta/catalog.json`.

`slug` is the original card id suffix: `base.hero.bear_claw` → `bear_claw`.
Leaders keep the `the_` prefix in `by_type/leaders/` (`the_shadow_claw.png`).

## Kind folders

| Card kind | Folder |
|-----------|--------|
| party_leader | `images/by_type/leaders/` |
| hero | `images/by_type/heroes/` |
| monster | `images/by_type/monsters/` |
| item | `images/by_type/items/` |
| magic | `images/by_type/magic/` |
| modifier | `images/by_type/modifiers/` |
| challenge | `images/by_type/challenges/` |

## After a batch

If the user wants these in the running GUI, the client already prefers
`here_to_vibe` over `here_to_slay`. Restart the GUI after adding new files.

## Memory

Every new person, nickname, and prop goes into `ROSTER.md` so later cards of
the same person stay consistent.

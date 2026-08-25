# PyGame UI Guide

The graphical client for *Here to Slay*. It sits in `src/here_to_slay/ui/pygame/`
and talks to the engine the same way the terminal client and the AI do: it reads
a redacted `GameView` and answers with `Decision` objects. It never mutates
`GameState`.

Launch it with:

```bash
uv sync
uv run hts gui data/base --players 4 --ai 3
```

That deals a four-seat table, puts you in seat 1, and lets the heuristic agent
play the other three. Up to six players are supported.

---

## 1. How to run it

```bash
# Two humans, hot-seat (pass the mouse; a privacy screen sits between turns)
uv run hts gui data/base

# Solo against five AIs — the full six-player table
uv run hts gui data/base --players 6 --ai 5 --names You Rivka Jonah Mei Ash Kai

# Named seats, reproducible deal, bigger window
uv run hts gui data/base --names Alice Bob Cara --seed dragons --width 1920 --height 1080

# Fullscreen, spectator mode (every hand visible — demos and debugging only)
uv run hts gui data/base --players 3 --ai 2 --fullscreen --reveal-all

# Mute the procedural cues
uv run hts gui data/base --no-sound
```

Useful flags:

| Flag | What it does |
|---|---|
| `--players N` | 2–6 seats (ignored if `--names` is given) |
| `--names …` | Seat names, in turn order |
| `--ai N` | Last *N* seats are played by the heuristic agent |
| `--seed S` | Reproducible deal |
| `--width` / `--height` | Window size (default 1920×1080; clamped to desktop; minimum 1024×640) |
| `--ui-scale` | Chrome scale (default 0.85 — smaller HUD, bigger board) |
| `--fullscreen` | Start fullscreen (`F11` toggles) |
| `--reveal-all` | Spectator: show every hand |
| `--no-sound` | Start muted (`M` toggles) |
| `--max-turns N` | Stop after N turns (useful for demos) |

The same pack the CLI uses (`data/base`, or any variant directory) is what the
GUI loads. A card added to YAML this afternoon is on the table this afternoon.

---

## 2. The board — camera views

The table is framed by **cameras**, not frosted panels:

1. **Tu** — angled on your deployed persoane + hand (răspunsuri AI)
2. **Each opponent** — tight on their deployed persoane (abilities readable)
3. **Besties** — centre row + decks

Cycle with **Q / E** (or arrow keys), click the camera strip, or **click an enemy pile**.
The **action bar** (bottom centre) shows every legal action with its key and AP cost.
The **turn chip** (top left) shows whose go it is and remaining AP. No full-width top bar.
Thin HUD only: prompts, dice readout, actions.

Romanian tech lexicon (`ui/lexicon.py`): persoane, cheats, **hacks** (cursed
items on opponents), provocări, scripts, besties, șeful grupului,
download/upload speed, etc. Engine ids stay English.

### Top bar

Thin status: title, turn/phase, seat pips, info / log / menu.

### Centre

Besties row — requirement chips and **POȚI ÎMPRIETENI** when legal. Decks:
inbox / trash / besties.

### Seats

Opponents are wedges around the oval (not a right-hand list). Hover expands
their persoane.

### Your strip

Șeful grupului + persoane above; hand of răspunsuri AI along the bottom.

**i** / **Log** / **Menu** still open rules, journal, and pause. Hover a seat
wedge to expand persoane; right-click pins a card inspector.

### np.random

The roll control is a monospace **`np.random("")`** button. When a 2d6 total
resolves, it becomes **`np.random("N")`** with N from 2–12. Enabled only when
the engine is waiting for a roll (`R`).

---

## 3. Playing with the mouse (and keys)

The engine computes what is legal (`legal_intents()`). The board only
highlights those widgets. You cannot play an illegal card by clicking it.

| Input | Effect |
|---|---|
| Left-click a highlighted card | Play it / choose it / attack it |
| Left-click a focused card with two actions | Scope the action menu to that card |
| Right-click any face-up card | Pin the inspector |
| Hover any card | Enlarge + show full wording |
| Hover an opponent strip | Grow their deployed cards |
| `1`–`9` | Press the matching action-menu row |
| `Enter` | Confirm a selection / accept a prompt |
| `Space` | Pass a reaction / decline a confirm |
| `Tab` | Cycle card-choice candidates |
| `R` | `np.random` when a roll is legal, or replay last total |
| `I` / `F1` | Rules |
| `L` | Log |
| `Esc` | Menu (or close the top overlay) |
| `M` | Mute / unmute |
| `F3` | FPS readout |
| `F4` | Layout debug overlay |
| `F5` | New game, same seats |
| `F11` | Fullscreen |
| `Ctrl+Q` | Quit |
| **`Ctrl+Shift+D`** | **Developer console** |

Hot-seat: when control passes between two humans a "pass the device" screen
blocks the next hand until someone hits Enter. Passing to an AI never
interrupts.

---

## 4. Developer console — `Ctrl+Shift+D`

Four tabs. One honest constraint: **the console cannot mutate a live game.**
`ui/` may read a `GameView` and submit `Decision`s; reaching into `GameState`
from a debug panel would put the project's one write-path at the mercy of a
button. So each request is routed to whoever is allowed to do it.

| Tab | What the buttons do |
|---|---|
| **Cards** | Browse every definition. **Spawn** flies a card into a sandbox tray on the board (presentation only — labelled "DEV SANDBOX · NOT IN PLAY"). **Inspect** opens the full-size inspector. Filter by kind / name. |
| **FX** | Fire every animation and every sound cue. Card flight, dice tumble, slay burst, challenge, ember rain, rune pulse, confetti, screen shake, handover screen, game-over screen, … |
| **Game** | Start a new deal with 2–6 players and 0–5 AI seats. Pause / step the engine between decisions. Let the agent answer the open question. Toggle reveal-all, animations, sound, autoplay. |
| **Debug** | Live stats (FPS, cached surfaces, art hit-rate, content hash, seat in focus). Layout inspector. |

Spawn-any-card is *cosmetic*. Adding a real card to a live game would skip
the event bus, the log and the invariants — the console refuses to pretend
otherwise. "Any number of players" starts a **new** game through the window,
which owns the engine and is allowed to build one.

---

## 5. Visual language

A light-blue arena with frosted white panels, black ink, and a cyan accent for
"look here next". Class colours stay on cards (same as the CLI), so a Bard is
still magenta. The board keeps every play-critical signal: AP, requirements,
attackability, thresholds, opponent stats, phase, and prompts — quiet chrome,
not a stripped-down party game UI.

The table stays alive without clutter:

- Soft sky-blue felt and a pale oval mat under the centre play.
- Sparse white/cyan motes (no constellation veil or corner torches).
- Hovered cards pick up a foil sheen (ModernGL when available, CPU fallback).
- Opponent strips ease open instead of snapping.
- Legal targets get a cyan ring; dimmed cards are visible but not selectable.

Animations are *cosmetic*. They are driven by a diff of two `GameView`s
(`tracker.py`) after the engine has already moved. If a frame drops, the
board still shows the truth, because the truth is drawn from `engine.view()`.

Card art comes from `assets/ref/here_to_slay/` when a scan exists (Leaders,
many Heroes, several Monsters and Items). Missing art is a generated
**sigil** — a coloured field plus a geometric emblem seeded from the card
id — so nothing on screen ever says "missing". `art.py` is the resolver.

Sound is synthesised at runtime (`sound.py`). There are no audio files.
Accent materials live in `materials.py` (foil + emissive); the dev console can
force the CPU path with **CPU materials**.

---

## 6. Architecture (so a variant still renders)

```
pygame frame loop  ──reads──►  engine.view(seat)     (GameView, redacted)
       │
       │  click / key
       ▼
PygamePresenter.submit_decision(Decision)
       │
       ▼
engine thread  ◄── DecisionSource.answer() blocks here
```

The engine is a generator. `engine.run()` blocks on every question, so it
lives on a worker thread; the window stays at 60 fps. `PygamePresenter` is
the airlock (`docs/architecture_notes.md` §8).

Layering rules the UI must not break (`tests/test_layering.py` asserts them):

- `ui/` never mutates `GameState`.
- `core/` never imports `pygame`.
- No card is drawn by bespoke code. A face is composed from its `CardDef`
  (art, name, class colour, text, roll threshold, monster requirement). A
  mod's new card renders the moment its YAML and optional PNG exist.

Modules, bottom up:

| Module | Job |
|---|---|
| `theme` | Palette, fonts, easing, glass / glow / shadow |
| `icons` | Vector glyphs — no image files |
| `art` | Find a scan, or invent a sigil |
| `atmosphere` | Dark felt table, rim bevel, placemats, cached motes |
| `keybinds` | Default action hotkeys (overridable per `ActionDef.hotkey`) |
| `card_renderer` | Cached card faces, level-of-detail |
| `animations` | Flights, dice, bursts, banners, confetti |
| `widgets` | Buttons, sprites, fans, toasts, fields |
| `layout` | Every named rect, rebuilt on resize |
| `panels` | The nine board regions |
| `tracker` | Diff two views into "what just happened" |
| `sound` | Procedural cues |
| `overlays` | Rules, inspector, log, menu, handover, game over |
| `devconsole` | `Ctrl+Shift+D` |
| `scenes` | Wires the above to the open `Request` |
| `presenter` | Engine-thread / GUI-thread bridge |
| `app` | Window, clock, restart |

The rules overlay is generated from the loaded `RuleSet`, not typed out.
Action-point costs, the class list, player bounds and the victory conditions
are read from content, so a variant that makes attacking cost three points
gets a rules screen that says three.

---

## 7. Tests

```bash
uv run pytest tests/test_pygame_ui.py
```

The suite runs headless against SDL's dummy video and audio drivers: real
surfaces, real layout arithmetic, real event dispatch. It covers 2-, 4- and
6-player tables, every window size the layout supports, every overlay, the
presenter contract (AI seats, pause, stale-answer rejection), and a whole
game played end-to-end with synthesised mouse clicks.

---

## 8. Adding something new

- **A new card** — add YAML (and a PNG under `assets/ref/here_to_slay/` if
  you have one). It renders. No UI edit.
- **A new action** — declare it in `rules.yaml`. The action menu lists it
  with a neutral chip; add a row to `ACTION_STYLE` in `scenes.py` if you
  want a specific icon/colour.
- **A new animation** — subclass `Animation`, add a `_fx_…` helper and a
  `_DEV_FX` entry in `scenes.py`. It appears on the FX tab automatically.
- **A new panel** — give it a rect in `layout.py` and a class in
  `panels.py`. The scene owns *when* it draws and *what a click means*.

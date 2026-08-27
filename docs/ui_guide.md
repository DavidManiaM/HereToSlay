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

# Resume the game you saved (F2 in game, or 's' at any prompt in `hts play`)
uv run hts gui data/base --load 20260827_1153_AB_t4

# Watch a saved game or a decision log play out on the real board
uv run hts gui data/base --watch hts_logs/20260827_120000_dragons.json
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
| `--load SAVE` | Resume a saved game (a path, or a name in the save folder) |
| `--watch LOG` | Replay viewer: step through a save or a decision log |
| `--save-dir DIR` | Where saves are written and looked for (default `hts_saves/`) |

`--width`, `--height`, `--ui-scale` and `--fullscreen` default to whatever the
settings screen last remembered. A flag typed today wins over a file written
last week, which is why they have no numeric default of their own.

The same pack the CLI uses (`data/base`, or any variant directory) is what the
GUI loads. A card added to YAML this afternoon is on the table this afternoon.

### Running a variant

```bash
uv run hts gui data/variants/overclock --players 4 --ai 3
```

Any pack the CLI loads, the GUI loads — including one with a `plugin.py`, whose
ops are registered before the first frame. Nothing in `ui/pygame/` knows a card,
a zone or a class by name, so a variant's additions render as soon as its YAML
does. See [`modding_guide.md`](modding_guide.md).

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
| `O` | Settings |
| `F2` | Save the game |
| `F9` | Load a game |
| `F3` | FPS readout |
| `F4` | Layout debug overlay |
| `F5` | New game, same seats |
| `F11` | Fullscreen |
| `Ctrl+Q` | Quit |
| **`Ctrl+Shift+D`** | **Developer console** |

Hot-seat: when control passes between two humans a "pass the device" screen
blocks the next hand until someone hits Enter. Passing to an AI never
interrupts.

In the replay viewer (`--watch`) the transport bar takes over four keys, because
a recorded game has nothing to confirm or decline:

| Input | Effect |
|---|---|
| `Space` | Play / pause |
| `.` | Step one decision |
| `+` / `-` | Faster / slower (0.25x … 8x) |
| `Q` / `E` | Cameras, as in a live game |

---

## 3a. Saving, loading, and watching

**A save game is a decision log.** `Game = f(content_hash, seed, max_turns,
decisions)` (`rules_engine.md §6`), so a save stores the *inputs* rather than a
board, and loading replays them. That is not a shortcut — a snapshot of
`GameState` would need every flag a mod invents and every subscription the bus
holds, and would silently load a *different* game the day one of them was
forgotten. A restore either reproduces the game exactly or refuses to load.

* **`F2` saves.** The engine is blocked on your question when you press it,
  which is exactly an `Engine.savepoint`: nothing is mid-effect, and the log
  holds every decision so far. If an AI seat happens to be deliberating the
  engine *is* mid-step, and the board says "cannot save mid-action" rather than
  writing a position nobody was in.
* **`F9` lists what you can resume**, newest first. The list is drawn from each
  file's header — no game is replayed to describe one.
* **`--watch` opens the same board as a viewer.** It needed no branch in the
  scene, the panels or the tracker: a replay is a `DecisionSource` that reads
  its answers from a file, so as far as the board is concerned every seat is
  simply being played by somebody else.
* **The end of a partial log is not an error.** A save is a partial log by
  definition; the bar turns red and says "end of log" rather than pretending the
  game finished, which is how a real divergence would otherwise hide.

The terminal client speaks the same files: `s` at any prompt in `hts play`
saves, `hts play --load <name>` resumes, and `hts saves` lists.

## 3b. Settings — `O`

Everything on this screen is cosmetic. Nothing here can change what a card does,
who wins or what a seed deals, which is why it lives in `ui/settings.py` and no
part of `core/` may read it: the determinism claim holds with the file deleted.

| Setting | Notes |
|---|---|
| Sound, Volume | Master level for the procedural cues |
| Animations | All cosmetic effects |
| Screen shake | Separately, because it is the one people turn off first |
| Reaction countdown | The 10-second auto-pass on reaction windows |
| AI pace | How long an agent "thinks" before its move lands |
| HUD scale | Below 1.0 shrinks the chrome so the board grows |
| Fullscreen | Same as `F11` |

Changes apply as you click (a volume slider you cannot hear is useless) and are
written to `~/.here_to_slay/settings.json` when the screen closes. A corrupt,
unreadable or hand-mangled file loads the defaults and carries on; one bad value
does not discard the good ones next to it. `$HTS_CONFIG_DIR` moves the file.

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

Sound is synthesised at runtime (`sound.py`). There are no audio files, and a
pack can add cues without shipping any — see §5a.
Accent materials live in `materials.py` (foil + emissive); the dev console can
force the CPU path with **CPU materials**.

### 5a. Sound is a table, not an `if` chain

Which cue a moment plays lives in `cues.py`, keyed `family.name`
(`zone.party`, `band.success`, `ui.close`, `game.turn`), with a `*` fallback per
family. The board only ever asks for *moments*: `self.cue("zone.slain")`, never
`self.sound.play("slay")`. A test greps `scenes.py` to keep it that way.

This is the class-tracker bug in another costume. The ladder it replaced
compared zone names — `slain` roared, `discard` rustled, `party` thumped — and
every one of those strings is *content*. The sample variant adds a `cache` zone
and spells its failure band `fail` rather than `failure`, so under the old code
a card entering the cache made a generic thump and a blown roll made no sound
at all.

A pack drops a `sounds.yaml` beside its `pack.yaml` to re-point any key, and may
declare **new voices** as data — layers of waveform, envelope and pitch glide,
the same parameters the built-in cues use:

```yaml
cues:
  zone.cache: cache_write
  band.fail: { cue: failure, volume: 0.55 }

voices:
  cache_write:
    layers:
      - { duration: 0.13, frequency: [880, 1760, 0.6], wave: square,
          decay: 0.07, gain: 0.16 }
      - { duration: 0.22, frequency: 330, wave: triangle, decay: 0.14, gain: 0.18 }
```

`data/variants/overclock/sounds.yaml` is the worked example. Nothing validates
it against the pack vocabulary: a cue for a zone that does not exist is a
harmless dead entry, and a typo in a pack's audio must never be the reason a
game will not start — a broken file is skipped, not raised.

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

### The performance pass (Phase 11), measured

Numbers from `tests/`-shaped scripts at 1920x1080, four seats, base pack. Two
were real, one hypothesis was measured and rejected.

| | before | after |
|---|---|---|
| Steady frame | 15.2 ms (66 fps ceiling) | **5.8 ms (171 fps)** |
| Frame while dragging a window edge | 139 ms | **43 ms** |
| Felt repaints over 20 frames of drag | 20 | **1** |
| Card surfaces after six camera cycles | 608, still growing | bounded at 640 |

* **The backdrop was doing per-frame work it never needed to.** The room and
  the vignette were two full-screen *alpha* blits over a board that then covered
  them (an opaque composite is a straight copy), and the active seat's arc was
  `.copy()`-ing a table-sized surface every frame just to set an alpha on it.
  Motes now come out of the sprite cache pre-faded instead of being copied.
* **Size-keyed art was rebuilt on every frame of a resize.** The felt oval is a
  fraction of the window, so dragging an edge asks for a size never seen before
  sixty times a second — and painting one is a 2x-supersampled ellipse stack
  with a tiled grain layer, about 58 ms. `atmosphere._Layer` stretches the art
  it already has while the geometry is moving and builds the real thing once,
  on the first frame that asks for the same size twice.
* **The card and glyph caches were unbounded.** Every distinct pixel size ever
  drawn stayed resident for the life of the process — six camera cycles took the
  card cache from 49 surfaces to 608 and nothing ever came out. Both are LRUs
  now, and the plain card face is cached separately from its dimmed / highlighted
  / selected / tapped variants, so a card on screen twice composes once.
* **Rejected:** composing faces at a rounded size and rescaling to the exact one.
  Card widths move by more than any tolerable rounding step, so nearly every
  frame still missed the cache *and* paid a rescale of forty cards on top:
  35 ms/frame exact, 51 ms rounded to ~2%, 49 ms rounded to a visibly soft ~8%.
  The comment in `card_renderer.py` records it so nobody tries it twice.
* **Not a bottleneck, checked:** `build_view` costs 0.05 ms — 0.3% of a frame.
  The item on the Phase 11 list said "view construction", and the measurement
  says leave it alone.

---

### The class tracker reads the rule set, not the palette (fixed in Phase 10)

The party row and every opponent strip show a dot per class and an *n*/*m* bar
for the all-classes win. Both used to count `theme.CLASS_COLOURS`, which is a
**palette** — six entries because the base game has six classes. Loading the
seven-class sample variant made the bar read 6/6 forever and silently dropped
the seventh dot, because a class with no colour was a class the tracker could
not see.

They now count `rules.classes` and look the colour up with a fallback, so:

- a variant's extra class gets a dot, a chip and a slot in the bar;
- a class the palette has never heard of renders in the neutral ink instead of
  raising;
- the base game is unchanged, pixel for pixel — its six classes are the same
  six, in the same order.

The rules screen had the same bug in prose (`The six classes`) and now counts
too. `tests/test_pygame_ui.py` asserts all three against both packs.

This is the class of bug Phase 10 exists to find: the engine was already
data-driven and the UI quietly was not.

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

Phase 11 added the settings screen, the cue table (including the variant's own
`sounds.yaml`), the replay transport and bar, the window's half of save/load,
and four cache tests — two of which fail on the code before the fix.

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
- **A new sound** — add a `sounds.yaml` to your pack (§5a). No Python, no
  audio files.
- **A new preference** — one field on `ui/settings.py:Settings` and one row in
  `overlays.py:SETTING_ROWS`. The screen itself knows no preference names; a
  test asserts every row names a real field.

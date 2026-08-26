# Here to Slay

A moddable, data-driven engine for *Here to Slay* — built so that a rules variant
ships as a directory of YAML, not a fork of the codebase.

```
ui/, ai/  →  core/  →  content/  →  data/<pack>/*.yaml
              ↖ modding/ ↗
```

Python provides *mechanisms*; YAML provides *policy*. `modding/` is the one
package that sees both sides at once — importing a pack's `plugin.py` needs the
pack directory and the engine registries together. See [`docs/`](docs/) for the
architecture, schemas, rules engine, modding guide and build plan.

## Quick start

```bash
uv sync
uv run hts validate data/base
uv run pytest
```

## Play

```bash
# Terminal, hot-seat
uv run hts play data/base

# Graphical client — you versus three AIs, four-player table
uv run hts gui data/base --players 4 --ai 3

# Full six-player table
uv run hts gui data/base --players 6 --ai 5
```

The GUI supports 2–6 players, a light-blue tabletop with frosted panels,
hover-to-read opponent parties, tumbling dice, and a developer console
(`Ctrl+Shift+D`) for firing every animation and spawning any card. See
[`docs/ui_guide.md`](docs/ui_guide.md) for the board map, hotkeys and how to
drive it.

## Mod

```bash
# a runnable skeleton pack, yours to edit
uv run hts new-pack my_variant --plugin

# what does a variant actually change?
uv run hts diff-pack data/base data/variants/overclock

# play the shipped sample variant
uv run hts play data/variants/overclock
```

[`data/variants/overclock`](data/variants/overclock) is the worked example: a
seventh class, a per-seat `cache` zone, three new actions, a reaction window on
an event the engine has never heard of, a replaced win condition, and one new op
in each of the five registries — **with zero edits to `core/`**. The tour is
[`docs/modding_guide.md`](docs/modding_guide.md).

## Status

Phases 0–10 complete. The base game is playable end to end in the terminal
*and* in the PyGame client — 88 card definitions, 136 physical cards — with
the interrupt system (Challenges, Modifiers, and chains of both) proven under
load, and the modding claim proven by a variant rather than asserted. See
[`docs/build_plan.md`](docs/build_plan.md).

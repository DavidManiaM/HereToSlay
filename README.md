# Here to Slay

A moddable, data-driven engine for *Here to Slay* — built so that a rules variant
ships as a directory of YAML, not a fork of the codebase.

```
ui/, ai/  →  core/  →  content/  →  data/<pack>/*.yaml
```

Python provides *mechanisms*; YAML provides *policy*. See [`docs/`](docs/) for the
architecture, schemas, rules engine and build plan.

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

The GUI supports 2–6 players, a living tabletop, hover-to-read opponent
parties, tumbling dice, and a developer console (`Ctrl+Shift+D`) for firing
every animation and spawning any card. See [`docs/ui_guide.md`](docs/ui_guide.md)
for the board map, hotkeys and how to drive it.

## Status

Phases 0–9 complete. The base game is playable end to end in the terminal
*and* in the PyGame client — 88 card definitions, 136 physical cards — with
the interrupt system (Challenges, Modifiers, and chains of both) proven under
load. See [`docs/build_plan.md`](docs/build_plan.md).

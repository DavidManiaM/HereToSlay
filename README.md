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

## Status

Phases 0–6 complete. The base game is playable end to end in the terminal with
the full card set — 88 card definitions, 136 physical cards.

```bash
uv run hts play data/base
```

The AI, the reaction stress tests and the pygame client land in later phases;
see [`docs/build_plan.md`](docs/build_plan.md).

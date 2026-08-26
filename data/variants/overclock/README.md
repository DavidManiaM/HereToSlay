# Overclock

The sample variant, and Phase 10's acceptance test. It exists to prove that
every extension point the architecture claims actually works from a directory —
with **zero edits to `src/here_to_slay/core/`**.

```bash
uv run hts validate data/variants/overclock --strict
uv run hts diff-pack data/base data/variants/overclock
uv run hts play data/variants/overclock
uv run hts gui  data/variants/overclock --players 4 --ai 3
uv run hts sim  data/variants/overclock --games 200 --strict
```

## What it changes

You keep the base game and gain a **cache**: a public pile in front of you that
you fill a card at a time. Fill it and you win — but every upload can be
Firewalled, and a rival can flush the whole thing.

| Seam | What this pack does with it | Where |
|---|---|---|
| a new class | `hacker`, with three Heroes and a Party Leader | `rules.yaml`, `cards/hackers.yaml` |
| a new zone | `cache`, one per seat, public and ordered | `rules.yaml` |
| new actions | `upload`, `download`, `overclock` | `rules.yaml` |
| a new reaction window | `cache_upload`, on this pack's own event | `rules.yaml` |
| an altered win condition | `full_cache` in, `full_party` out | `rules.yaml` |
| a new effect op | `upload_card` | `plugin.py` |
| a new condition op | `cache_size` | `plugin.py` |
| a new selector | `cached` | `plugin.py` |
| a new mutator + event | `cache.uploaded` | `plugin.py` |
| a new currency | `cache_burn` — the `overclock` action costs no action points | `plugin.py` |
| patching another pack's card | Dracos, tuned down a pip by dotted path | `pack.yaml` |

## Reading order

`pack.yaml` → `rules.yaml` → `plugin.py` → `cards/`. Each file is commented with
the thing that is easy to get wrong about it; `rules.yaml` in particular labels
which of its blocks deep-merge, which merge by id, and which replace wholesale.

The tour is [`docs/modding_guide.md`](../../../docs/modding_guide.md); the tests
are `tests/test_variant_overclock.py`.

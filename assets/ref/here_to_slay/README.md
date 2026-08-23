# Here to Slay — reference assets (for AI / UI work)

Offline reference pack scraped for the **base game** (plus a few expansion cards that appear on the fan wiki).

**Not redistributable game content for commercial use.** Card art and rules belong to Unstable Games / TeeTurtle. Keep this tree as **local development reference** only.

## Start here

| File | Purpose |
|------|---------|
| [`meta/catalog.json`](meta/catalog.json) | Per-card index: name, kind, `game_id` (when matched to `data/base/cards`), image paths, wiki fields |
| [`meta/catalog.jsonl`](meta/catalog.jsonl) | Same data, one JSON object per line (easy to stream) |
| [`meta/base_deck_reference.json`](meta/base_deck_reference.json) | Official-wiki base deck sections + `HtS-Base-NNN.png` → title map |
| [`meta/scan_title_map.json`](meta/scan_title_map.json) | Scan filename ↔ card title ↔ gallery section |
| [`meta/summary.json`](meta/summary.json) | Counts / coverage |

## Layout

```
assets/ref/here_to_slay/
  README.md                 ← this file
  images/
    cards/                  ← art from here-to-slay.fandom.com (named art)
    by_type/                ← copy organized by leaders|heroes|monsters|items|…
    scans/                  ← placeholder for official HtS-Base-NNN.png scans
  rules/                    ← PDF rulebooks (EN/ES/IT + variants)
  text/                     ← per-card markdown + extracted PDF plain text
  meta/                     ← machine-readable indexes
  raw/                      ← Wayback HTML + Fandom API dumps
  scripts/                  ← re-fetch / rebuild helpers
```

## What we successfully fetched

1. **71 card images** from [Here to Slay Fandom](https://here-to-slay.fandom.com/) → `images/cards/` and `images/by_type/`.
2. **Per-card wikitext** (effects, roll thresholds where templated) → `text/<slug>.md` + `meta/catalog.json`.
3. **Rule PDFs** via Internet Archive snapshots of Unstable Games Wiki uploads → `rules/` and plain text in `text/*.txt`.
4. **Complete base-deck card list + scan IDs** (`HtS-Base-001` …) from Wayback HTML of the official wiki gallery → `meta/scan_title_map.json`.

## What we could *not* fetch

- **Live** [unstablegameswiki.com](https://www.unstablegameswiki.com/index.php?title=Here_To_Slay) is behind Cloudflare bot protection (API + HTML both 403).
- **Full-resolution official card scans** (`HtS-Base-NNN.png`) are linked from archived HTML, but Wayback has **no archived snapshots of those image URLs**, so binaries could not be downloaded automatically. Paths and titles are recorded so you can drop files into `images/scans/` later (browser download while logged in / manual save).

## Mapping to this repo’s game data

Many fan-wiki pages match `data/base/cards/*.yaml` by card name (`game_id` in the catalog). Unmatched rows are usually expansions / KSE / convention exclusives.

## Re-run

```bash
uv run python assets/ref/here_to_slay/scripts/fetch_fandom.py
uv run python assets/ref/here_to_slay/scripts/fetch_wayback_media.py
uv run python assets/ref/here_to_slay/scripts/map_scan_titles.py
uv run python assets/ref/here_to_slay/scripts/build_deck_reference.py
uv run python assets/ref/here_to_slay/scripts/organize_assets.py
```

## Sources

- https://www.unstablegameswiki.com/index.php?title=Here_To_Slay (primary; Cloudflare-blocked for bots)
- Wayback Machine snapshots of the above
- https://here-to-slay.fandom.com/ (secondary art + card pages)

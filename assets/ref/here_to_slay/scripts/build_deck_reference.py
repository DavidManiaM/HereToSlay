"""Build structured card list + scan URL manifest from cleaned wiki HTML."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "wayback_cleaned"
META = ROOT / "meta"


def parse_cards_in_deck(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "unknown"
    for line in text.splitlines():
        hm = re.search(r'<span class="mw-headline"[^>]*id="([^"]+)"', line)
        if hm:
            current = hm.group(1).replace("_", " ")
            sections.setdefault(current, [])
            continue
        # Cards-in-deck page uses <strong>Section</strong> headers
        sm = re.search(r"<strong>([^<]+)</strong>\s*$", line.strip())
        if not sm:
            sm = re.search(r"<strong>([^<]+)</strong>\s*<ul>", line)
        if sm:
            label = sm.group(1).strip()
            if label and "Here To Slay" not in label and len(label) < 60:
                current = label
                sections.setdefault(current, [])
                continue
        for name in re.findall(r'title="Here To Slay - ([^"]+)"', line):
            sections.setdefault(current, [])
            if name not in sections[current]:
                sections[current].append(name)
        for name in re.findall(
            r'href="/index\.php\?title=Here_To_Slay_-_([^"]+)"[^>]*>([^<]+)</a>',
            line,
        ):
            pretty = name[1].strip() or name[0].replace("_", " ")
            sections.setdefault(current, [])
            if pretty not in sections[current]:
                sections[current].append(pretty)
    return {k: v for k, v in sections.items() if v}


def main() -> None:
    deck = (RAW / "wayback_cards_in_deck.html").read_text(
        encoding="utf-8", errors="replace"
    )
    sections = parse_cards_in_deck(deck)

    title_map = json.loads((META / "scan_title_map.json").read_text(encoding="utf-8"))
    scan_to_title = title_map.get("scan_to_title", {})
    scan_to_section = title_map.get("scan_to_section", {})

    # Build reverse title -> scan
    title_to_scan = {v: k for k, v in scan_to_title.items()}

    # Wayback download URLs (for manual / later fetch)
    paths = json.loads((META / "scan_paths.json").read_text(encoding="utf-8"))
    path_by_name = {Path(p).name: p for p in paths}

    scans = []
    for fn, title in sorted(scan_to_title.items()):
        rel = path_by_name.get(fn)
        original = (
            f"https://unstablegameswiki.com{rel}" if rel else None
        )
        scans.append(
            {
                "filename": fn,
                "title": title,
                "section": scan_to_section.get(fn),
                "wiki_path": rel,
                "original_url": original,
                "wayback_candidates": [
                    f"https://web.archive.org/web/20250207233553id_/{original}",
                    f"https://web.archive.org/web/2024id_/{original}",
                ]
                if original
                else [],
                "local_scan": f"images/scans/{fn}",
            }
        )

    out = {
        "base_deck_sections": sections,
        "scans": scans,
        "notes": [
            "Official Unstable Games Wiki is Cloudflare-protected; HTML recovered via Wayback.",
            "Full-resolution scan binaries may need manual download if Wayback image CDN is unreachable.",
            "Per-card art also available under images/cards/ from here-to-slay.fandom.com.",
        ],
    }
    (META / "base_deck_reference.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Flat AI-friendly list of base deck card names by type
    flat = []
    for section, names in sections.items():
        for n in names:
            flat.append(
                {
                    "name": n,
                    "section": section,
                    "scan_file": title_to_scan.get(n),
                }
            )
    (META / "base_deck_cards.json").write_text(
        json.dumps(flat, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"sections={len(sections)} scans={len(scans)} flat={len(flat)}")
    for s, names in sections.items():
        print(f"  {s}: {len(names)}")


if __name__ == "__main__":
    main()

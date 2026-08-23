"""Organize fetched assets: categorize images, map to game card IDs, build catalog."""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]  # assets/ref/here_to_slay -> repo root
META = ROOT / "meta"
IMG_SRC = ROOT / "images" / "cards"
IMG_ORG = ROOT / "images" / "by_type"
TEXT = ROOT / "text"


KIND_DIRS = {
    "leader": "leaders",
    "party_leader": "leaders",
    "hero": "heroes",
    "monster": "monsters",
    "item": "items",
    "cursed_item": "items",
    "magic": "magic",
    "modifier": "modifiers",
    "challenge": "challenges",
    "mask": "items",
}


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "_", s)
    return s.strip("_")


def parse_template(wikitext: str) -> dict:
    """Best-effort parse of {{Name|key=val|...}} templates."""
    m = re.search(r"\{\{(\w[\w\s]*)\|(.*?)\}\}", wikitext, re.S)
    if not m:
        return {}
    name = m.group(1).strip()
    body = m.group(2)
    fields = {"_template": name}
    for part in re.split(r"\|", body):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        fields[k.strip().lower()] = re.sub(r"\s+", " ", v.strip())
    return fields


def load_title_kinds() -> dict[str, str]:
    """Map card title -> kind using official wiki gallery sections."""
    path = META / "scan_title_map.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    section_to_kind = {
        "party leaders": "leader",
        "personal-inner": "leader",  # leaders subsection id in some snapshots
        "monsters": "monster",
        "fighter heroes": "hero",
        "bard heroes": "hero",
        "guardian heroes": "hero",
        "ranger heroes": "hero",
        "thief heroes": "hero",
        "wizard heroes": "hero",
        "heroes": "hero",
        "items": "item",
        "cursed items": "item",
        "modifiers": "modifier",
        "magic": "magic",
        "challenges": "challenge",
        "card backs & rule card": "misc",
    }
    out: dict[str, str] = {}
    scan_to_title = data.get("scan_to_title", {})
    scan_to_section = data.get("scan_to_section", {})
    for fn, title in scan_to_title.items():
        section = (scan_to_section.get(fn) or "").replace("-", " ").lower()
        # normalize ids like Fighter_Heroes
        section = section.replace("_", " ")
        kind = "unknown"
        for key, k in section_to_kind.items():
            if key in section or section in key:
                kind = k
                break
        out[slugify(title)] = kind
        out[title.lower()] = kind
    return out


def infer_kind(card: dict, title_kinds: dict[str, str] | None = None) -> str:
    title_kinds = title_kinds or {}
    slug = card.get("slug") or slugify(card.get("name", ""))
    if slug in title_kinds:
        return title_kinds[slug]
    if card.get("name", "").lower() in title_kinds:
        return title_kinds[card["name"].lower()]

    cats = " ".join(card.get("categories") or []).lower()
    text = (card.get("wikitext") or "").lower()
    name = card.get("name", "").lower()
    if "party leader" in cats or "party_leader" in text or "leader" in cats:
        return "leader"
    if "monster" in cats or "monster_template" in text:
        return "monster"
    if "hero" in cats or "hero_template" in text:
        return "hero"
    if "magic" in cats:
        return "magic"
    if "modifier" in cats:
        return "modifier"
    if "challenge" in cats:
        return "challenge"
    if "item" in cats or "mask" in name:
        return "item"
    # filename heuristics from images
    for im in card.get("images") or []:
        low = im.lower()
        if "party_leader" in low or "leader" in low:
            return "leader"
    if name.startswith("the ") and any(
        x in name
        for x in (
            "song",
            "sage",
            "arrow",
            "reason",
            "horn",
            "claw",
            "flame",
            "bow",
            "maestro",
            "shaman",
            "howl",
            "raider",
            "unicorn",
            "panguardian",
            "trickster",
            "archer",
            "dread",
        )
    ):
        return "leader"
    return "unknown"


def load_game_cards() -> dict[str, dict]:
    """Map normalized name -> {id, kind, path} from data/base/cards."""
    cards_dir = REPO / "data" / "base" / "cards"
    by_name: dict[str, dict] = {}
    if not cards_dir.exists():
        return by_name
    for path in cards_dir.glob("*.yaml"):
        docs = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(docs, list):
            continue
        for card in docs:
            if not isinstance(card, dict) or "name" not in card:
                continue
            key = slugify(card["name"])
            by_name[key] = {
                "id": card.get("id"),
                "kind": card.get("kind"),
                "name": card["name"],
                "source_yaml": str(path.relative_to(REPO)).replace("\\", "/"),
            }
    return by_name


def main() -> None:
    META.mkdir(parents=True, exist_ok=True)
    TEXT.mkdir(parents=True, exist_ok=True)
    for d in set(KIND_DIRS.values()) | {"unknown", "misc"}:
        (IMG_ORG / d).mkdir(parents=True, exist_ok=True)

    index = json.loads((META / "cards_index.json").read_text(encoding="utf-8"))
    game = load_game_cards()
    title_kinds = load_title_kinds()

    catalog = []
    unmatched = []
    by_type_counts: dict[str, int] = defaultdict(int)

    for card in index:
        kind = infer_kind(card, title_kinds)
        fields = parse_template(card.get("wikitext") or "")
        slug = card.get("slug") or slugify(card["name"])
        game_match = game.get(slug)
        # fuzzy: try without leading the_
        if not game_match and slug.startswith("the_"):
            game_match = game.get(slug[4:])

        organized_images = []
        dest_dir = IMG_ORG / KIND_DIRS.get(kind, "unknown")
        for rel in card.get("images") or []:
            src = ROOT / rel
            if not src.exists():
                # also try relative to images/cards
                alt = IMG_SRC / Path(rel).name
                src = alt if alt.exists() else src
            if not src.exists():
                continue
            dest = dest_dir / f"{slug}{src.suffix.lower()}"
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dest)
            organized_images.append(
                str(dest.relative_to(ROOT)).replace("\\", "/")
            )

        # write per-card text excerpt for AI
        text_path = TEXT / f"{slug}.md"
        lines = [
            f"# {card['name']}",
            "",
            f"- kind_guess: `{kind}`",
            f"- slug: `{slug}`",
        ]
        if game_match:
            lines.append(f"- game_id: `{game_match['id']}`")
            lines.append(f"- game_yaml: `{game_match['source_yaml']}`")
        else:
            lines.append("- game_id: _(no match in data/base)_")
        if card.get("wiki_url"):
            lines.append(f"- fandom: {card['wiki_url']}")
        if organized_images:
            lines.append("- images:")
            for im in organized_images:
                lines.append(f"  - `{im}`")
        if fields:
            lines.append("")
            lines.append("## Parsed wiki fields")
            for k, v in fields.items():
                lines.append(f"- **{k}**: {v}")
        if card.get("wikitext"):
            lines.append("")
            lines.append("## Raw wikitext")
            lines.append("```")
            lines.append(card["wikitext"].strip())
            lines.append("```")
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        entry = {
            "name": card["name"],
            "slug": slug,
            "kind": kind,
            "game_id": game_match["id"] if game_match else None,
            "fandom_url": card.get("wiki_url"),
            "images": organized_images,
            "text_file": str(text_path.relative_to(ROOT)).replace("\\", "/"),
            "wiki_fields": fields,
            "categories": card.get("categories") or [],
            "base_game": any(
                "base" in (c or "").lower() for c in (card.get("categories") or [])
            )
            or ("base game" in (card.get("wikitext") or "").lower()),
        }
        catalog.append(entry)
        by_type_counts[kind] += 1
        if not game_match:
            unmatched.append(card["name"])

    catalog.sort(key=lambda e: (e["kind"], e["name"]))
    (META / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Compact AI-friendly CSV-ish JSONL
    with (META / "catalog.jsonl").open("w", encoding="utf-8") as f:
        for e in catalog:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    summary = {
        "total_cards": len(catalog),
        "by_kind": dict(by_type_counts),
        "matched_to_game_yaml": len(catalog) - len(unmatched),
        "unmatched_names": unmatched,
        "sources": [
            "https://here-to-slay.fandom.com/",
            "https://web.archive.org/ (unstablegameswiki.com snapshots)",
            "https://www.unstablegameswiki.com/ (blocked by Cloudflare for bots)",
        ],
    }
    (META / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

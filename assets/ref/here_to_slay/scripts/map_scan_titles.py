"""Map HtS-Base-NNN.png -> card title from gallery href wrappers."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "wayback_cleaned"
META = ROOT / "meta"


def main() -> None:
    text = (RAW / "wayback_card_previews.html").read_text(
        encoding="utf-8", errors="replace"
    )
    mapping: dict[str, str] = {}

    # <a href="...Here_To_Slay_-_Card_Name"><img alt="HtS-Base-003.png" ...
    for m in re.finditer(
        r'href="/index\.php\?title=Here_To_Slay_-_([^"]+)"[^>]*>\s*<img[^>]+alt="(HtS-[^"]+\.(?:png|jpe?g))"',
        text,
        re.I,
    ):
        title = m.group(1).replace("_", " ").strip()
        title = title.replace("%27", "'")
        mapping[m.group(2)] = title

    # Reverse order sometimes
    for m in re.finditer(
        r'alt="(HtS-[^"]+\.(?:png|jpe?g))"[^>]*>\s*</a>',
        text,
        re.I,
    ):
        # look backwards for title=
        start = max(0, m.start() - 300)
        chunk = text[start : m.start()]
        tm = re.search(r'title=Here_To_Slay_-_([^"&]+)', chunk)
        if tm:
            mapping.setdefault(
                m.group(1), tm.group(1).replace("_", " ").strip()
            )

    inv = (RAW / "wayback_inventory.html").read_text(
        encoding="utf-8", errors="replace"
    )
    inv_names = sorted(set(re.findall(r'title="Here To Slay - ([^"]+)"', inv)))

    bases = sorted(set(re.findall(r"HtS-Base-\d+[A-Za-z0-9_-]*\.png", text, re.I)))

    # Also parse section headings to know type ranges — capture last h2 before each image
    typed: dict[str, str] = {}
    section = "unknown"
    for m in re.finditer(
        r'<h2[^>]*>.*?id="([^"]+)".*?</h2>|alt="(HtS-[^"]+\.(?:png|jpe?g))"',
        text,
        re.I | re.S,
    ):
        if m.group(1):
            section = m.group(1)
        elif m.group(2):
            typed[m.group(2)] = section

    out = {
        "scan_to_title": dict(sorted(mapping.items())),
        "scan_to_section": typed,
        "inventory_card_names": inv_names,
        "base_scan_filenames": bases,
    }
    (META / "scan_title_map.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"mapped {len(mapping)}  sections {len(typed)}  inventory {len(inv_names)}")
    for k, v in list(mapping.items())[:20]:
        print(f"  {k} -> {v} [{typed.get(k,'?')}]")


if __name__ == "__main__":
    main()

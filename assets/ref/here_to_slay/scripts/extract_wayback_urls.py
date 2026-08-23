"""Extract image / PDF URLs from archived Unstable Games Wiki HTML."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
META = ROOT / "meta"

IMG_RE = re.compile(
    r"https?://[^\"'\s<>]+?\.(?:png|jpg|jpeg|webp|gif|pdf|svg)",
    re.I,
)
# Wayback wraps: https://web.archive.org/web/TIMESTAMP/http://...
WAYBACK_STRIP = re.compile(
    r"^https?://web\.archive\.org/web/\d+(?:id_)?/",
    re.I,
)


def normalize(url: str) -> str:
    url = WAYBACK_STRIP.sub("", url)
    return unquote(url)


def main() -> None:
    META.mkdir(parents=True, exist_ok=True)
    found: dict[str, list[str]] = {}
    all_urls: set[str] = set()

    for path in sorted(RAW.glob("wayback_*.html")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        urls = []
        for m in IMG_RE.findall(text):
            # Prefer original URL for downloading via Wayback id_
            original = normalize(m)
            urls.append(original)
            all_urls.add(original)
        found[path.name] = sorted(set(urls))
        print(f"{path.name}: {len(found[path.name])} unique media URLs")

    (META / "wayback_media_urls.json").write_text(
        json.dumps({"by_page": found, "all": sorted(all_urls)}, indent=2),
        encoding="utf-8",
    )
    print(f"total unique: {len(all_urls)}")
    for u in sorted(all_urls)[:40]:
        print(" ", u)


if __name__ == "__main__":
    main()

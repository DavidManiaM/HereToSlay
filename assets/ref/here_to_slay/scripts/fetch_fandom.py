"""Fetch Here to Slay card pages and images from the Fandom wiki API."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
IMG = ROOT / "images" / "cards"
META = ROOT / "meta"

FANDOM_API = "https://here-to-slay.fandom.com/api.php"
UA = "HereToSlayDevAssetFetcher/1.0 (local game adaptation; contact: local)"

SKIP_TITLES = {
    "Main Page",
    "Here To Slay Wiki",
    "Hero Cards",
    "Item Cards",
    "Monster Cards",
    "Magic Cards",
    "Party Leader Cards",
    "Game Expansions",
}


def api(params: dict) -> dict:
    q = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(
        FANDOM_API + "?" + q,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "_", s)
    return s.strip("_")


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        dest.write_bytes(data)
        return True
    except urllib.error.HTTPError as e:
        print(f"  FAIL {e.code} {url}")
        return False


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    IMG.mkdir(parents=True, exist_ok=True)
    META.mkdir(parents=True, exist_ok=True)

    allpages_path = RAW / "fandom_allpages.json"
    if not allpages_path.exists():
        raise SystemExit("missing fandom_allpages.json — fetch it first")

    pages = json.loads(allpages_path.read_text(encoding="utf-8"))["query"]["allpages"]
    card_titles = [p["title"] for p in pages if p["title"] not in SKIP_TITLES]
    print(f"card pages: {len(card_titles)}")

    manifest: list[dict] = []
    for i in range(0, len(card_titles), 8):
        batch = card_titles[i : i + 8]
        data = api(
            {
                "action": "query",
                "titles": "|".join(batch),
                "prop": "images|revisions|categories|info",
                "rvprop": "content",
                "rvslots": "main",
                "imlimit": 50,
                "inprop": "url",
            }
        )
        for _pid, page in data["query"]["pages"].items():
            if "missing" in page:
                continue
            wikitext = ""
            if "revisions" in page:
                rev = page["revisions"][0]
                wikitext = rev.get("slots", {}).get("main", {}).get("*", "") or rev.get(
                    "*", ""
                )
            manifest.append(
                {
                    "title": page.get("title"),
                    "pageid": page.get("pageid"),
                    "fullurl": page.get("fullurl"),
                    "images": [im["title"] for im in page.get("images", [])],
                    "categories": [
                        c.get("title", "") for c in page.get("categories", [])
                    ],
                    "wikitext": wikitext,
                }
            )
        print(f"  batch {i // 8 + 1}: {len(manifest)} pages")
        time.sleep(0.35)

    (RAW / "fandom_cards_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    files = sorted({im for m in manifest for im in m["images"]})
    print(f"unique files: {len(files)}")
    file_urls: dict[str, dict] = {}
    for i in range(0, len(files), 15):
        batch = files[i : i + 15]
        data = api(
            {
                "action": "query",
                "titles": "|".join(batch),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|sha1|extmetadata",
            }
        )
        for _pid, page in data["query"]["pages"].items():
            if "imageinfo" in page:
                info = page["imageinfo"][0]
                file_urls[page["title"]] = {
                    "url": info["url"],
                    "size": info.get("size"),
                    "mime": info.get("mime"),
                    "sha1": info.get("sha1"),
                    "extmetadata": info.get("extmetadata", {}),
                }
        time.sleep(0.25)

    (RAW / "fandom_image_urls.json").write_text(
        json.dumps(file_urls, indent=2), encoding="utf-8"
    )

    # Download card images; prefer non-site chrome
    chrome = re.compile(r"(site-logo|favicon|wordmark|wiki\.png|fandom)", re.I)
    downloaded = []
    for file_title, info in file_urls.items():
        url = info["url"]
        if chrome.search(file_title) or chrome.search(url):
            continue
        # File:Foo.png -> foo.png
        bare = file_title.split(":", 1)[-1]
        dest = IMG / bare.replace(" ", "_")
        ok = download(url, dest)
        if ok:
            downloaded.append(
                {
                    "file_title": file_title,
                    "local_path": str(dest.relative_to(ROOT)).replace("\\", "/"),
                    "source_url": url,
                    "sha1": info.get("sha1"),
                    "mime": info.get("mime"),
                    "size": info.get("size"),
                }
            )
            print(f"  saved {dest.name}")
        time.sleep(0.15)

    (META / "downloaded_images.json").write_text(
        json.dumps(downloaded, indent=2), encoding="utf-8"
    )

    # Per-card index linking title -> local images + effect text snippet
    by_card = []
    for m in manifest:
        locals_ = []
        for im in m["images"]:
            if im not in file_urls:
                continue
            bare = im.split(":", 1)[-1].replace(" ", "_")
            local = IMG / bare
            if local.exists():
                locals_.append(str(local.relative_to(ROOT)).replace("\\", "/"))
        by_card.append(
            {
                "name": m["title"],
                "slug": slugify(m["title"]),
                "wiki_url": m.get("fullurl"),
                "categories": m.get("categories", []),
                "images": locals_,
                "wikitext": m.get("wikitext", ""),
            }
        )

    (META / "cards_index.json").write_text(
        json.dumps(by_card, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"done: {len(downloaded)} images, {len(by_card)} cards")


if __name__ == "__main__":
    main()

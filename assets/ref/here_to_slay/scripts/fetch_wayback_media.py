"""Clean gzip-prefixed Wayback HTML, extract media, download PDFs + gallery images."""

from __future__ import annotations

import gzip
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
META = ROOT / "meta"
RULES = ROOT / "rules"
GALLERY = ROOT / "images" / "wiki_gallery"
UA = "HereToSlayDevAssetFetcher/1.0 (local game adaptation)"

IMG_RE = re.compile(
    r"(?:src|href|data-src)=[\"']([^\"']+\.(?:png|jpe?g|webp|gif|pdf|svg))[\"']",
    re.I,
)
ABS_RE = re.compile(
    r"https?://[^\"'\s<>]+?\.(?:png|jpe?g|webp|gif|pdf|svg)",
    re.I,
)
FILE_LINK_RE = re.compile(r"(?:File|Image):([^\]\|\"'<>]+)", re.I)
WAYBACK_STRIP = re.compile(r"^https?://web\.archive\.org/web/\d+(?:id_)?/", re.I)


def read_html(path: Path) -> str:
    raw = path.read_bytes()
    # Some Wayback responses are raw gzip bytes; others are plain HTML with a
    # stray gzip header prefix before <!DOCTYPE.
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw).decode("utf-8", errors="replace")
        except OSError:
            # Truncated / nonstandard: find HTML start
            idx = raw.find(b"<!DOCTYPE")
            if idx == -1:
                idx = raw.find(b"<html")
            if idx != -1:
                return raw[idx:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def normalize(url: str) -> str:
    return WAYBACK_STRIP.sub("", url)


def wayback_id_url(original: str, ts: str = "2024") -> str:
    # Prefer id_ (raw) capture
    return f"https://web.archive.org/web/{ts}id_/{original}"


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  skip exists {dest.name}")
        return True
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if len(data) < 500 or b"Just a moment" in data[:2000]:
            print(f"  bad response {url}")
            return False
        dest.write_bytes(data)
        print(f"  saved {dest.name} ({len(data)} bytes)")
        return True
    except Exception as e:
        print(f"  FAIL {url}: {e}")
        return False


def main() -> None:
    META.mkdir(parents=True, exist_ok=True)
    RULES.mkdir(parents=True, exist_ok=True)
    GALLERY.mkdir(parents=True, exist_ok=True)

    cleaned_dir = RAW / "wayback_cleaned"
    cleaned_dir.mkdir(exist_ok=True)

    all_media: set[str] = set()
    all_files: set[str] = set()
    by_page: dict[str, dict] = {}

    for path in sorted(RAW.glob("wayback_*.html")):
        text = read_html(path)
        out = cleaned_dir / path.name
        out.write_text(text, encoding="utf-8")

        media = set()
        for m in IMG_RE.findall(text):
            if m.startswith("//"):
                m = "https:" + m
            media.add(normalize(m))
        for m in ABS_RE.findall(text):
            media.add(normalize(m))
        files = {f.strip() for f in FILE_LINK_RE.findall(text)}
        all_media |= media
        all_files |= files
        by_page[path.name] = {
            "media_count": len(media),
            "file_links": sorted(files),
            "media_sample": sorted(media)[:30],
        }
        print(f"{path.name}: media={len(media)} file_links={len(files)}")

    (META / "wayback_extracted.json").write_text(
        json.dumps(
            {
                "by_page": by_page,
                "all_media": sorted(all_media),
                "all_file_links": sorted(all_files),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Download known rule PDFs (from main page + common names)
    pdfs = [u for u in all_media if u.lower().endswith(".pdf")]
    # Ensure English base rules are attempted even if missed
    defaults = [
        "https://unstablegameswiki.com/images/7/73/Here-to-Slay-Rules.pdf",
        "https://unstablegameswiki.com/images/5/56/HTS_Rules_for-KS-1_v1-fixed.pdf",
        "https://unstablegameswiki.com/images/c/cf/H2S_2v2_Variant.pdf",
        "https://unstablegameswiki.com/images/f/fb/Warrior-Druid-Expansion-Rules.pdf",
        "https://unstablegameswiki.com/images/4/4e/HTS_Reglas_KS_Spanish.pdf",
        "https://unstablegameswiki.com/images/1/1c/Here_to_Slay!_Manuale_Italiano.pdf",
    ]
    for u in defaults:
        if u not in pdfs:
            pdfs.append(u)

    print(f"\nDownloading {len(pdfs)} PDFs via Wayback...")
    for url in pdfs:
        name = url.rstrip("/").split("/")[-1]
        # Try several timestamps
        ok = False
        for ts in ("20240601000000", "20240101000000", "2023", "2022", "2024"):
            ok = download(wayback_id_url(url, ts), RULES / name)
            if ok:
                break
            time.sleep(0.4)
        time.sleep(0.5)

    # Try to pull card preview gallery page with a known good snapshot from CDX
    cdx_path = RAW / "cdx_previews.json"
    if cdx_path.exists():
        try:
            cdx = json.loads(cdx_path.read_text(encoding="utf-8"))
            # rows: [urlkey, timestamp, original, mimetype, statuscode, digest, length]
            snaps = [row for row in cdx[1:] if row[4] == "200"]
            print(f"CDX snapshots: {len(snaps)}")
            if snaps:
                # Prefer later snapshots
                best = snaps[-1]
                ts, original = best[1], best[2]
                gallery_url = f"https://web.archive.org/web/{ts}id_/{original}"
                dest = RAW / f"wayback_card_previews_{ts}.html"
                if download(gallery_url, dest):
                    text = read_html(dest)
                    (RAW / "wayback_cleaned" / dest.name).write_text(
                        text, encoding="utf-8"
                    )
                    imgs = sorted({normalize(u) for u in ABS_RE.findall(text)})
                    print(f"  preview page images: {len(imgs)}")
                    img_list = [
                        u
                        for u in imgs
                        if any(
                            u.lower().endswith(ext)
                            for ext in (".png", ".jpg", ".jpeg", ".webp")
                        )
                        and "wiki" not in Path(u).name.lower()
                    ]
                    (META / "wayback_preview_images.json").write_text(
                        json.dumps(img_list, indent=2), encoding="utf-8"
                    )
                    print(f"Downloading up to {min(len(img_list), 200)} gallery images...")
                    for url in img_list[:200]:
                        name = url.rstrip("/").split("/")[-1]
                        # Use same timestamp for image
                        download(wayback_id_url(url, ts), GALLERY / name)
                        time.sleep(0.25)
        except Exception as e:
            print(f"CDX gallery failed: {e}")

    print("done")


if __name__ == "__main__":
    main()

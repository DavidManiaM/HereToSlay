"""Download full-resolution HtS-Base / expansion card scans from Wayback."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "meta"
OUT = ROOT / "images" / "scans"
UA = "HereToSlayDevAssetFetcher/1.0 (local game adaptation)"

# /images/thumb/a/ab/File.png/100px-File.png  -> /images/a/ab/File.png
THUMB_RE = re.compile(
    r"/images/thumb/([0-9a-f])/([0-9a-f]{2})/([^/]+)/(\d+px-\3)",
    re.I,
)
HOSTS = (
    "https://unstablegameswiki.com",
    "https://www.unstablegameswiki.com",
)
TIMESTAMPS = (
    "20250207233553",
    "20250427040636",
    "20240601000000",
    "20230330113512",
    "2024",
    "2023",
)


def full_from_thumb(path: str) -> str | None:
    m = THUMB_RE.search(path)
    if not m:
        return None
    a, ab, name, _ = m.groups()
    return f"/images/{a}/{ab}/{name}"


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 5000:
        return True
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        if len(data) < 2000 or b"Just a moment" in data[:1500]:
            return False
        # Reject HTML error pages
        if data[:15].lstrip().startswith(b"<!DOCTYPE") or data[:6] == b"<html>":
            return False
        dest.write_bytes(data)
        return True
    except Exception:
        return False


def main() -> None:
    extracted = json.loads((META / "wayback_extracted.json").read_text(encoding="utf-8"))
    media = extracted["all_media"]

    full_paths: set[str] = set()
    for m in media:
        # relative thumb paths
        fp = full_from_thumb(m)
        if fp:
            full_paths.add(fp)
            continue
        # already a full /images/x/xy/File path (not thumb, not index.php)
        if re.match(r"/images/[0-9a-f]/[0-9a-f]{2}/[^/]+\.(?:png|jpe?g|webp)$", m, re.I):
            full_paths.add(m)
        elif re.search(r"/images/[0-9a-f]/[0-9a-f]{2}/[^/]+\.(?:png|jpe?g|webp)$", m, re.I):
            # absolute URL
            path = "/" + m.split("/", 3)[-1] if ":///" in m else None
            # better: extract path
            mm = re.search(r"(/images/[0-9a-f]/[0-9a-f]{2}/[^/]+\.(?:png|jpe?g|webp))", m, re.I)
            if mm and "/thumb/" not in m:
                full_paths.add(mm.group(1))

    # Prefer card scans
    card_paths = sorted(
        p
        for p in full_paths
        if re.search(r"HtS-|HTS-", Path(p).name, re.I)
        and "Notavail" not in Path(p).name
    )
    print(f"card scan paths: {len(card_paths)}")

    (META / "scan_paths.json").write_text(
        json.dumps(card_paths, indent=2), encoding="utf-8"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    ok_n = 0
    fail = []
    for path in card_paths:
        name = Path(path).name
        dest = OUT / name
        if dest.exists() and dest.stat().st_size > 5000:
            ok_n += 1
            continue
        success = False
        for host in HOSTS:
            original = host + path
            for ts in TIMESTAMPS:
                wb = f"https://web.archive.org/web/{ts}id_/{original}"
                if download(wb, dest):
                    print(f"  OK {name} via {ts}")
                    success = True
                    ok_n += 1
                    break
                time.sleep(0.2)
            if success:
                break
            time.sleep(0.2)
        if not success:
            print(f"  FAIL {name}")
            fail.append(path)
        time.sleep(0.3)

    (META / "scan_download_report.json").write_text(
        json.dumps({"ok": ok_n, "failed": fail}, indent=2), encoding="utf-8"
    )
    print(f"done ok={ok_n} fail={len(fail)}")


if __name__ == "__main__":
    main()

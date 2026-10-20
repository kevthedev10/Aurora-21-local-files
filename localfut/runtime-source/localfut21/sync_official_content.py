"""Download the finite FUT presentation asset set needed by LocalFUT.

The files are public static CDN responses used by FIFA's Store/New Items UI.
They are stored beside the runtime source so the local server can serve them
without relying on the CDN after this command completes.
"""
from __future__ import annotations

import argparse
import os
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT / "contentfifa"
FUT_BASE = (
    "https://fifa21.content.easports.com/fifa/fltOnlineAssets/"
    "21D4F1AC-91A3-458D-A64E-895AA6D871D1/2021"
)
LEGACY_FUT_BASE = (
    "https://fifa21.content.easports.com/fifa/fltOnlineAssets/"
    "7D49A6B1-760B-4491-B10C-167FBC81D58A/2019"
)
ASSETS = {
    "fifa/cfgrouting/cfgrouting.xml": (
        "https://fifa21.content.easports.com/fifa/fltOnlineAssets/"
        "CC8267B6-0817-4842-BB6A-A20F88B05418/2017/fifa/cfgrouting/cfgrouting.xml"
    ),
    "fut/packs/loc/storepackdescriptions.en_gb.xml": FUT_BASE + "/fut/packs/loc/storepackdescriptions.en_gb.xml",
    "fut/items/images/backgrounds/itemBGs/futitemraritytunables.json": FUT_BASE + "/fut/items/images/backgrounds/itemBGs/futitemraritytunables.json",
    "fut/packs/packopening/packopeningsetting.json": FUT_BASE + "/fut/packs/packopening/packopeningsetting.json",
    "fut/packs/packopening/packopeningconfig.json": FUT_BASE + "/fut/packs/packopening/packopeningconfig.json",
    # The final FIFA 21 content bundle no longer exposes this aggregate file;
    # retain the compatible legacy copy used by the native PC cache.
    "fut/playerheads/g4/fut2dheads.big": LEGACY_FUT_BASE + "/fut/playerheads/g4/fut2dheads.big",
}
MAX_BYTES = 2 * 1024 * 1024


def download(destination: Path, url: str, refresh: bool) -> str:
    if destination.is_file() and destination.stat().st_size and not refresh:
        return "kept"
    request = urllib.request.Request(url, headers={"User-Agent": "FIFA21-LocalFUT/0.2"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
    if not body or len(body) > MAX_BYTES:
        raise ValueError(f"unexpected response size: {len(body)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, destination)
    return f"downloaded ({len(body)} bytes)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Cache LocalFUT presentation assets from the official FIFA CDN")
    parser.add_argument("--refresh", action="store_true", help="download again even when a local copy exists")
    args = parser.parse_args()
    failures = 0
    for relative, url in ASSETS.items():
        try:
            result = download(DESTINATION / relative, url, args.refresh)
            print(f"{result}: {relative}")
        except Exception as error:
            failures += 1
            print(f"FAILED: {relative}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

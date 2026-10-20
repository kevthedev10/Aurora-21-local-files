from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pefile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    size = len(data)
    return -sum(
        (count / size) * math.log2(count / size)
        for count in counts
        if count
    )


def inspect(path: Path, anchors: list[str]) -> dict[str, object]:
    raw = path.read_bytes()
    pe = pefile.PE(data=raw, fast_load=False)

    imports: dict[str, list[str]] = {}
    for module in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        module_name = module.dll.decode("ascii", errors="replace")
        imports[module_name] = [
            item.name.decode("ascii", errors="replace")
            if item.name
            else f"ordinal:{item.ordinal}"
            for item in module.imports
        ]

    exports: list[str] = []
    directory = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if directory:
        exports = [
            symbol.name.decode("ascii", errors="replace")
            if symbol.name
            else f"ordinal:{symbol.ordinal}"
            for symbol in directory.symbols
        ]

    anchor_results: list[dict[str, str]] = []
    for value in anchors:
        encoded = value.encode("utf-8")
        start = 0
        while True:
            offset = raw.find(encoded, start)
            if offset < 0:
                break
            rva = pe.get_rva_from_offset(offset)
            anchor_results.append(
                {
                    "value": value,
                    "file_offset": f"0x{offset:X}",
                    "rva": f"0x{rva:X}",
                    "preferred_va": f"0x{pe.OPTIONAL_HEADER.ImageBase + rva:X}",
                }
            )
            start = offset + 1

    security = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]
    ]
    return {
        "file": path.name,
        "size": len(raw),
        "sha256": sha256(path),
        "machine": f"0x{pe.FILE_HEADER.Machine:04X}",
        "image_base": f"0x{pe.OPTIONAL_HEADER.ImageBase:X}",
        "entry_point_rva": f"0x{pe.OPTIONAL_HEADER.AddressOfEntryPoint:X}",
        "certificate_table_present": bool(security.VirtualAddress and security.Size),
        "sections": [
            {
                "name": section.Name.rstrip(b"\0").decode("ascii", errors="replace"),
                "rva": f"0x{section.VirtualAddress:X}",
                "virtual_size": int(section.Misc_VirtualSize),
                "raw_size": int(section.SizeOfRawData),
                "entropy": round(entropy(section.get_data()), 5),
                "characteristics": f"0x{section.Characteristics:08X}",
            }
            for section in pe.sections
        ],
        "imports": imports,
        "exports": exports,
        "anchors": anchor_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hash and inventory local PE files without copying them."
    )
    parser.add_argument("binary", type=Path, nargs="+")
    parser.add_argument("--anchor", action="append", default=[])
    args = parser.parse_args()

    missing = [path for path in args.binary if not path.is_file()]
    if missing:
        parser.error("missing files: " + ", ".join(str(path) for path in missing))
    print(json.dumps([inspect(path, args.anchor) for path in args.binary], indent=2))


if __name__ == "__main__":
    main()

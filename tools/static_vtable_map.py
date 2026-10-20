from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import pefile


def parse_int(value: str) -> int:
    return int(value, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


@dataclass(frozen=True)
class RuntimeFunction:
    begin_rva: int
    end_rva: int
    unwind_rva: int


def runtime_functions(pe: pefile.PE) -> list[RuntimeFunction]:
    functions = [
        RuntimeFunction(
            begin_rva=int(entry.struct.BeginAddress),
            end_rva=int(entry.struct.EndAddress),
            unwind_rva=int(entry.struct.UnwindData),
        )
        for entry in getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", [])
        if int(entry.struct.EndAddress) > int(entry.struct.BeginAddress)
    ]
    return sorted(functions, key=lambda item: item.begin_rva)


def containing_function(
    functions: list[RuntimeFunction], starts: list[int], rva: int
) -> RuntimeFunction | None:
    index = bisect.bisect_right(starts, rva) - 1
    if index < 0:
        return None
    candidate = functions[index]
    return candidate if rva < candidate.end_rva else None


def raw_backed_file_offset(pe: pefile.PE, rva: int, size: int) -> int:
    range_end = rva + size
    for section in pe.sections:
        section_rva = int(section.VirtualAddress)
        section_raw_end = section_rva + int(section.SizeOfRawData)
        if section_rva <= rva and range_end <= section_raw_end:
            offset = int(section.PointerToRawData) + (rva - section_rva)
            if offset + size <= len(pe.__data__):
                return offset
            break
    raise ValueError("vtable range is not fully backed by one PE section")


def map_vtable(
    pe: pefile.PE,
    rva: int,
    entries: int,
    functions: list[RuntimeFunction],
    starts: list[int],
) -> list[dict[str, object]]:
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    image_end = image_base + int(pe.OPTIONAL_HEADER.SizeOfImage)
    size = entries * 8
    offset = raw_backed_file_offset(pe, rva, size)
    data = pe.__data__[offset : offset + size]

    result = []
    for index, (value,) in enumerate(struct.iter_unpack("<Q", data)):
        target_rva = value - image_base if image_base <= value < image_end else None
        owner = (
            containing_function(functions, starts, target_rva)
            if target_rva is not None
            else None
        )
        result.append(
            {
                "index": index,
                "slot_offset": f"0x{index * 8:X}",
                "value": f"0x{value:X}",
                "target_rva": f"0x{target_rva:X}" if target_rva is not None else None,
                "function": (
                    {
                        "begin_rva": f"0x{owner.begin_rva:X}",
                        "end_rva": f"0x{owner.end_rva:X}",
                        "unwind_rva": f"0x{owner.unwind_rva:X}",
                    }
                    if owner is not None
                    else None
                ),
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve 64-bit PE vtable entries to image RVAs and exception-directory "
            "function bounds without loading or modifying the binary."
        )
    )
    parser.add_argument("binary", type=Path)
    parser.add_argument("--rva", type=parse_int, required=True)
    parser.add_argument("--entries", type=int, default=16)
    parser.add_argument(
        "--expect-sha256",
        help="refuse analysis unless the file matches this SHA-256",
    )
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"binary does not exist: {args.binary}")
    if args.rva < 0:
        parser.error("--rva must be non-negative")
    if not 1 <= args.entries <= 256:
        parser.error("--entries must be between 1 and 256")

    actual_hash = sha256(args.binary)
    if args.expect_sha256 and actual_hash != args.expect_sha256.upper():
        parser.error(
            f"SHA-256 mismatch: expected {args.expect_sha256.upper()}, "
            f"got {actual_hash}"
        )

    pe = pefile.PE(str(args.binary), fast_load=False)
    functions = runtime_functions(pe)
    if not functions:
        parser.error("PE has no x64 exception-directory function records")
    starts = [function.begin_rva for function in functions]

    try:
        entries = map_vtable(pe, args.rva, args.entries, functions, starts)
    except (pefile.PEFormatError, ValueError) as error:
        parser.error(str(error))

    print(
        json.dumps(
            {
                "file": args.binary.name,
                "sha256": actual_hash,
                "image_base": f"0x{int(pe.OPTIONAL_HEADER.ImageBase):X}",
                "vtable_rva": f"0x{args.rva:X}",
                "entries": entries,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

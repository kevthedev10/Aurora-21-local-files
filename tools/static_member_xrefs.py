from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, CS_OP_MEM, Cs
from capstone.x86_const import X86_REG_RIP


IMAGE_SCN_MEM_EXECUTE = 0x20000000


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


def executable_sections(pe: pefile.PE) -> list[tuple[int, bytes]]:
    return [
        (int(section.VirtualAddress), section.get_data())
        for section in pe.sections
        if int(section.Characteristics) & IMAGE_SCN_MEM_EXECUTE
    ]


def disassembler() -> Cs:
    result = Cs(CS_ARCH_X86, CS_MODE_64)
    result.detail = True
    result.skipdata = True
    return result


def register_name(decoder: Cs, register: int) -> str | None:
    if register == 0:
        return None
    return decoder.reg_name(register)


def find_member_xrefs(
    pe: pefile.PE,
    offsets: set[int],
    functions: list[RuntimeFunction],
    starts: list[int],
) -> dict[str, list[dict[str, object]]]:
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    matches = {f"0x{offset:X}": [] for offset in sorted(offsets)}
    decoder = disassembler()

    for section_rva, data in executable_sections(pe):
        for instruction in decoder.disasm(data, image_base + section_rva):
            if instruction.id == 0:
                continue
            site_rva = instruction.address - image_base
            owner = containing_function(functions, starts, site_rva)
            for operand_index, operand in enumerate(instruction.operands):
                if operand.type != CS_OP_MEM or operand.mem.disp not in offsets:
                    continue
                base = register_name(decoder, operand.mem.base)
                index = register_name(decoder, operand.mem.index)
                matches[f"0x{operand.mem.disp:X}"].append(
                    {
                        "site_rva": f"0x{site_rva:X}",
                        "function": (
                            {
                                "begin_rva": f"0x{owner.begin_rva:X}",
                                "end_rva": f"0x{owner.end_rva:X}",
                            }
                            if owner is not None
                            else None
                        ),
                        "mnemonic": instruction.mnemonic,
                        "operands": instruction.op_str,
                        "memory_operand_index": operand_index,
                        "base_register": base,
                        "index_register": index,
                        "scale": int(operand.mem.scale),
                        "rip_relative": operand.mem.base == X86_REG_RIP,
                    }
                )
    return matches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find x64 memory operands that use selected object-member displacements "
            "in executable PE sections without modifying or loading the binary."
        )
    )
    parser.add_argument("binary", type=Path)
    parser.add_argument("--offset", type=parse_int, action="append", required=True)
    parser.add_argument(
        "--expect-sha256",
        help="refuse analysis unless the file matches this SHA-256",
    )
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"binary does not exist: {args.binary}")
    if any(offset < 0 for offset in args.offset):
        parser.error("--offset values must be non-negative")

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
    offsets = set(args.offset)

    print(
        json.dumps(
            {
                "file": args.binary.name,
                "sha256": actual_hash,
                "image_base": f"0x{int(pe.OPTIONAL_HEADER.ImageBase):X}",
                "runtime_function_count": len(functions),
                "offsets": find_member_xrefs(pe, offsets, functions, starts),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

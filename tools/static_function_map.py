from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, CS_OP_IMM, CS_OP_MEM, Cs
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


def imported_symbols(pe: pefile.PE) -> dict[int, str]:
    symbols: dict[int, str] = {}
    for module in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        module_name = module.dll.decode("ascii", errors="replace")
        for item in module.imports:
            name = (
                item.name.decode("ascii", errors="replace")
                if item.name
                else f"ordinal:{item.ordinal}"
            )
            symbols[int(item.address)] = f"{module_name}!{name}"
    return symbols


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


def call_target(instruction: object, imports: dict[int, str]) -> dict[str, str] | None:
    if instruction.mnemonic not in {"call", "jmp"} or not instruction.operands:
        return None
    operand = instruction.operands[0]
    if operand.type == CS_OP_IMM:
        return {"virtual_address": f"0x{operand.imm:X}"}
    if operand.type == CS_OP_MEM and operand.mem.base == X86_REG_RIP:
        slot = instruction.address + instruction.size + operand.mem.disp
        result = {"indirect_slot": f"0x{slot:X}"}
        if slot in imports:
            result["import"] = imports[slot]
        return result
    return None


def function_instructions(
    pe: pefile.PE, function: RuntimeFunction, image_base: int
) -> list[object]:
    offset = pe.get_offset_from_rva(function.begin_rva)
    size = function.end_rva - function.begin_rva
    data = pe.__data__[offset : offset + size]
    return list(disassembler().disasm(data, image_base + function.begin_rva))


def map_target(
    pe: pefile.PE,
    target_rva: int,
    functions: list[RuntimeFunction],
    starts: list[int],
    imports: dict[int, str],
    sections: list[tuple[int, bytes]],
) -> dict[str, object]:
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    function = containing_function(functions, starts, target_rva)
    if function is None:
        return {"target_rva": f"0x{target_rva:X}", "function": None}

    calls = []
    for instruction in function_instructions(pe, function, image_base):
        target = call_target(instruction, imports)
        if target is None:
            continue
        if "virtual_address" in target:
            address = int(target["virtual_address"], 16)
            target["rva"] = f"0x{address - image_base:X}" if address >= image_base else None
        calls.append(
            {
                "site_rva": f"0x{instruction.address - image_base:X}",
                "mnemonic": instruction.mnemonic,
                "target": target,
            }
        )

    callers = []
    function_va = image_base + function.begin_rva
    decoder = disassembler()
    for section_rva, data in sections:
        for instruction in decoder.disasm(data, image_base + section_rva):
            if instruction.mnemonic not in {"call", "jmp"} or not instruction.operands:
                continue
            operand = instruction.operands[0]
            if operand.type != CS_OP_IMM or operand.imm != function_va:
                continue
            caller_rva = instruction.address - image_base
            owner = containing_function(functions, starts, caller_rva)
            callers.append(
                {
                    "site_rva": f"0x{caller_rva:X}",
                    "mnemonic": instruction.mnemonic,
                    "function_rva": (
                        f"0x{owner.begin_rva:X}" if owner is not None else None
                    ),
                }
            )

    return {
        "target_rva": f"0x{target_rva:X}",
        "function": {
            "begin_rva": f"0x{function.begin_rva:X}",
            "end_rva": f"0x{function.end_rva:X}",
            "unwind_rva": f"0x{function.unwind_rva:X}",
            "callers": callers,
            "calls": calls,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Map x64 PE functions, direct callers, and call targets from disk "
            "without changing or loading the binary into another process."
        )
    )
    parser.add_argument("binary", type=Path)
    parser.add_argument(
        "--target-rva", type=parse_int, action="append", required=True
    )
    parser.add_argument(
        "--expect-sha256",
        help="refuse analysis unless the file matches this SHA-256",
    )
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"binary does not exist: {args.binary}")

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
    imports = imported_symbols(pe)
    sections = executable_sections(pe)

    print(
        json.dumps(
            {
                "file": args.binary.name,
                "sha256": actual_hash,
                "image_base": f"0x{int(pe.OPTIONAL_HEADER.ImageBase):X}",
                "runtime_function_count": len(functions),
                "targets": [
                    map_target(
                        pe,
                        target_rva,
                        functions,
                        starts,
                        imports,
                        sections,
                    )
                    for target_rva in args.target_rva
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


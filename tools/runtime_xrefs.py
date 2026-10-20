from __future__ import annotations

import argparse
import ctypes
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, CS_OP_IMM, Cs


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
IMAGE_SCN_MEM_EXECUTE = 0x20000000


def parse_int(value: str) -> int:
    return int(value, 0)


class ProcessReader:
    def __init__(self, pid: int) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_uint32,
        ]
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.ReadProcessMemory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.kernel32.ReadProcessMemory.restype = ctypes.c_bool
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
        )
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def read(self, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        if not self.kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(bytes_read),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw[: bytes_read.value]

    def close(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "ProcessReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find direct runtime call/jump references to module RVAs."
    )
    parser.add_argument("binary", type=Path)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--runtime-base", type=parse_int, required=True)
    parser.add_argument("--target-rva", type=parse_int, action="append", required=True)
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"binary does not exist: {args.binary}")
    if args.pid <= 0 or args.runtime_base <= 0:
        parser.error("--pid and --runtime-base must be positive")

    pe = pefile.PE(str(args.binary), fast_load=True)
    targets = {args.runtime_base + rva: rva for rva in args.target_rva}
    disassembler = Cs(CS_ARCH_X86, CS_MODE_64)
    disassembler.detail = True
    disassembler.skipdata = True

    matches = 0
    with ProcessReader(args.pid) as process:
        for section in pe.sections:
            if not section.Characteristics & IMAGE_SCN_MEM_EXECUTE:
                continue
            rva = int(section.VirtualAddress)
            size = max(int(section.Misc_VirtualSize), int(section.SizeOfRawData))
            data = process.read(args.runtime_base + rva, size)
            for instruction in disassembler.disasm(data, args.runtime_base + rva):
                if instruction.mnemonic not in {"call", "jmp"}:
                    continue
                if not instruction.operands or instruction.operands[0].type != CS_OP_IMM:
                    continue
                target = instruction.operands[0].imm
                if target not in targets:
                    continue
                matches += 1
                print(
                    f"target_rva=0x{targets[target]:X} "
                    f"xref=0x{instruction.address:X} "
                    f"xref_rva=0x{instruction.address - args.runtime_base:X} "
                    f"{instruction.mnemonic} {instruction.op_str}"
                )
    if matches == 0:
        print("no direct runtime references found")


if __name__ == "__main__":
    main()

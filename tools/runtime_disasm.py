from __future__ import annotations

import argparse
import ctypes
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400


def parse_int(value: str) -> int:
    return int(value, 0)


def read_process_memory(pid: int, address: int, size: int) -> bytes:
    if pid <= 0 or address <= 0 or size <= 0:
        raise ValueError("pid, address, and size must be positive")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.ReadProcessMemory.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    process = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(
            process,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(bytes_read),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if bytes_read.value == 0:
            raise RuntimeError("ReadProcessMemory returned no data")
        return buffer.raw[: bytes_read.value]
    finally:
        kernel32.CloseHandle(process)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Disassemble a PE region from disk or a live process."
    )
    parser.add_argument("binary", type=Path)
    parser.add_argument("--rva", type=parse_int, required=True)
    parser.add_argument("--before", type=parse_int, default=0x100)
    parser.add_argument("--after", type=parse_int, default=0x180)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--runtime-base", type=parse_int)
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"binary does not exist: {args.binary}")
    if args.before < 0 or args.after <= 0:
        parser.error("--before must be non-negative and --after must be positive")
    if (args.pid is None) != (args.runtime_base is None):
        parser.error("--pid and --runtime-base must be supplied together")

    pe = pefile.PE(str(args.binary), fast_load=True)
    preferred_base = int(pe.OPTIONAL_HEADER.ImageBase)
    start_rva = max(0, args.rva - args.before)
    size = args.before + args.after

    if args.pid is not None:
        base = args.runtime_base
        data = read_process_memory(args.pid, base + start_rva, size)
        source = "runtime"
    else:
        base = preferred_base
        offset = pe.get_offset_from_rva(start_rva)
        with args.binary.open("rb") as source_file:
            source_file.seek(offset)
            data = source_file.read(size)
        source = "file"

    target = base + args.rva
    disassembler = Cs(CS_ARCH_X86, CS_MODE_64)
    disassembler.skipdata = True

    print(
        f"preferred_base=0x{preferred_base:X} runtime_base=0x{base:X} "
        f"target=0x{target:X} bytes={len(data)} source={source}"
    )
    for instruction in disassembler.disasm(data, base + start_rva):
        marker = "=>" if instruction.address == target else "  "
        print(
            f"{marker} 0x{instruction.address:016X} "
            f"{instruction.mnemonic:<8} {instruction.op_str}"
        )


if __name__ == "__main__":
    main()

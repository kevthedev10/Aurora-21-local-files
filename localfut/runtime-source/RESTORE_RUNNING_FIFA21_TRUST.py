from __future__ import annotations
import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME_EXE = "FIFA21.exe"

PAIRS = [
    ("gos2015-original-modulus.bin", "local-gos2015-modulus.bin", "GOS 2015 Certificate Authority"),
    ("gs2019-original-modulus.bin", "local-gs2019-modulus.bin", "Gameplay Services 2019 Certificate Authority"),
]

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020

MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_EXECUTE_READWRITE = 0x40

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.VirtualQueryEx.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
kernel32.WriteProcessMemory.restype = wt.BOOL
kernel32.VirtualProtectEx.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(wt.DWORD)
]
kernel32.VirtualProtectEx.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


def die(msg: str, code: int = 1):
    print("[ERROR]", msg)
    input("Press Enter to close...")
    raise SystemExit(code)


def find_game_pid() -> int:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        die("Could not enumerate processes.")
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if pe.szExeFile.lower() == GAME_EXE.lower():
                return int(pe.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    die(f"{GAME_EXE} is not running.")


def read_region(hproc, base: int, size: int) -> bytes | None:
    if size <= 0:
        return None
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        hproc, ctypes.c_void_p(base), buf, size, ctypes.byref(got)
    )
    if not ok or got.value == 0:
        return None
    return buf.raw[: got.value]


def write_exact(hproc, address: int, old: bytes, new: bytes) -> bool:
    current = read_region(hproc, address, len(old))
    if current != old:
        return False

    old_protect = wt.DWORD(0)
    changed = kernel32.VirtualProtectEx(
        hproc, ctypes.c_void_p(address), len(new),
        PAGE_EXECUTE_READWRITE, ctypes.byref(old_protect)
    )
    if not changed:
        return False

    try:
        buf = ctypes.create_string_buffer(new)
        written = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(
            hproc, ctypes.c_void_p(address), buf, len(new), ctypes.byref(written)
        )
        if not ok or written.value != len(new):
            return False
    finally:
        tmp = wt.DWORD(0)
        kernel32.VirtualProtectEx(
            hproc, ctypes.c_void_p(address), len(new),
            old_protect.value, ctypes.byref(tmp)
        )

    verify = read_region(hproc, address, len(new))
    return verify == new


def scan_exact(hproc, needle: bytes):
    matches = []
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    MAX_ADDR = 0x00007FFFFFFFFFFF
    CHUNK = 16 * 1024 * 1024
    overlap = len(needle) - 1

    while addr < MAX_ADDR:
        n = kernel32.VirtualQueryEx(
            hproc, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if not n:
            break

        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        protect = int(mbi.Protect)

        readable = (
            mbi.State == MEM_COMMIT
            and not (protect & PAGE_GUARD)
            and protect != PAGE_NOACCESS
        )

        if readable and size:
            off = 0
            carry = b""
            while off < size:
                want = min(CHUNK, size - off)
                block = read_region(hproc, base + off, want)
                if block is None:
                    break
                data = carry + block
                search_from = 0
                while True:
                    idx = data.find(needle, search_from)
                    if idx < 0:
                        break
                    absolute = base + off - len(carry) + idx
                    if absolute not in matches:
                        matches.append(absolute)
                    search_from = idx + 1
                carry = data[-overlap:] if len(data) >= overlap else data
                off += len(block)
                if len(block) < want:
                    break

        next_addr = base + size
        if next_addr <= addr:
            break
        addr = next_addr

    return matches


def main():
    pairs = []
    for orig_name, local_name, label in PAIRS:
        orig = (ROOT / orig_name).read_bytes() if (ROOT / orig_name).exists() else None
        local = (ROOT / local_name).read_bytes() if (ROOT / local_name).exists() else None
        if orig is None or local is None:
            print(f"[SKIP] {label}: missing {orig_name} or {local_name}.")
            continue
        pairs.append((orig, local, label))

    if not pairs:
        die("No restore pairs available.")

    print("FIFA 21 localhost trust bridge - RESTORE")
    print("========================================")
    pid = find_game_pid()
    print("[INFO] FIFA21.exe PID:", pid)

    access = (
        PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
        PROCESS_VM_READ | PROCESS_VM_WRITE
    )
    hproc = kernel32.OpenProcess(access, False, pid)
    if not hproc:
        die(
            "OpenProcess failed. Run this script as Administrator, "
            "and make sure FIFA21.exe is still running."
        )

    try:
        total_restored = 0
        for orig, local, label in pairs:
            print(f"[INFO] Scanning for the local {label} modulus...")
            matches = scan_exact(hproc, local)
            print(f"[INFO] Exact local copies found: {len(matches)}")
            for a in matches:
                print(f"       0x{a:016X}")

            restored = 0
            for a in matches:
                if write_exact(hproc, a, local, orig):
                    restored += 1
                    print(f"[RESTORED] 0x{a:016X} ({label})")
                else:
                    print(f"[FAILED]  0x{a:016X}")
            total_restored += restored

        if not total_restored:
            die("No local copies were found. Nothing was changed.")

        print()
        print(f"[SUCCESS] Restored {total_restored} copy/copies in the running FIFA21.exe.")
        print("No file on disk was modified.")
        input("Press Enter to close this window...")
    finally:
        kernel32.CloseHandle(hproc)


if __name__ == "__main__":
    main()

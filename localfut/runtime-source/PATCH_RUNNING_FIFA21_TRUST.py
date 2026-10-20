from __future__ import annotations
import ctypes
import ctypes.wintypes as wt
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME_EXES = {"FIFA21.exe", "FIFA21_Trial.exe"}

# FIFA 21's GOS 2015 trust anchor uses a 2048-bit RSA modulus, so replacing
# that one field is sufficient for a process-local development CA bridge.
#
# Do not add the existing gs2019-*-modulus.bin files here. The GS 2019 trust
# anchor in this build uses a 65-byte EC public point. Those 256-byte files
# extend into adjacent ProtoSSL constants and writing them corrupts TLS state.
# (original modulus file, local modulus file, label)
PAIRS = [
    ("gos2015-original-modulus.bin", "local-gos2015-modulus.bin", "GOS 2015 Certificate Authority"),
]

# OriginSDK normally requires a matching Origin/EA Desktop process before it
# connects to LSX on 127.0.0.1:3216. Local replacement mode takes the same
# continuation used when that process check succeeds, without starting or
# depending on EA Desktop. The full test/branch context guards this build.
ORIGIN_CLIENT_BOOTSTRAP_ORIGINAL = bytes.fromhex(
    "85 F6 75 67 41 8B 87 68 04 00 00"
)
ORIGIN_CLIENT_BOOTSTRAP_PATCHED = bytes.fromhex(
    "EB 69 90 90 41 8B 87 68 04 00 00"
)
ORIGIN_CLIENT_BOOTSTRAP_LABEL = "Origin client bootstrap bypass"

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


def find_game_pid(requested_pid: int | None = None) -> int:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        die("Could not enumerate processes.")
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            name_matches = pe.szExeFile.lower() in {name.lower() for name in GAME_EXES}
            pid_matches = requested_pid is None or int(pe.th32ProcessID) == requested_pid
            if name_matches and pid_matches:
                return int(pe.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    if requested_pid is not None:
        die(f"The requested FIFA 21 process PID {requested_pid} is no longer running.")
    die("FIFA21.exe or FIFA21_Trial.exe is not running. Launch FIFA 21 and stay at the main menu first.")


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
    if current == new:
        return True
    if current != old:
        current_hash = hashlib.sha256(current or b"").hexdigest()[:16]
        old_hash = hashlib.sha256(old).hexdigest()[:16]
        print(
            f"[SKIP] 0x{address:016X}: bytes changed before write "
            f"current_sha256={current_hash} expected_sha256={old_hash}"
        )
        return False

    for attempt in range(1, 4):
        old_protect = wt.DWORD(0)
        changed = kernel32.VirtualProtectEx(
            hproc, ctypes.c_void_p(address), len(new),
            PAGE_EXECUTE_READWRITE, ctypes.byref(old_protect)
        )
        if not changed:
            error = ctypes.get_last_error()
            print(
                f"[WRITE ERROR] 0x{address:016X}: VirtualProtectEx failed "
                f"attempt={attempt}/3 winerr={error}"
            )
            time.sleep(0.05)
            continue

        try:
            buf = ctypes.create_string_buffer(new)
            written = ctypes.c_size_t(0)
            ok = kernel32.WriteProcessMemory(
                hproc, ctypes.c_void_p(address), buf, len(new), ctypes.byref(written)
            )
            error = ctypes.get_last_error()
        finally:
            tmp = wt.DWORD(0)
            kernel32.VirtualProtectEx(
                hproc, ctypes.c_void_p(address), len(new),
                old_protect.value, ctypes.byref(tmp)
            )

        verify = read_region(hproc, address, len(new))
        if ok and written.value == len(new) and verify == new:
            return True
        print(
            f"[WRITE ERROR] 0x{address:016X}: WriteProcessMemory failed "
            f"attempt={attempt}/3 winerr={error} written={written.value}/{len(new)} "
            f"verified={verify == new}"
        )
        time.sleep(0.05)

    return False


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
    only = None
    requested_pid = None
    skip_origin = "--skip-origin" in sys.argv
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        if i + 1 < len(sys.argv):
            only = sys.argv[i + 1]
    if "--pid" in sys.argv:
        i = sys.argv.index("--pid")
        if i + 1 >= len(sys.argv):
            die("--pid requires a FIFA 21 process ID.")
        try:
            requested_pid = int(sys.argv[i + 1])
        except ValueError:
            die("--pid must be a numeric FIFA 21 process ID.")
        if requested_pid <= 0:
            die("--pid must be a positive FIFA 21 process ID.")

    pairs = []
    for orig_name, local_name, label in PAIRS:
        if only and only.lower() not in label.lower():
            continue
        orig = (ROOT / orig_name).read_bytes() if (ROOT / orig_name).exists() else None
        local = (ROOT / local_name).read_bytes() if (ROOT / local_name).exists() else None
        if orig is None or local is None:
            print(f"[SKIP] {label}: missing {orig_name} or {local_name}. Run GENERATE_LOCAL_CERT.cmd first.")
            continue
        if len(orig) != 256 or len(local) != 256:
            print(f"[SKIP] {label}: modulus files must be exactly 256 bytes.")
            continue
        if orig == local:
            print(f"[SKIP] {label}: local modulus equals the original (cert not regenerated?).")
            continue
        pairs.append((orig, local, label))

    if not pairs:
        die("No patchable CA pairs are available.")

    print("FIFA 21 localhost trust bridge")
    print("==============================")
    print("Scope: running FIFA 21 process only")
    print("No file on disk is modified; the patch vanishes when the game exits.")
    for _, _, label in pairs:
        print(f"  target: {label}")
    print()

    pid = find_game_pid(requested_pid)
    print("[INFO] FIFA 21 process PID:", pid)

    access = (
        PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
        PROCESS_VM_READ | PROCESS_VM_WRITE
    )
    hproc = kernel32.OpenProcess(access, False, pid)
    if not hproc:
        die(
            "OpenProcess failed. Run this script as Administrator, "
            "and make sure FIFA 21 is still running."
        )

    try:
        total_patched = 0
        for orig, local, label in pairs:
            print(f"[INFO] Scanning for the {label} modulus...")
            matches = scan_exact(hproc, orig)
            already = scan_exact(hproc, local)
            print(f"[INFO] Exact original copies found: {len(matches)}")
            for a in matches:
                print(f"       0x{a:016X}")
            print(f"[INFO] Already-patched local copies found: {len(already)}")
            for a in already:
                print(f"       0x{a:016X}")

            patched = 0
            for a in matches:
                if write_exact(hproc, a, orig, local):
                    patched += 1
                    print(f"[PATCHED] 0x{a:016X} ({label})")
                else:
                    print(f"[FAILED]  0x{a:016X}")
            total_patched += patched + len(already)
            if already:
                print(f"[ALREADY] {len(already)} copy/copies ({label})")

        if skip_origin:
            print(f"[SKIP] {ORIGIN_CLIENT_BOOTSTRAP_LABEL}: EA App session mode")
        else:
            print(f"[INFO] Scanning for the {ORIGIN_CLIENT_BOOTSTRAP_LABEL}...")
            matches = scan_exact(hproc, ORIGIN_CLIENT_BOOTSTRAP_ORIGINAL)
            already = scan_exact(hproc, ORIGIN_CLIENT_BOOTSTRAP_PATCHED)
            print(f"[INFO] Exact original copies found: {len(matches)}")
            for a in matches:
                print(f"       0x{a:016X}")
            print(f"[INFO] Already-patched copies found: {len(already)}")
            for a in already:
                print(f"       0x{a:016X}")

            patched = 0
            for a in matches:
                if write_exact(
                    hproc,
                    a,
                    ORIGIN_CLIENT_BOOTSTRAP_ORIGINAL,
                    ORIGIN_CLIENT_BOOTSTRAP_PATCHED,
                ):
                    patched += 1
                    print(f"[PATCHED] 0x{a:016X} ({ORIGIN_CLIENT_BOOTSTRAP_LABEL})")
                else:
                    print(f"[FAILED]  0x{a:016X}")
            total_patched += patched + len(already)
            if already:
                print(f"[ALREADY] {len(already)} copy/copies ({ORIGIN_CLIENT_BOOTSTRAP_LABEL})")

        if not total_patched:
            die("The patch targets were found but no copy could be patched.")

        print()
        print(f"[SUCCESS] Patched {total_patched} copy/copies in the running FIFA 21 process.")
        print("No file on disk was modified.")
        print()
        print("NEXT:")
        print("  1. Leave FIFA 21 open.")
        print("  2. Install the hosts redirect (spring18.gosredirector.ea.com -> 127.0.0.1).")
        print("  3. Start the local FUT 21 runtime.")
        print("  4. Click Ultimate Team once.")
        print()
        print("If FIFA 21 exits, the in-memory patch disappears automatically.")
        input("Press Enter to close this bridge window...")
    finally:
        kernel32.CloseHandle(hproc)


if __name__ == "__main__":
    main()


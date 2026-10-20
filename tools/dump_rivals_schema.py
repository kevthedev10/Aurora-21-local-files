"""
Read-only dump of the RS4 response deserializer / field list for the FIFA 21
Division Rivals + FUT Champions hub responses in CardsDLL_Win64_retail.dll.

Run with Binary Ninja's Python (headless license) OR paste into the BN
Scripting console after opening the DLL (then set `bv` to the open view):

    py -3 tools\\dump_rivals_schema.py "C:\\Program Files\\EA Games\\FIFA 21\\CardsDLL_Win64_retail.dll"

Paste the whole stdout back. Writes nothing.
"""
import sys

CLASSES = [
    "RS4:FutGetRivalsHubServerResponse",
    "RS4:FutRivalsSetDataServerResponse",
    "RS4:FutRivalsSelectRewardServerResponse",
    "RS4:FutGetChampionsTopXServerResponse",
    "RS4:FutGrantPrizeChampionUserServerResponse",
    "RS4:FutSeasonListServerResponse",
    "RS4:FutGetSquadBattleHubServerResponse",   # known-working, for comparison
]


def strings_in_func(bv, func):
    """Every printable string a function references, in first-seen order."""
    out, seen = [], set()

    def add(val):
        try:
            s = bv.get_string_at(val)
        except Exception:
            s = None
        if s is None:
            # maybe it's a pointer to a char* (RS4 name tables hold char**)
            try:
                deref = int.from_bytes(bv.read(val, bv.address_size), "little")
                s = bv.get_string_at(deref)
            except Exception:
                s = None
        if s and 2 <= len(s.value) <= 96 and s.value.isprintable():
            if s.value not in seen:
                seen.add(s.value)
                out.append(s.value)

    for il in func.llil_instructions:
        for tok in il.tokens:
            v = getattr(tok, "value", None)
            if isinstance(v, int) and v > 0x1000:
                add(v)
    # also raw disassembly tokens (catches lea rax,[rip+x] data refs)
    for toks, _ln in func.instructions:
        for tok in toks:
            v = getattr(tok, "value", None)
            if isinstance(v, int) and v > 0x1000:
                add(v)
    return out


def walk(bv, func, tag, depth, seen):
    if func.start in seen or depth > 3:
        return
    seen.add(func.start)
    ind = "  " * depth
    ni = len(list(func.instructions))
    print(f"{ind}- {tag} {func.start:#x} '{func.name}' ({ni} insns)")
    ss = strings_in_func(bv, func)
    if ss:
        print(f"{ind}    strings ({len(ss)}):")
        for x in ss:
            print(f"{ind}      {x!r}")
    if depth < 3:
        for c in func.callees:
            walk(bv, c, "callee", depth + 1, seen)


def main():
    bv = globals().get("bv")  # reuse the open view if run from the BN console
    if bv is None:
        try:
            import binaryninja as bn
        except Exception as e:
            print("no binaryninja module:", e)
            return
        dll = sys.argv[1] if len(sys.argv) > 1 else \
            r"C:\Program Files\EA Games\FIFA 21\CardsDLL_Win64_retail.dll"
        print("[*] loading", dll)
        bv = bn.load(dll, update_analysis=True)
    print("[*]", len(list(bv.functions)), "functions\n")

    by_val = {s.value: s for s in bv.strings}
    psize = bv.address_size

    for cls in CLASSES:
        print("=" * 78)
        print("CLASS:", cls)
        st = by_val.get(cls)
        if not st:
            print("  string not found")
            continue
        print(f"  @ {st.start:#x}")
        refs = list(bv.get_code_refs(st.start))
        print(f"  {len(refs)} code xref(s)")
        for r in refs:
            if r.function:
                walk(bv, r.function, "xref", 0, set())

        # vtable candidates: run of code pointers just before the class string
        vt = []
        a = st.start - psize
        for _ in range(48):
            v = int.from_bytes(bv.read(a, psize) or b"", "little")
            f = bv.get_function_at(v)
            if not f:
                break
            vt.append((a, v))
            a -= psize
        vt.reverse()
        if vt:
            print(f"  vtable-ish: {len(vt)} code ptrs before string")
            big = max(vt, key=lambda t: len(list(bv.get_function_at(t[1]).instructions)))
            for a, v in vt:
                f = bv.get_function_at(v)
                mark = " <== biggest" if v == big[1] else ""
                print(f"    [{a:#x}] {v:#x} {f.name} "
                      f"({len(list(f.instructions))} insns){mark}")
            print("  >>> dumping biggest vtable fn:")
            walk(bv, bv.get_function_at(big[1]), "vtable", 0, set())

    print("\n[*] done")


main()

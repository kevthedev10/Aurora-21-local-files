# Dynamic-analysis playbook

This procedure observes local EA Desktop IPC and the game-side parser. It does not patch files or bypass entitlement, activation, anti-cheat, TLS, certificates, or signatures.

## Preparation

1. Verify local module hashes against `findings.md` or record a new build map.
2. Launch FIFA 21 normally through EA App and reach the main menu.
3. Attach x64dbg only after the menu appears.
4. Record the runtime base of `dbdata.dll` from the module map.
5. Convert every documented RVA to `runtime base + RVA`. Do not reuse absolute addresses from another run.
6. Close unrelated personal applications before capturing memory and redact all values before sharing evidence.

## x64dbg breakpoint hygiene

The tested x64dbg configuration assigns `Ctrl+Return` to the Command box. If an attached session inherited noisy TLS-callback breakpoints, first review the Breakpoints tab. The command `bc` with no address deletes every software breakpoint, including project breakpoints. Use it only when the complete list is understood, then re-add the scoped module-relative breakpoints before resuming.

Use Run with pass-exceptions behavior (`Shift+F9` in the tested configuration) for unrelated startup exceptions. Do not suppress or modify game logic.

## Validated parser-to-dispatch trace

The following sequence is now validated for the recorded `dbdata.dll` hash:

```text
+0x402A0   framed-message consumer entry
+0x40417   common point after output creation
+0xA1AC    successful return from the first consumer call
+0xA26F    source selection before queue-slot string copy
+0xA27D    after incoming-queue count increment
+0x9FA0    incoming parsed-string queue consumer
+0xA093    consumer boolean return
+0x6C29    XML dispatcher after successful dequeue
+0x6DDE    handler lookup call site
+0x6DFC    first resolved handler virtual call
+0x32A75   concrete presence handler owner callback
+0x6E07    handler follow-up virtual call
```

At `+0x402A0`, record only the receive object shape, caller output object, selector at `RCX+0x108`, transform parameter at `RCX+0x10C`, and ring-buffer cursors at `RCX+0x110`. At `+0x40417`, inspect the same output object and retain only message type, tag names, field names, lengths, and routing information.

The validated live frame used the transformed branch and produced a 209-byte XML `GetPresenceResponse`. The drain stage copied it into owner queue `+0x2F0`; `+0x9FA0` dequeued it; `+0x6BC0` parsed it and resolved `Origin::EventHandler<lsx::GetPresenceResponseT, OriginPresenceT>::HandleMessage` at `+0x32A10`.

## Presence callback outcome

The first three narrower points below were hit by a fresh user-generated event. The final two indirect callback sites were armed but not reached:

```text
+0x32A9E   copy parsed OriginPresenceT into handler member +0x70
+0x32AA3   immediately after that copy
+0x2AA20   concrete presence callback-dispatch entry
+0x2D1E2   indirect registered game callback
+0x2D24C   indirect fallback callback
```

At `+0x32A9E`, compare only the destination object shape before and after the copy. Do not publish presence values. The live event reached `+0x2AA20`, but the primary handler's callback-vector begin/end at `+0x10`/`+0x18` and fallback record at `+0x28`/`+0x30` were all null before and after dispatch. Do not continue waiting on `+0x2D1E2` or `+0x2D24C` for this handler.

## `StubFifaOnline` retry outcome

`powdll_Win64_retail.dll + 0x2759` resolves `StubFifaOnline`; plugin global `+0xEBF8` holds the returned FIFA21.exe function table. The corrected selected-reader mapping for the recorded main executable build is:

```text
FIFA21.exe + 0xA295B0   composite readiness getter
FIFA21.exe + 0xA29790   dword state getter (+0x1DC8)
FIFA21.exe + 0xA29870   boolean state getter (+0x1DC6)
FIFA21.exe + 0xA29910   table slot +0x108; per-index boolean getter (+0x140 + index*0x148)
```

One user-generated retry with software breakpoints on all four entries produced no hits before the connection error. This set is retained only as historical evidence and for independent comparison with another build or materially different session; do not repeat it in the current build/session.

A later whole-section execute watch hit powdll `+0x2A60`, a zero-return `ActionRunning` stub called continuously from FIFA21.exe return site `+0x4BE4A55` at about 120 events per second. This is background polling. After filtering it, the next pause was powdll's PE entrypoint `+0x3B1C` on a newly created thread with an `ntdll` return, followed by entrypoint instructions through `+0x3B2B`. Treat these as loader lifecycle, not online-state decisions.

Do not use another execute `bpmrange` across the complete powdll `.text` section: page-guard re-entry can repeatedly pause inside `DllMain` and make FIFA appear to have crashed. Clear such a watch with `bpmc` after focusing the command box, then resume; the tested game recovered normally.

## Completed table-wide and loader-boundary traces

The expanded read-only table map supported this second narrow software-breakpoint set:

```text
FIFA21.exe + 0xA294B0   null-checked service-chain wrapper
FIFA21.exe + 0xA299E0   locked comparison: state dword +0x1DC0 equals 0x16
FIFA21.exe + 0xA29A80   locked dword reader at +0x1DA8
FIFA21.exe + 0xA29AD0   virtual slot +0x10 forwarder
```

This set is retained as historical evidence. It has already been tested; do not re-arm it for the current build/session. Do not return to a whole-section page watch or alter state values to force a successful result.

The four-entry set above was tested in one user-generated retry and produced no hits before the connection error. FIFA and x64dbg remained responsive. The two selected pointer readers at `+0xA299A0` and `+0xA29AC0` were tested separately and were also negative.

The two pointer readers were also tested in a later retry and produced no hits. A final sweep then covered all 47 non-null live table entries with ordinary software breakpoints whose log text was enabled and whose break condition was false. After clearing the log, a user-generated Ultimate Team retry produced no table-entry events before the connection error. Stop adding breakpoints anywhere in this `StubFifaOnline` table for the current build/session.

The next completed experiment placed one non-pausing software breakpoint on `ntdll.dll!LdrLoadDll`, logging the requested Unicode DLL name and caller. After another clean log and user-generated retry, no invocation of that loader boundary was observed before the connection error. `CardsDLL_Win64_retail.dll` remained unloaded. The loader breakpoint was removed after this test; repeating it is useful only for another build or a materially different transition.

## Recommended handoff trace

Investigate the main-executable callback for selecting Ultimate Team and the controller path that creates or presents the connection error as an alternative active path. Neither the live `StubFifaOnline` table nor the instrumented `LdrLoadDll` boundary produced a retry event, but that does not establish temporal ordering for the unused table. Use a small number of ordinary software breakpoints at validated caller boundaries, and retain only module-relative addresses, boolean/status results, and object-member offsets.

Do not assume a direct main-executable xref to the DLL filename: a read-only scan found no exact ASCII or UTF-16LE `CardsDLL_Win64_retail.dll` literal in this `FIFA21.exe` build. Prefer the feature/module registry, indirect component identifier, or transition state that precedes `LdrLoadDll`. Passive Process Monitor correlation of configuration and registry reads immediately before the error is an acceptable fallback. Do not patch a branch, force a success status, or reintroduce whole-section memory breakpoints.

`tools/static_member_xrefs.py` can narrow exact owner-member uses before placing a breakpoint. Example:

```powershell
python tools\static_member_xrefs.py `
  "C:\Program Files\EA Games\FIFA 21\dbdata.dll" `
  --expect-sha256 92612A66F7A3658B74246A0277662DC45FDD6E5F1F24F8F6D7B1D3CDEDCD6C89 `
  --offset 0x2F0 --offset 0x340
```

Resolve a runtime-captured vtable without loading the module:

```powershell
python tools\static_vtable_map.py `
  "C:\Program Files\EA Games\FIFA 21\dbdata.dll" `
  --expect-sha256 92612A66F7A3658B74246A0277662DC45FDD6E5F1F24F8F6D7B1D3CDEDCD6C89 `
  --rva 0xABB10 --entries 12
```

Find code references to a selected data RVA such as a factory-name string or global slot:

```powershell
python tools\static_rva_xrefs.py `
  "C:\Program Files\EA Games\FIFA 21\powdll_Win64_retail.dll" `
  --expect-sha256 6FA167DCA64BD78FD0E3961C253D48668961CE3D33AC8567D77E9D1FF3615885 `
  --target-rva 0x5498 --target-rva 0xEBF8
```

## Fallback receive-boundary trace

If the consumer never fires, set a temporary breakpoint on `ws2_32!recv`. On the first relevant hit:

1. Record the socket, destination pointer, requested length, and return address.
2. Replace the broad API breakpoint with a breakpoint at that single return address.
3. Remove the broad `recv` breakpoint immediately to avoid freezing on unrelated traffic.
4. Resume to the return address and record the byte count from `RAX`.
5. Trace only into the owning local module.

The validated run returned to `dbdata.dll + 0x4052C` with `RAX=0xA1`.

## Passive fallback

If x64dbg is blocked or unstable, use Process Monitor for local file and registry telemetry, record process modules and loopback connections with read-only PowerShell commands, and inspect EA Desktop logs only for command names and timestamps. Do not publish raw logs because they can contain account identifiers.

## Stop conditions

Stop the trace if it enters `Activation64.dll`, an anti-cheat component, entitlement validation, certificate validation, TLS internals, or a non-loopback EA service. Record the boundary and choose a different interoperability seam.

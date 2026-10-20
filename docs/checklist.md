# Project checklist

Last updated: 25 August 2026.

This checklist is the operational view of the roadmap. A checked item means the result has either been validated against the recorded module hashes or covered by deterministic tests. It does not mean FIFA 21 can connect to the local service yet.

## Completed research and safety baseline

- [x] Record SHA-256 hashes for the target FIFA 21 modules.
- [x] Confirm FIFA 21 reaches its normal offline main menu after retired-service failure.
- [x] Confirm the observed early game traffic is loopback IPC to EA Desktop.
- [x] Establish and document legal and technical stop conditions.
- [x] Keep EA binaries, payload values, credentials, account data, and former server content out of Git.
- [x] Keep activation, entitlement, anti-cheat, TLS, certificates, signatures, and encryption bypass out of scope.

## Completed `dbdata.dll` receive-path work

- [x] Identify receive loop `+0x40480` and tested return point `+0x4052C`.
- [x] Identify framed-message consumer `+0x402A0` and common output point `+0x40417`.
- [x] Validate a live transformed frame without publishing payload values.
- [x] Identify the frame as XML `GetPresenceResponse` using tag and field names only.
- [x] Validate drain/enqueue stage `+0xA0B0` and incoming queue at owner offset `+0x2F0`.
- [x] Validate queue consumer `+0x9FA0` and pop helper `+0xD010`.
- [x] Validate XML dispatcher `+0x6BC0`.
- [x] Resolve the live handler to `Origin::EventHandler<lsx::GetPresenceResponseT, OriginPresenceT>::HandleMessage` at `+0x32A10`.
- [x] Identify the parsed presence copy at `+0x32A9E` and handled-object queue at owner offset `+0x340`.
- [x] Add hash-locked static object-member xref tooling.
- [x] Add hash-locked static vtable resolution tooling.
- [x] Resolve handler vtable `0xABB10`: AddRef `+0x5ED0`, Release `+0x9E50`, and callback-dispatch stage `+0x2AA20`.
- [x] Identify indirect game callback sites `+0x2D1E2` and fallback `+0x2D24C`.
- [x] Validate a fresh live event at the pre-copy, post-copy, and callback-dispatch boundaries.
- [x] Confirm the live presence handler has no registered callback vector or fallback callback.
- [x] Update repeatable x64dbg breakpoint and cleanup instructions.

## Local server scaffold

- [x] Create an independently authored Python HTTP server package.
- [x] Restrict bind configuration to literal IPv4 or IPv6 loopback addresses.
- [x] Use an unprivileged default port (`42121`).
- [x] Add `/healthz` and an explicit non-emulating capabilities route.
- [x] Return generic placeholder errors without echoing paths or request bodies.
- [x] Add deterministic tests for loopback validation and route behavior.
- [ ] Package an operating-system outbound-network denial with the Windows runtime.
- [ ] Add synthetic local persistence only after the first real request schema is known.
- [ ] Package the server as a reversible Windows service or tray application after protocol validation.

## Active next trace

- [x] Break before and after the `OriginPresenceT` copy at `+0x32A9E`.
- [ ] Trace handled-object queue consumer `+0x6A70`.
- [x] Resolve lifecycle virtual calls at `+0x6B27` and `+0x6B89` statically from the captured vtable.
- [x] Determine why neither indirect callback site fires for the live presence handler.
- [x] Resolve `powdll_Win64_retail.dll`'s `StubFifaOnline` factory result to a FIFA21.exe function table.
- [x] Identify four candidate online-state readers and their object-member offsets.
- [x] Correct the expanded table map: slot `+0x108` targets FIFA21.exe `+0xA29910`; slot `+0x120` targets `+0xA29990`.
- [x] Retry Ultimate Team with the four scoped state-reader breakpoints armed; none fired before the connection error.
- [x] Classify powdll `+0x2A60` as a continuous zero-return `ActionRunning` poll rather than a retry-specific gate.
- [x] Classify the filtered `+0x3B1C` hit as PE-entrypoint/DLL thread-attach lifecycle activity.
- [x] Expand the live interface table read-only and classify selected service-chain, state, pointer, and virtual-forwarder entries.
- [x] Test the second narrow software-breakpoint set at FIFA21.exe `+0xA294B0`, `+0xA299E0`, `+0xA29A80`, and `+0xA29AD0`; none fired before the error.
- [x] Test the remaining selected pointer readers at FIFA21.exe `+0xA299A0` and `+0xA29AC0`; neither fired before the error.
- [x] Sweep all 47 non-null live `StubFifaOnline` table entries with non-pausing software-breakpoint logging; none executed before the connection error.
- [x] Reject the live `StubFifaOnline` table as the active Ultimate Team retry path for this build/session.
- [x] Trace `ntdll.dll!LdrLoadDll` across a fresh retry with non-pausing logging; no invocation of that loader boundary was observed before the error.
- [x] Reconfirm after that retry that `CardsDLL_Win64_retail.dll` remained unloaded while `powdll_Win64_retail.dll` remained loaded.
- [x] Confirm the exact `CardsDLL_Win64_retail.dll` filename is absent as ASCII and UTF-16LE text in this `FIFA21.exe` build.
- [ ] Identify the Ultimate Team menu-action callback and the controller that presents the connection error.
- [ ] Inspect the feature/module registry or indirect component identifier used before any native DLL-load request.
- [ ] Locate the upstream Ultimate Team transition/controller branch that precedes `CardsDLL_Win64_retail.dll` loading.
- [ ] Identify the state reader or dispatcher used by the connection-error path and capture its object base privately.
- [ ] Correlate one Ultimate Team retry with the resulting FIFA online-state reads using narrow software breakpoints.
- [ ] Identify the exact state field consulted by Ultimate Team entry.
- [ ] Correlate that state with the condition that loads `CardsDLL_Win64_retail.dll`.

## Native FUT routing seam

- [ ] Observe `CardsDLL_Win64_retail.dll` loading in the normal transition.
- [ ] Trace the source of `FUT/SINGLE_BASEURL_%s` and `FUT/MODULE_BASEURL_%s`.
- [ ] Prefer a native configuration or service-factory path over a binary patch.
- [ ] Confirm the selected path affects only retired FUT routing.
- [ ] Direct the first request to `127.0.0.1` without altering launcher ownership validation.
- [ ] Record the first request method, path, headers, and body schema with all values redacted.

## Minimum compatibility protocol

- [ ] Implement only the first observed request and response schema.
- [ ] Add deterministic fixtures using synthetic identifiers and blank state.
- [ ] Confirm the game process has no non-loopback FUT traffic.
- [ ] Expand one route at a time from runtime evidence.
- [ ] Create a new synthetic local user and blank club.
- [ ] Render the FUT hub with no former EA-hosted data.
- [ ] Add schema migrations, backup, restore, and rollback tests.

## Offline gameplay slice

- [ ] Implement local squad persistence.
- [ ] Complete one offline match flow.
- [ ] Persist local progression.
- [ ] Keep the store, FIFA Points, transfer market, public matchmaking, and former server content disabled.

## Definition of the next major success

The next major success is **FIFA 21 making one native, loopback-only FUT request to the local server and receiving an independently authored synthetic response**. The HTTP listener is ready for that experiment; the remaining blocker is the validated native routing seam and observed request schema.

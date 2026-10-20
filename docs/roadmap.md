# Roadmap

## Milestone 0 — immutable baseline

Status: complete for the current build.

- Hash target modules.
- Confirm the supported offline main-menu path.
- Record module loading and loopback ownership.
- Keep original files unchanged and retain a rollback copy outside the repository.

## Milestone 1 — understand the early local handshake

Status: active; parser, queue, and concrete presence handler are validated.

- [x] Break at `dbdata.dll + 0x402A0` and `+0x40417`.
- [x] Identify message type and routing structure after local frame processing.
- [x] Confirm the handoff in `dbdata.dll + 0xA0B0` through owner queue `+0x2F0`.
- [x] Validate queue consumer `+0x9FA0` and XML dispatcher `+0x6BC0`.
- [x] Resolve the live handler as `Origin::EventHandler<lsx::GetPresenceResponseT, OriginPresenceT>::HandleMessage` at `+0x32A10`.
- [x] Resolve the earlier transformed-only non-fire as traffic timing; a later live frame used that branch.
- [x] Resolve handled-object lifecycle targets through vtable `0xABB10`, including presence callback dispatch at `+0x2AA20`.
- [x] Identify the indirect SDK-to-game callback sites at `+0x2D1E2` and `+0x2D24C`.
- [x] Validate a fresh presence dispatch and prove its callback vector and fallback record are empty.
- [x] Resolve `powdll_Win64_retail.dll`'s `StubFifaOnline` factory result to a FIFA21.exe function table.
- [x] Identify candidate online-state readers at FIFA21.exe `+0xA295B0`, `+0xA29790`, `+0xA29870`, and `+0xA29910`; correct the last reader's table slot to `+0x108`.
- [x] Test all four selected readers during a fresh Ultimate Team retry; none fired before the connection error.
- [x] Separate continuous powdll `ActionRunning` polling and DLL thread-attach entrypoint activity from the retry-specific gate.
- [x] Expand the live interface table read-only and select a second non-lifecycle software-breakpoint set.
- [x] Test the second four-entry software-breakpoint set; none fired before the connection error.
- [x] Test the remaining two selected direct pointer readers; neither fired before the connection error.
- [x] Sweep all 47 non-null live `StubFifaOnline` table entries; none executed during a fresh Ultimate Team retry.
- [x] Trace `ntdll.dll!LdrLoadDll` during a fresh retry; no invocation of that loader boundary was observed before the connection error.
- [x] Reconfirm `CardsDLL_Win64_retail.dll` remains unloaded and reject the instrumented `LdrLoadDll` boundary as the point that creates the observed error.
- [x] Confirm `FIFA21.exe` contains no exact ASCII or UTF-16LE `CardsDLL_Win64_retail.dll` filename literal.
- [x] Build a CardsDLL-derived component map and identify the direct module name, plugin-export names, and service-factory cluster in `FIFA21.exe`.
- [ ] Resolve data and code references to `FIFA21.exe + 0x7073B50` and compare their owner with `PlugInitialize_` and the stub-service cluster.
- [ ] Expand the main-executable/online-plugin caller map and capture the actual reader or dispatcher used during a fresh retry.
- [ ] Identify the Ultimate Team menu callback and connection-error controller as an alternative active path after the table sweep and `LdrLoadDll` trace produced no events.
- [ ] Identify the exact FIFA online-state field and reader used by Ultimate Team entry.
- [ ] Correlate that state transition with the condition that loads `CardsDLL_Win64_retail.dll`.

Exit condition: the exact state field used by the Ultimate Team entry check is documented without modifying authentication results.

## Milestone 2 — reach the FUT plugin configuration seam

Status: pending.

- Determine the condition that loads `CardsDLL_Win64_retail.dll`.
- Trace native resolution of `FUT/SINGLE_BASEURL_%s`, `FUT/MODULE_BASEURL_%s`, and safe stub selectors.
- Prefer a documented configuration source over a binary patch.
- Do not enable skip-auth or skip-user-agent options until legal review establishes that they apply only to the independent local session.

Exit condition: either a native loopback base URL is selected or the first FUT service request is known.

## Milestone 3 — minimum loopback compatibility service

Status: scaffold started; protocol implementation is blocked on Milestone 2.

- [x] Add an independently authored HTTP server scaffold.
- [x] Bind only to literal loopback addresses on an unprivileged project port.
- [x] Reject wildcard, LAN, public, and hostname bind targets.
- [x] Add deterministic tests for network scope and placeholder responses.
- [ ] Implement only the first observed FUT request shape.
- [ ] Add an explicit outbound-network denial for the packaged runtime.
- [ ] Create a synthetic local user and blank club.
- [ ] Add deterministic fixtures, schema migrations, structured logs, and backup/restore.

Exit condition: the FUT hub renders with a brand-new local club and no EA service traffic.

## Milestone 4 — offline gameplay slice

Status: pending.

- Squad management using synthetic/local data.
- One offline match flow.
- Local progression and persistence.
- No store, FIFA Points, transfer market, public matchmaking, or former server content.

## Patch fallback

A binary edit is a last resort after configuration tracing fails. Any patcher must verify the full original SHA-256, verify expected bytes at every offset, modify only retired FUT routing, create a recoverable backup, and refuse unknown builds. It must never target activation, entitlement, anti-cheat, signatures, encryption, TLS, or certificates.

## Acceptance tests

| Test | Pass condition |
| --- | --- |
| Baseline integrity | Original module hashes match recorded values |
| Network containment | Compatibility service binds to loopback and has no outbound connections |
| Launcher separation | EA App performs only its normal lawful ownership validation |
| Fresh state | A new synthetic club is created without importing former EA data |
| Offline menu | FUT hub opens with the network physically disconnected after launch requirements are satisfied |
| Rollback | Removing project configuration restores the original offline behavior |

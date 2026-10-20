# Developer handoff

## Startup integration update — 30 August 2026

The historical investigation below predates the working local-auth launcher and
should not be used as the current startup guide. See [LocalFUT setup and upgrade
instructions](../localfut/README.md). The pack-presentation update `7963eae` also
replaced newer launcher/config/auth wiring with older copies: entrypoint arguments
no longer matched, the colliding `FIFA21-` helper name returned, and workers looked
for repo-local personal config files that are not shipped. It also re-included logs.

The startup repair restores the compatible `Project21` helper/build/worker interfaces,
per-user defaults, native local auth bootstrap and precise ignore rules. Pack assets
and content routes from that update are retained. Use the complete checkout, select
the game path on each PC, and run the portability/settings/hygiene checks documented
in the README before sharing. Neither those checks nor a successful build establish
full native FUT onboarding; test that separately. No club reset is part of this fix.
The same pack update removed the previous onboarding integration and missing-club
465 response gate. Those gameplay changes are not restored by this startup repair
and need separate review before assuming the earlier onboarding tests still apply.

## Historical investigation

Date: 25 August 2026. Branch: `trace/dbdata-handoff-20260825`. Pull request: [#1](https://github.com/myctafaa/Project-21-V2/pull/1).

## Current position

The project remains an observation-only, offline-preservation effort. FIFA 21 reaches its normal offline menu, and the independently authored compatibility server is implemented and tested but is not connected to the game. `CardsDLL_Win64_retail.dll` remains unloaded when Ultimate Team returns the connection error.

The investigation has now closed two plausible but inactive boundaries. First, all 47 non-null entries in the live `StubFifaOnline` function table were covered during a fresh retry by ordinary software breakpoints configured to log without pausing. None executed, so the table is rejected as the active retry path. Second, `ntdll.dll!LdrLoadDll` was traced in the same non-pausing manner during another retry. No invocation of that loader boundary was observed before the connection error, and `CardsDLL_Win64_retail.dll` remained unloaded.

A read-only scan also found no exact ASCII or UTF-16LE `CardsDLL_Win64_retail.dll` filename in this build's `FIFA21.exe`. The next developer should not expect a simple filename xref in the main executable.

## Validated closed routes

| Route | Result | Disposition |
| --- | --- | --- |
| `dbdata.dll` `GetPresenceResponse` callback dispatch | Handler callback vector and fallback callback are null | Do not wait on this handler for a FIFA state update |
| Selected `StubFifaOnline` state readers | Three scoped retry sets produced no hits | Rejected for observed retries |
| Complete live `StubFifaOnline` table | All 47 non-null entries produced no retry hit | Reject the table as the active path for this build/session |
| Whole-section powdll execute watch | Captured continuous `ActionRunning` polling and DLL thread-attach lifecycle activity | Do not repeat; page-guard re-entry can make FIFA appear frozen |
| `ntdll.dll!LdrLoadDll` | No invocation of the instrumented loader boundary before the connection error | Trace an earlier controller or feature-selection boundary |
| Direct main-executable filename search | No ASCII or UTF-16LE filename match | Look for an indirect module registry or component identifier |

## Recommended next investigation

Start at the **Ultimate Team menu action and connection-error controller** as an alternative active path, rather than repeating the rejected `StubFifaOnline` sweep or the same `ntdll.dll!LdrLoadDll` breakpoint. Other loader entry points or loading mechanisms have not been assessed. Use Binary Ninja to identify the menu-action callback, error-dialog construction path, or feature/module registry consulted by that callback. Place only a small number of ordinary software breakpoints at validated caller boundaries and record module-relative addresses, status values, and object-member offsets.

If static control flow is obscured, use passive Process Monitor correlation around one retry. Filter to `FIFA21.exe` and retain only sanitized file or registry path shapes associated with feature selection, plugin manifests, or configuration. A file-open attempt for `CardsDLL_Win64_retail.dll` would be useful, but none should be assumed from the current evidence.

The desired sequence is:

1. Identify the main-executable function handling the Ultimate Team selection.
2. Trace the branch that chooses the connection-error path.
3. Identify the feature/module registry or indirect component identifier checked before `LdrLoadDll`.
4. Determine the lawful native configuration or routing seam that permits normal `CardsDLL` loading.
5. Only after CardsDLL loads naturally, trace `FUT/SINGLE_BASEURL_%s` and `FUT/MODULE_BASEURL_%s` toward literal loopback.
6. Observe the first request schema before implementing any FUT protocol route.

## Runtime cleanup state

FIFA and x64dbg were responsive after the final retry. All software breakpoints were cleared with `bc`; the earlier powdll page watch had already been removed with `bpmc`. No software or memory breakpoints remained armed after cleanup. `powdll_Win64_retail.dll` remained loaded; `CardsDLL_Win64_retail.dll` remained unloaded. No game file was patched, no code was injected, and no runtime result was forced.

## Scope constraints

Continue to exclude activation, entitlement, EA ownership, anti-cheat, signatures, TLS, certificates, encryption, former EA-hosted clubs, packet payloads, account identifiers, public hosting, transfer market, matchmaking, and monetization. Publish only hashes, RVAs, sanitized shapes, independently authored tooling, and synthetic local data.

The canonical detailed records are [validated findings](findings.md), the [project checklist](checklist.md), the [debugging playbook](debugging-playbook.md), the [roadmap](roadmap.md), and the sanitized evidence files under [`docs/evidence`](evidence/).

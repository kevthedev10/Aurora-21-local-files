# Validated findings

Date: 25 August 2026. Target: FIFA 21 PC, Ultimate Team entry from the normal main menu.

## Build identity

| Module | SHA-256 |
| --- | --- |
| `FIFA21.exe` | `05CC56F897201A6BA74D608F971BCF157B1A46C10E9CDA335E868C17004FBC71` |
| `CardsDLL_Win64_retail.dll` | `37133BE975C0552C919E2901B7DA203EB458792B5BF43764821AD687DE548A49` |
| `powdll_Win64_retail.dll` | `6FA167DCA64BD78FD0E3961C253D48668961CE3D33AC8567D77E9D1FF3615885` |
| `dbdata.dll` | `92612A66F7A3658B74246A0277662DC45FDD6E5F1F24F8F6D7B1D3CDEDCD6C89` |
| `Core/Activation64.dll` | `1EFAE8EFA58C77EA05C1B8A77F564CE933A4CDED75ECC4DECB57B03101649FCF` |
| `FIFA21.exe` | `05CC56F897201A6BA74D608F971BCF157B1A46C10E9CDA335E868C17004FBC71` |

Findings are build-specific. Verify hashes before reusing an RVA.

## Runtime observations

### Main-menu behavior — validated

The game tolerates its retired-service failure, reaches the supported offline main menu, and keeps Career Mode available. Selecting Ultimate Team starts a separate online transition and returns a connection error.

During the observed transition, `FIFA21.exe` owned two established loopback connections to EA Desktop at `127.0.0.1:3216`. No remote TCP connection was owned by the game process at that observation point.

EA Desktop's local log showed fresh `SetPresence`, `GetProfile`, and `GetProfileResponse` activity during retries. Breakpoints on the first main-executable `GetProfileResponse` dispatcher and handler did not fire, proving that static branch was not the active receive path for this run.

### Module loading — validated

`CardsDLL_Win64_retail.dll` was not loaded before or during the current Ultimate Team connection failure. This makes it unlikely to own the earliest online-state gate. Its FUT URL and stub configuration strings remain candidates for the next stage after an online session object is established.

`powdll_Win64_retail.dll` was loaded. Its `StubFifaOnline` lookup occurs in plugin initialization alongside other service-factory names. The lookup is now a validated route to the main executable's online-state interface, not an established offline toggle.

### Live `dbdata.dll` receive and frame processing — validated

Observed ASLR bases include `0x7FF906D60000` and `0x7FF93B070000`. Use the RVAs below rather than a one-session absolute address.

| Role | RVA | Evidence |
| --- | ---: | --- |
| Receive-loop entry | `0x40480` | Runtime disassembly; loops while the socket at object `+0x100` is valid |
| Return after imported `recv` | `0x4052C` | Dynamic breakpoint; `RAX=0xA1` after one loopback receive |
| Available-write-size helper | `0x556B0` | Returns `[buffer+8] - [buffer+0x18]` |
| Advance/read helper | `0x556C0` | Advances the read cursor and searches for NUL |
| Current-message pointer | `0x55700` | Returns the next complete NUL-framed message pointer |
| Current-message length | `0x55720` | Returns terminator minus read cursor plus one |
| Commit received bytes | `0x55730` | Extends the write cursor and searches newly received data for NUL |
| Buffer growth/compaction | `0x55770` | Expands and compacts the receive buffer |
| Message-consumer entry | `0x402A0` | Dynamic breakpoint; live background LSX traffic hit immediately |
| Transformed-output point | `0x4039B` | Static point after transformed assignment; branch validated at the common point |
| Direct-copy output call | `0x40412` | Copies an untransformed frame to the caller-provided string |
| Common post-output point | `0x40417` | Dynamic breakpoint; both branches converge here |

The earlier receive returned 161 bytes: 160 printable hexadecimal characters followed by a NUL terminator. Values are intentionally omitted because captured payloads may contain identifiers or session material.

A later live frame hit `+0x402A0` before any new FIFA UI input. The selector at receive object `+0x108` was `1`, proving that this instance took the transformed branch; object dword `+0x10C` supplied its transform parameter. The ring buffer had a 512-byte allocation, 449 committed bytes, and 448 framed bytes at entry. The caller-provided output string was empty.

At `+0x40417`, the output was a 209-byte, NUL-free, printable UTF-8 XML document. Privacy-preserving parsing identified tags `LSX`, `Event`, and `GetPresenceResponse`, plus attribute names `sender`, `GamePresence`, `Group`, `GroupId`, `MultiplayerId`, `Presence`, `RichPresence`, `SessionId`, `Title`, `TitleId`, and `UserId`. All values were omitted. The transformed branch is therefore live; the earlier non-fire at `+0x4039B` reflected traffic timing, not a dead branch.

### Incoming queue and XML dispatcher — validated

The stack return address at `+0x402A0` was `+0xA1AC`, proving that the live frame came from the first consumer call at `+0xA1A7` inside `+0xA0B0`. `AL=1` at `+0xA1AC` confirmed successful frame production.

The owner object uses the following relevant members in the validated path:

| Owner offset | Role | Confidence |
| ---: | --- | --- |
| `+0x1B0` | Receive/session object passed to `+0x402A0` | Validated |
| `+0x218` | Synchronization object used around queue operations | Validated |
| `+0x2F0` | Incoming parsed-string queue | Validated |
| `+0x318` | Separate queue used by the drain stage | Candidate outgoing/reply queue |
| `+0x340` | Queue of handled event objects | Validated |

At `+0xA26F`, `RDX` was set to the stack-local 209-byte output string and `RCX` already addressed an empty string slot in the `+0x2F0` queue. The call at `+0xA273` resolves to generic string-copy helper `+0xBFE0`; `+0xA279` increments the queue count. Before this enqueue, the queue had capacity 8, head/index 0, and count 0. At `+0xA27D`, count was 1 and the destination slot length was 209.

Function `+0x9FA0` is the matching incoming-queue consumer. It copies the front string to its caller-provided output, calls pop helper `+0xD010`, and returns whether the output is non-empty. Runtime entry occurred within 1.2 seconds on the same owner object, and `AL=1` at `+0xA093` confirmed a successful dequeue. Continuous background LSX traffic placed another message in the queue before that breakpoint completed.

Direct xrefs show that `+0x9FA0` is called only at `+0x6C24` and `+0x6EE0` inside `+0x6BC0`. This function drains queued strings, parses XML, derives a compact handler key, resolves a handler through `+0x9A40` at `+0x6DDE`, and invokes virtual methods at `+0x6DFC` and `+0x6E07`.

For the live `GetPresenceResponse` frame, the first virtual call resolved to `dbdata.dll + 0x32A10`. A static function-name literal identifies it as `Origin::EventHandler<lsx::GetPresenceResponseT, OriginPresenceT>::HandleMessage`. The method deserializes the `Event` element, then copies the parsed `OriginPresenceT` into handler member `+0x70` through call site `+0x32A9E`. Its owner callback at `+0x32A75` resolved to the no-op `+0x529F0`, so the meaningful local state write is the member copy at `+0x32A9E`.

The follow-up virtual method resolves to `+0x63B30`, which returns `true`. Consequently, `+0x6BC0` places the handled object into owner queue `+0x340`. Function `+0x6A70` drains that queue and invokes later handler lifecycle methods.

Static resolution of the runtime-captured handler vtable at RVA `0xABB10` narrows those lifecycle calls further. Vtable slot `+0x08` resolves to `+0x5ED0`, an atomic reference increment; slot `+0x10` resolves to `+0x9E50`, the matching decrement-and-delete path. The call at `+0x6B27` is therefore an AddRef operation, and the calls at `+0x6B55` and `+0x6B98` are Release operations rather than FIFA state updates.

Vtable slot `+0x48`, called at `+0x6B89`, resolves to `+0x2AA20`. Its static behavior identifies it as the presence callback-dispatch stage. It dequeues the copied `OriginPresenceT` from the secondary handler interface at `+0x70`, converts one record through `+0x19C20`, snapshots callback records from primary-handler vector `+0x10`/`+0x18`, and invokes helper `+0x2D130`. The secondary interface begins at primary-handler offset `+0x38`; its apparent `+0x98` data member is therefore primary offset `+0xD0`, not the callback registry.

Helper `+0x2D130` can cross the SDK boundary through an indirect callback pointer at `+0x2D1E2`, or through a fallback record at primary-handler offsets `+0x28`/`+0x30` and call site `+0x2D24C`. A fresh user-generated event dynamically hit `+0x32A9E`, `+0x32AA3`, and `+0x2AA20`. Read-only inspection before and after dispatch showed that this live handler's callback-vector begin/end and fallback function/context were all null. Neither indirect call site is active for this handler, so this presence route cannot produce the FIFA state write sought in this session. Presence values remain out of scope.

The primary handler vtable at `0xABAA0` is installed by constructor `+0x118C0`; its slot `+0x20` resolves to registration method `+0x353C0`. That method passes the secondary interface at primary offset `+0x38` to helper `+0x9C10`. The sole direct caller of the constructor is `+0x343A3` inside initialization function `+0x34310`.

### `StubFifaOnline` state interface — validated boundary, candidate readers

For the recorded `powdll_Win64_retail.dll` hash, string `StubFifaOnline` is at RVA `0x5498` and is referenced at `+0x2759` inside plugin initialization function `+0x2540`. The returned factory is invoked, and its result is stored at plugin global `+0xEBF8`. Read-only runtime inspection proved that the global points to a table of functions in `FIFA21.exe`.

The sanitized slot-to-RVA observation is retained in [`docs/evidence/stub-fifa-online-table.json`](evidence/stub-fifa-online-table.json). It excludes session pointers, object addresses, payload values, and machine paths.

An expanded read-only table dump corrected one earlier slot label: FIFA21.exe `+0xA29910` is at table slot `+0x108`, not `+0x120`. Slot `+0x120` instead targets FIFA21.exe `+0xA29990`. The evidence file records both corrected mappings.

The following main-executable table entries are high-signal online-state readers. Their code and field accesses are validated; their exact role in the Ultimate Team error path remains candidate.

| Table slot | FIFA21.exe RVA | Static behavior |
| ---: | ---: | --- |
| `+0x50` | `0xA295B0` | Composite boolean: false if byte `+0x1DC6` is set; otherwise compares dwords `+0x1DB8` and `+0x1C40` |
| `+0xB0` | `0xA29790` | Returns dword at object `+0x1DC8` under the object's lock |
| `+0xD0` | `0xA29870` | Returns boolean byte at object `+0x1DC6` under the object's lock |
| `+0x108` | `0xA29910` | Returns per-index boolean at `object + 0x140 + index * 0x148` |

A user-generated Ultimate Team retry with software breakpoints on all four selected reader entries produced no hits before the connection error. These readers therefore remain valid interface members but are rejected as the active entry path for that observed retry.

A broader execute watch on `powdll_Win64_retail.dll` identified two kinds of non-gate activity. Entry `+0x2A60`, labelled `ActionRunning` by the debugger, is a three-byte zero-return stub called repeatedly from FIFA21.exe return site `+0x4BE4A55` at roughly 120 events per second. Its continuous idle activity makes it a background lifecycle poll, not a retry-specific error gate. After excluding that poll, the next pause during the retry occurred at the PE entrypoint `+0x3B1C` immediately after a new thread was created; the return was in `ntdll`, and continuation remained in the entrypoint sequence through `+0x3B2B`. This is consistent with DLL thread-attach lifecycle activity, not a validated FIFA-called state reader. The broad page watch was removed and FIFA resumed normally.

No distinct FIFA-called powdll method was validated during this retry. `CardsDLL_Win64_retail.dll` remained unloaded, so the current task is to locate the main-executable/online-plugin gate with narrower software breakpoints rather than another whole-section page watch.

A subsequent bounded, read-only expansion of the same live function table identified additional non-lifecycle candidates without executing another retry. Selected entries are: slot `+0x18` -> FIFA21.exe `+0xA294B0`, a null-checked service-chain wrapper; `+0x128` -> `+0xA299A0`, a locked pointer reader at object `+0x1DD0`; `+0x130` -> `+0xA299E0`, a locked comparison of state dword `+0x1DC0` with `0x16`; `+0x158` -> `+0xA29A80`, a locked dword reader at `+0x1DA8`; `+0x160` -> `+0xA29AC0`, an indexed pointer reader using stride `0x58` from `+0x3DE8`; and `+0x168` -> `+0xA29AD0`, a virtual slot `+0x10` forwarder. These behaviors are validated; their roles in Ultimate Team entry remain candidates.

A second user-generated retry with ordinary software breakpoints at `+0xA294B0`, `+0xA299E0`, `+0xA29A80`, and `+0xA29AD0` also produced no hits before the connection error. FIFA and x64dbg remained responsive, confirming a clean negative result rather than a debugger pause. The completed software-breakpoint set was removed.

A third scoped retry tested the remaining selected direct pointer readers at `+0xA299A0` and `+0xA29AC0`. Neither fired before the connection error, and FIFA remained responsive. All tested direct entries from the runtime `StubFifaOnline` table are therefore negative for these observed retries. The earlier whole-section freeze remains attributable to page-guard re-entry through DLL thread-attach, not evidence that the entrypoint owns the connection decision. The investigation now moves upstream to FIFA21.exe transition/controller callers and the statically identified forwarding targets.

A final table-wide retry closed this route more strongly. Forty-seven non-null entries from the live `StubFifaOnline` function table were covered with ordinary software breakpoints configured to log and never pause. The debugger log was cleared immediately before a user-generated Ultimate Team retry. No table-entry log event occurred before the connection error; only unrelated thread create/exit events were recorded. For this build and observed transition, the `StubFifaOnline` table is therefore rejected as the active Ultimate Team entry path, rather than merely having an incomplete candidate set.

### Native DLL-load boundary — validated negative result

After the table-wide negative result, a single ordinary software breakpoint was placed on `ntdll.dll!LdrLoadDll`. It logged the requested `UNICODE_STRING` buffer and caller return address while using a false break condition, so execution continued without pausing. The log was cleared immediately before another user-generated Ultimate Team retry.

No `LdrLoadDll` invocation was observed before the connection error. A read-only module query immediately afterward again confirmed that `powdll_Win64_retail.dll` was loaded and `CardsDLL_Win64_retail.dll` was not. The observed retry therefore did not reach this native loader boundary; the missing `CardsDLL` load is consistent with an earlier transition or feature-selection decision.

The sanitized observation is retained in [`docs/evidence/fut-loader-trace.json`](evidence/fut-loader-trace.json). It contains no raw debugger log, machine path, payload, session pointer, or account identifier.

A separate read-only scan of this build's `FIFA21.exe` found no exact ASCII or UTF-16LE occurrence of `CardsDLL_Win64_retail.dll`. The next investigation should not assume a direct filename xref in the main executable. It should start at the Ultimate Team menu transition and connection-error presentation, then identify the feature/module registry or controller decision that prevents the loader boundary from being reached.

### Static caller map and tooling — validated

`tools/static_function_map.py`, `tools/static_member_xrefs.py`, `tools/static_rva_xrefs.py`, `tools/static_vtable_map.py`, Capstone 5.0.6, and the PE x64 exception directory establish the following boundaries and direct references for the recorded module hashes:

| Role | Function range | Direct caller sites |
| --- | --- | --- |
| Message consumer | `0x402A0`–`0x4047B` | `0xA1A7`, `0xA29C`, both inside `0xA0B0` |
| Message-drain/enqueue stage | `0xA0B0`–`0xA440` | `0xBB4C`, inside wrapper `0xBAE0` |
| Incoming-queue consumer | `0x9FA0`–`0xA0A3` | `0x6C24`, `0x6EE0`, both inside `0x6BC0` |
| Incoming-queue pop helper | `0xD010`–`0xD07F` | `0xA03B`, inside `0x9FA0` |
| Concrete presence handler | `0x32A10`–`0x32B44` | Runtime-resolved vtable target |
| Presence callback-dispatch stage | `0x2AA20`–`0x2AC3A` | Handler vtable slot `+0x48`; called at `0x6B89` |
| Callback invocation helper | `0x2D130`–`0x2D293` | Called at `0x2AB74`; indirect callback sites `0x2D1E2`, `0x2D24C` |
| Receive loop | `0x40480`–`0x40624` | `0x4084C`, inside wrapper `0x407E0` |
| Receive wrapper | `0x407E0`–`0x40869` | Tail entry at `0x407D0` |

`tools/static_member_xrefs.py` finds exact x64 memory displacements in executable sections, reports their containing exception-directory function, and rejects an unexpected module hash. `tools/static_rva_xrefs.py` finds RIP-relative and immediate code references to selected data RVAs. `tools/static_vtable_map.py` resolves hash-locked 64-bit vtable slots to image RVAs and exception-directory function bounds. These tools corroborate the owner offsets, factory strings, globals, and lifecycle targets above without loading or modifying an EA binary.

## Static anchors retained for later stages

### Indirect component map — validated identifiers, candidate ownership

A CardsDLL-derived exact-match scan produced 115 high-signal identifiers: 77 routes, 30 configuration keys, three factory/service names, three module names, and two plugin exports. `FIFA21.exe` contains nine unique high-signal matches, including a direct `CardsDLL` module stem at RVA `0x7073B50`, `PlugInitialize_` at `0x6BFDA80`, `PlugDeinitialize_` at `0x6BFDA90`, and eight occurrences of `ut/online` beginning at `0x709530C`.

The executable also contains a service-name cluster: `StubFifaDispatchers` at `0x6C23920`, `StubFifaGameDb` at `0x6C23BB0`, and `StubFifaOnline` at `0x6C23DD8`. `powdll_Win64_retail.dll` shares the latter two names and both plugin exports. This is evidence for a native plugin/feature registry in or above the main executable, but code ownership is still a candidate until cross-references are resolved.

See [CardsDLL component map](component-map.md) for exact offsets, commands, limitations, and the next Binary Ninja targets.

`CardsDLL_Win64_retail.dll` contains high-signal configuration text including:

- `FUT/SINGLE_BASEURL_%s`
- `FUT/MODULE_BASEURL_%s`
- `FUT/SBC_USE_STUBS`
- `FUT/SKIP_AUTH_VALIDATION`
- `FUT/SKIP_USER_AGENT_CHECK`
- `StubFifaOnline`
- `ut/auth`, `ut/offline`, `ut/offline_draft`, `ut/offline_season`, and `ut/offline_tournament`

Known string RVAs for this build include base URL `0x485F70`, skip-auth-validation `0x485D18`, skip-user-agent-check `0x485F98`, local-auth header text `0x485D40`, `ut/auth` `0x483CB8`, and `StubFifaOnline` `0x418E00`. String presence does not prove a configuration is reachable or safe to enable.

`powdll_Win64_retail.dll` contains legacy endpoint strings and a `pow/auth` route. Its initialization provides the validated `StubFifaOnline` table described above, but the complete table-wide retry rejected that interface as the active Ultimate Team entry route in the observed session. The exact upstream transition decision remains unconfirmed.

## Rejected or out-of-scope paths

- DLL injection and runtime hooking.
- Patching `Activation64.dll` or its imported ordinals.
- EA App entitlement or ownership checks.
- Anti-cheat, signature, TLS, certificate, or encryption bypass.
- Main-executable resource strings that never received runtime reads.
- Recovering former EA-hosted clubs or server-side content.

# FIFA 21 Local FUT — Next-Agent Handoff

## Current objective

Diagnose why the FIFA 21 process exits after remaining idle while using the local replacement Origin-LSX service. Do not restart the investigation from the TLS redirector or certificate bridge unless new logs prove that layer is failing.

## Machine and project

- Project root: `C:\Users\ghoul\Downloads\Project-21-V2-main\Project-21-V2-main`
- Active runtime: `localfut\`
- FIFA executable: `E:\fifa\FIFA 21\FIFA21.exe`
- FIFA 21 EA App offer ID: `195853`
- Local replacement LSX endpoint: `127.0.0.1:3216`

## What is complete

1. `localfut\Launch-LocalFUT.ps1` has explicit `-LSXMode auto|ea-app|replacement` handling.
2. EA App mode detects `EADesktop.exe` on port `3216`, leaves that listener alone, and launches FIFA through EA App's registered `origin2://game/launch/` handler using offer `195853`.
3. Replacement mode requires port `3216` to be free and starts `server.py --lsx-mode required`.
4. `localfut\runtime-source\localfut21\server.py` has explicit LSX ownership modes.
5. The replacement LSX handler already performs the protocol-3 challenge and encrypted XML exchange.
6. The handler now enables TCP keepalive and removes the post-handshake 120-second receive timeout. This change needs a fresh FIFA run to validate.
7. `Stop-LocalFUT.ps1` waits for elevated cleanup and its PowerShell parse trap was fixed.
8. TLS redirector and dual FIFA 21 trust-root patching were previously validated independently. Do not change them without new evidence.

## Confirmed trace before the idle-session patch

The replacement server successfully logged:

- `Bound REQUIRED Origin-LSX on 127.0.0.1:3216`
- FIFA connected from loopback.
- Protocol 3 challenge response validated.
- `GetConfig` and `GetProfile` completed.
- `RequestLicense` was answered with `RequestLicenseResponse License=""`.
- FIFA then sent repeated `InvalidateLicense` requests.
- The client disconnected.

This proves port takeover and the basic handshake work. It does not prove that the license/session response is semantically accepted by FIFA 21.

## Next run

1. Close EA App completely so no EA process owns port `3216`.
2. Run `localfut\Launch-LocalFUT.cmd -LSXMode replacement` as Administrator.
3. Leave FIFA idle until the failure reproduces.
4. Preserve `localfut\runtime\server.log`, `server.error.log`, `redirector.log`, `trust-patch.log`, and the matching Windows application event.
5. Confirm whether `server.log` reaches `LSX session entering idle-safe receive loop` and whether the disconnect occurs near 120 seconds or after a new LSX request.
6. Stop the runtime with `localfut\Stop-LocalFUT.ps1` after the attempt.

## Decision boundary

- Disconnect near 120 seconds with no new request: inspect the idle-safe socket change and process lifetime.
- New `RequestLicense`/`InvalidateLicense` exchange immediately before disconnect: investigate the FIFA 21 response shape using authorized evidence; do not invent a license value.
- No LSX connection: fix launch path or port ownership before touching protocol responses.
- LSX remains connected but FIFA exits: inspect the FIFA crash event and the next application-layer request; the transport is no longer the leading suspect.

## Important mode rule

EA App mode and replacement mode are mutually exclusive. EA App owns `3216` in retail mode. The local server owns `3216` in replacement mode. Never run both owners together.

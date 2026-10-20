# FIFA 21 Local FUT — Testing Environment

Local (offline) Ultimate Team runtime for **FIFA 21 PC**, built on the proven
**FIFA 20 LocalFUT** trust-bridge base.

## Components

- `runtime-source/localfut21/` — local FUT/Blaze protocol emulation server
  (personal config: `%LOCALAPPDATA%\Project21\LocalFUT\config.json`).
- `runtime-source/redirector_tls.py` — TLS server that impersonates
  `spring18.gosredirector.ea.com` on port 42230 and answers
  `POST /redirector/getServerInstance` with the local Blaze endpoint
  (127.0.0.1:44321).
- `runtime-source/PATCH_RUNNING_FIFA21_TRUST.py` — in-memory trust patch for the
  running FIFA21.exe process (GOS 2015 CA modulus by default, `--all` also
  patches the GS 2019 CA modulus).
- `runtime-source/RESTORE_RUNNING_FIFA21_TRUST.py` — restores the original
  moduli in the running process.
- `runtime-source/generate_local_cert.py` — generates the local CA pair +
  `spring18` leaf certificates and the local modulus files.
- `Launch-LocalFUT.ps1` — unified launcher: starts server + redirector, launches
  the game, prepares the process-local trust/auth bridges, then installs the hosts
  redirect. The native auth helper is `Project21-LocalAuthBridge.exe`.
- `Stop-LocalFUT.ps1`, `Offline-LocalFUT.ps1` — stop/orchestration helpers.

## Quick start

1. `runtime-source\INSTALL_PREREQUISITES.cmd` (installs `cryptography`).
2. `runtime-source\GENERATE_LOCAL_CERT.cmd` (creates `certs\` + modulus files).
3. Double-click `RUN-FIFA21.cmd` from the complete repository. It builds or refreshes
   `Project21-LocalFUT-Launcher.exe` and the auth helper as needed. On first run,
   choose your installed `FIFA21.exe` in the file picker; the path is saved for
   your Windows user. **Browse** lets you change it later.
4. Click **Start LocalFUT** and approve UAC for the service worker. The launcher
   stays open with progress and logs. In EA App mode, sign into the owning account
   and use FIFA Config's Play button when instructed.

`Launch-LocalFUT.cmd` remains available for command-line troubleshooting, but
normal launches do not require opening PowerShell manually.

The launcher defaults to `lsxMode: "auto"`. It detects the owner of
`127.0.0.1:3216` before starting anything: EA App mode leaves the EA-owned LSX
listener alone; replacement mode requires the local server to own the port.
Use `Launch-LocalFUT.cmd -LSXMode ea-app` or
`Launch-LocalFUT.cmd -LSXMode replacement` to make the choice explicit.

When EA App mode is selected, the launcher starts FIFA 21 through EA App's
registered `origin2://game/launch/` handler using offer ID `195853`. Set
`launchViaEAApp` to `false` only when a direct executable launch is intentional.

### Settings and upgrading another PC

Only portable `*.example.json` settings defaults are tracked. The GUI and worker
share `%LOCALAPPDATA%\Project21\LocalFUT\launcher-settings.json`; the server creates
`config.json` there from `runtime-source\localfut21\config.example.json`, and creates
`pack_settings.json` from its built-in pack defaults. Existing personal values are
preserved. An explicit `LOCALFUT21_USER_CONFIG_ROOT` is passed through UAC, including
when a different administrator account approves it. The club database stays in
`%LOCALAPPDATA%\FIFA21LocalFUT\data` and is not reset by an update.

Keep the game path and any optional EA App path in **Settings**, not in tracked
files. Leave `sourcePath` empty to use this checkout's bundled runtime. If upgrading
from old repo-local settings, copy only the overrides you actually need into your
own AppData files; do not copy another person's installation paths or club database.

Pull/extract the complete update and run `RUN-FIFA21.cmd`; do not mix new EXEs with
old scripts from a different zip. Stop using old shortcuts to
`FIFA21-LocalFUT-Launcher.exe`. That helper name collides with FIFA Config's
`FIFA21` process-prefix check; the supported helper names start with `Project21`.
The actual game is still named `FIFA21.exe` and must not be renamed.

The GUI passes `-UserConfigRoot` and `-LauncherLogPath` to the worker, and the CMD
entrypoint passes `-IfNeeded` to the build script. Keep those interfaces together
when merging updates. The auth helper and server bootstrap contract must also be
shipped together; a listening port alone does not prove the correct server build
is running. The auth helper currently supports official build `21.0.4750997.0`;
it fails closed on mismatches, and does not supply a retail game license.

### Maintainer verification (no game launch)

Run from the repository root using Windows PowerShell:

```powershell
powershell.exe -NoProfile -File localfut/Build-FIFA21-Launcher.ps1 -IfNeeded
powershell.exe -NoProfile -File localfut/Test-StartupScriptPortability.ps1
powershell.exe -NoProfile -File localfut/Test-LauncherProcessNames.ps1
powershell.exe -NoProfile -File localfut/Test-LauncherSettings.ps1
powershell.exe -NoProfile -File localfut/Test-RepositoryHygiene.ps1
node localfut/verify-auth-bootstrap.mjs --self-test
```

The portability test can also inspect an old commit with `-SourceRevision` without
checking it out or running its scripts. `7963eae` reproduces the incompatible
launcher interfaces; its pack-presentation assets are not reverted by this fix.
These checks do not prove live FIFA login or complete native onboarding.

For an isolated runtime smoke test, run
`node localfut/verify-startup-runtime.mjs --python "C:\path\to\python.exe"`
with your actual Python 3 executable. This runs the existing server from a foreign
working directory with temporary profiles and a non-production HTTP-only port;
it checks fresh defaults, custom identity overrides, local auth and restart, then
stops only its own test child. It never starts FIFA, the launcher or live services.
Fixture data stays in the printed temporary directory for inspection.

### Read-only local FUT check

### Offline card grants

With LocalFUT running, grant every verified bundled club-customisation asset:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Grant-All-Club-Customisation.ps1
```

The operation is duplicate-safe. It puts every definition from `kits.json` in
the club, exposes every venue from `stadiums.json`, and grants the verified badge
and ball definitions. It reports vanity categories whose native FIFA 21 tables
are not bundled instead of inventing unsafe IDs. Home/away kit, badge, and ball
slots are editable in Match Day; stadium selection continues through the
existing `Stadium: ...` store entries.

With **FIFA 21 and LocalFUT stopped**, grant a known catalog card directly to
the existing local club without starting the game or any network listener:

```powershell
powershell.exe -NoProfile -File .\Grant-LocalFUTCard.ps1 -ResourceId 167963031
```

The command refuses to run if FIFA 21 or either required LocalFUT listener is
active, refuses an unknown resource ID, and is duplicate-safe by default. Use
`-Pile unassigned` to place a card in New Items, or `-AllowDuplicate` only when
you explicitly want another copy. `-ReplaceResourceId <id>` explicitly removes
non-squad copies of an obsolete card before the grant. Restart LocalFUT
afterwards to load the updated club. The 99 Neymar Jr card has resource ID
`167963031` (the player-compatible built-in Teal rarity).

With the local server running, execute:

    py -3 runtime-source\localfut21\verify_live_fut.py

The check only reads local HTTP responses defined in
`runtime-source\localfut21\test-fixtures\http_routes.json`. A complete run
reports `20/20` checks passed; it does not emulate or validate a retail FIFA
license.

For the local Blaze/Fire2 flow, execute:

    py -3 runtime-source\localfut21\verify_blaze_flow.py

This sends only synthetic frames to loopback and checks PreAuth, Ping, the
FIFA35 login response, and the local entitlement-shaped payload. It does not
use EA credentials or modify FIFA.

For one combined local-session verdict, execute:

    py -3 runtime-source\localfut21\verify_local_session.py

This runs the synthetic Blaze sequence followed by read-only shard, account,
user, squad, and club checks.

## Ports

| Port  | Service                        |
|-------|--------------------------------|
| 42230 | GOS redirector (TLS, SNI spring18.gosredirector.ea.com) |
| 44321 | Blaze                          |
| 8090  | FUT HTTP                       |
| 8081  | Legacy FUT                     |
| 42231 | Redirect (legacy)              |
| 17502 | QoS                            |
| 10853 | Local SSF bootstrap            |
| 42232 | EASW                           |
| 3216  | Origin LSX (optional)          |

## Notes

- Only our two `Project21-*.exe` helpers are tracked, with matching C# source,
  embedded icons and build scripts. They do not bundle FIFA or the Python runtime;
  the prerequisite and certificate setup above still applies.
- Logs, captures, bytecode, status/PID files, personal settings and local club
  SQLite files are ignored. Native catalogs, both mined `players.json` exports,
  portable defaults and reusable diagnostic source remain tracked. Ignoring output
  does not delete local copies or remove historical commits.

- The trust patch is memory-only: it never writes to FIFA21.exe on disk and
  disappears when the game exits.
- Do not leave the hosts redirect (`spring18.gosredirector.ea.com -> 127.0.0.1`)
  installed when the local runtime is not running.
- Never run EA App mode and replacement LSX mode at the same time. Only one
  process may own `127.0.0.1:3216`.

# FIFA 21 Local FUT — Testing Environment

Local (offline) Ultimate Team runtime for **FIFA 21 PC**, built on the proven
**FIFA 20 LocalFUT** trust-bridge base.

## Components

- `runtime-source/localfut21/` — local FUT/Blaze protocol emulation server
  (config: `config.json`).
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
  the game, patches trust, installs the hosts redirect.
- `Stop-LocalFUT.ps1`, `Offline-LocalFUT.ps1` — stop/orchestration helpers.

## Quick start

1. `runtime-source\INSTALL_PREREQUISITES.cmd` (installs `cryptography`).
2. `runtime-source\GENERATE_LOCAL_CERT.cmd` (creates `certs\` + modulus files).
3. `Launch-LocalFUT.cmd` **as Administrator** (or follow the manual order in
   `runtime-source\README.md`).

`launcher-settings.json` points to the game binary:
`E:\fifa\FIFA 21\FIFA21.exe`.

## Ports

| Port  | Service                        |
|-------|--------------------------------|
| 42230 | GOS redirector (TLS, SNI spring18.gosredirector.ea.com) |
| 44321 | Blaze                          |
| 8080  | FUT HTTP                       |
| 8081  | Legacy FUT                     |
| 42231 | Redirect (legacy)              |
| 17502 | QoS                            |
| 42232 | EASW                           |
| 3216  | Origin LSX (optional)          |

## Notes

- The trust patch is memory-only: it never writes to FIFA21.exe on disk and
  disappears when the game exits.
- Do not leave the hosts redirect (`spring18.gosredirector.ea.com -> 127.0.0.1`)
  installed when the local runtime is not running.

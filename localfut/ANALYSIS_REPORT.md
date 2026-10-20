# FIFA 21 Local FUT beta1 — read-only analysis

Analysis date: 2026-08-20.  Source inspected:
`C:\Users\hassa\Downloads\Project-21-V2-main\Project-21-V2-main\localfut\runtime-source`.

## What this project is

This is a Windows/Python local FUT test environment.  It is not a FIFA game
installation.  Its traffic path is:

```text
FIFA21.exe (running process)
  └─ temporary in-memory trust-table substitution
       └─ hosts entry: spring18.gosredirector.ea.com → 127.0.0.1
            └─ TLS redirector :42230 → XML pointing to local Blaze :44321
                 └─ FUT/Blaze Python server :44321 and FUT HTTP :8080
                      └─ SQLite state and protocol logs in %LOCALAPPDATA%\FIFA20LocalFUT
```

The trust change is process-memory-only, but it requires permission to open,
read, protect, and write the memory of the running `FIFA20.exe`.  The hosts-file
change requires an elevated administrator process.  Neither operation modifies
`FIFA21.exe` on disk.

## Observed environment and prerequisites

* `py` is available: Python 3.14.7.
* Required dependency is installed: `cryptography` 50.0.0 (the source requires
  `cryptography>=42`).
* Required ports 42230, 44321, and 8080 were free at analysis time.
* TCP 3216 was already listening on `127.0.0.1`, PID 4164.  This is not a
  startup blocker: the source marks its Origin-LSX compatibility service
  optional.
* No `FIFA21.exe` was found in the standard EA/Origin/Steam paths or the usual
  product registry keys.  A launcher must therefore be given the real game
  executable path before it can launch or patch the game.  This is a verified
  missing location, not a guessed failure.

## File-by-file inventory

| File | Contents and responsibility |
|---|---|
| `README.md` | Beta 1/2 usage notes. It documents the trust bridge, its manual test order, required ports (44321 and 8080), optional listeners, cleanup, and scope. |
| `requirements.txt` | One dependency: `cryptography>=42`. |
| `INSTALL_PREREQUISITES.cmd` | Runs `py -m pip install -r requirements.txt`. |
| `GENERATE_LOCAL_CERT.cmd` | Runs `generate_local_cert.py`. |
| `generate_local_cert.py` | Generates a 2048-bit local RSA CA with the GOS 2015 CA identity, a server certificate for `winter15` and `spring18.gosredirector.ea.com`, four PEM files, and `local-gos2015-modulus.bin`. It overwrites those generated outputs when run. |
| `certs/local-gos2015-ca-cert.pem` | Generated self-signed CA certificate. Subject/issuer are GOS 2015 Certificate Authority; valid 2026-08-19 through 2036-08-17. |
| `certs/local-gos2015-ca-key.pem` | PEM private key for that local CA. Its private material is intentionally not reproduced in this report. |
| `certs/spring18-cert.pem` | Generated TLS server certificate issued by the local CA. Its subject is `winter15.gosredirector.ea.com`; SAN includes both winter15 and spring18 redirector hostnames; valid 2026-08-19 through 2036-08-17. |
| `certs/spring18-key.pem` | PEM private key for the redirector TLS certificate. Its private material is intentionally not reproduced in this report. |
| `gos2015-original-modulus.bin` | Exactly 256 bytes: the known original 2048-bit GOS 2015 RSA public modulus used as the memory-search signature. |
| `local-gos2015-modulus.bin` | Exactly 256 bytes: the generated local CA public modulus, substituted only in the running game process. |
| `PATCH_RUNNING_FIFA21_TRUST.cmd` | Starts the Python trust-patch script. |
| `PATCH_RUNNING_FIFA21_TRUST.py` | Locates `FIFA21.exe`, opens it with VM read/write/operation access, scans committed readable memory for exact 256-byte original-modulus copies, temporarily makes matching pages writable, substitutes the local modulus, verifies writes, then restores each page protection. It pauses for input at completion. |
| `RESTORE_RUNNING_FIFA21_TRUST.cmd` | Starts the inverse trust-restore script. |
| `RESTORE_RUNNING_FIFA21_TRUST.py` | Same Windows-memory mechanics as the patcher, but finds the local modulus and restores the original modulus. It also pauses for input. |
| `INSTALL_HOSTS_REDIRECT.ps1` | Requires Administrator. Adds the exact line `127.0.0.1 spring18.gosredirector.ea.com` to the Windows hosts file if absent, then flushes DNS. |
| `REMOVE_HOSTS_REDIRECT.ps1` | Requires Administrator. Removes only lines matching that exact redirector mapping from the Windows hosts file, then flushes DNS. |
| `redirector_tls.py` | TLS 1.2 HTTP server on `0.0.0.0:42230`. Uses the generated PEM pair, logs GET/POST bodies, and returns a local Blaze endpoint XML response (`127.0.0.1:44321`). |
| `START_LOCAL_FUT21.cmd` | Opens two command windows: `localfut21/server.py`, then `redirector_tls.py`. It does not validate readiness. |
| `STOP_LOCAL_FUT21.cmd` | Only tells the user to close Python windows; deliberately does not kill Python processes. |
| `FUT20_STATUS.cmd` | Displays listening entries for project ports. |
| `CHECK_PORTS.cmd` | Displays TCP owners for project-related ports and explains how to map a PID to a process. Note: this script omits required port 8080, while the server source correctly requires it. |
| `localfut21/config.json` | Local identity/club/coin/pack defaults and ports. `server.py` forces the network topology to 44321/8080/etc. after loading this file, so those port fields are not user-overridable. |
| `localfut21/players.json` | Empty JSON object (`{}`). The server uses its embedded player database unless this file later supplies overrides. |
| `localfut21/server.py` | Main 145,250-byte Python local server. It initializes SQLite state, local cards/squads/packs/coins, FUT HTTP routes, Blaze/Fire2 responses, QoS/EASW compatibility HTTP routes, and an optional Origin-LSX handler. |

## `server.py` responsibilities in detail

The server writes mutable state outside the source folder to
`%LOCALAPPDATA%\FIFA21LocalFUT`:

* `data/localfut15.sqlite3`: credits, player items, pack state, squads, and
  related local FUT state.
* `logs/localfut15.log`: application logs.
* Additional capture files: raw/hex Fire2 packets, unknown Blaze frames,
  card-store scout JSON, and failed LSX frames, when those protocol paths occur.

Its required listeners are `127.0.0.1:44321` (Blaze TCP, with HTTP fallback)
and `127.0.0.1:8080` (FUT HTTP). Optional listeners are 8081 (legacy FUT),
42231 (cleartext redirector debug), 17502 (QoS), 42232 (EASW), and 3216
(Origin-LSX). A failed required bind closes any listeners already opened and
exits with code 2.

The FUT handler implements local authentication/shard data; account, club,
credits, squad, user, event, store, pack-purchase, unassigned-item,
quick-sell/discard, and item-pile routes; plus localization/config responses.
The Blaze handler handles Fire2/TDF bootstrap/authentication/entitlement/QoS
and FIFA-related messages, persisting diagnostic captures. The protocol values
identify a local PC FIFA 21 profile (`LocalPlayer`, `Local FC`) with configured
coins and an embedded player metadata set.

## Interaction and safety conclusions

`redirector_tls.py` and `server.py` must both be alive before the game is sent
to the local redirector. The game must be launched before the hosts redirect is
installed, according to the supplied README, because EA App startup may need
the live host. The supplied patch script needs an already-running game and may
need elevation; a launcher can report an access-denied error exactly, but it
cannot safely bypass it.

Original source hashes were recorded during analysis and the source folder was
not changed. The unified launcher created beside this report keeps its working
copy separate from the original source.
